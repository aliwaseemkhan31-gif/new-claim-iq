"""Stage executors.

Each function executes one pipeline stage against the database. The ordering,
transitions and resumability rules live in
:mod:`claimiq.ingestion.domain.pipeline`; this module holds only the work.

**Every executor must be idempotent.** Celery is configured with ``acks_late``,
so a worker crash re-queues the task and a stage may run twice on the same
version. Each executor therefore deletes-then-writes its own output rather than
appending, so a second run converges to the same result instead of duplicating
it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from django.conf import settings
from django.db import transaction

from claimiq.core.domain.errors import ExtractionError, ProcessingError
from claimiq.core.logging import get_logger
from claimiq.documents.domain.taxonomy import DEFAULT_TAXONOMY
from claimiq.documents.models import (
    DocumentChunk,
    DocumentPage,
    DocumentSection,
    DocumentVersion,
    ExtractionMethod,
)
from claimiq.ingestion.domain import chunking as chunking_domain
from claimiq.ingestion.domain import clause_detection as clause_domain
from claimiq.ingestion.domain import quality as quality_domain
from claimiq.ingestion.domain.file_safety import enforce_safety
from claimiq.ingestion.domain.pipeline import Stage
from claimiq.ingestion.providers.extraction import DocTROCRProvider, PdfPlumberExtractor

logger = get_logger("ingestion.stages")


@dataclass
class StageContext:
    """Everything a stage executor needs.

    ``checkpoint`` carries the previous attempt's progress, so a resumed stage
    can skip what it already did.
    """

    version: DocumentVersion
    checkpoint: dict[str, Any]
    cancel_requested: Callable[[], bool]
    record_checkpoint: Callable[..., None]

    @property
    def file_path(self) -> str:
        return self.version.file.path

    def check_cancelled(self) -> None:
        """Cooperative cancellation between units of work.

        There is no safe way to interrupt a stage mid-page; killing the worker
        would leave the state machine inconsistent.
        """
        if self.cancel_requested():
            from claimiq.core.domain.errors import JobCancelledError

            raise JobCancelledError("Processing was cancelled by an operator.")


# ---------------------------------------------------------------------------
# Stage implementations
# ---------------------------------------------------------------------------


def run_validate(ctx: StageContext) -> dict[str, Any]:
    """Re-check upload safety.

    Already checked at upload. Repeated here because reprocessing an existing
    version, or importing a file that bypassed the upload endpoint, must not
    skip the check — and because it is cheap.
    """
    version = ctx.version
    with open(ctx.file_path, "rb") as handle:
        header = handle.read(8192)

    from claimiq.ingestion.domain.file_safety import validate_upload

    report = validate_upload(
        filename=version.original_filename,
        declared_content_type=version.content_type,
        size_bytes=version.file_size_bytes,
        header=header,
        allowed_types=frozenset(settings.ALLOWED_UPLOAD_MIME_TYPES),
        max_bytes=settings.MAX_UPLOAD_BYTES,
    )
    enforce_safety(report)

    return {
        "risk_level": report.risk_level.value,
        "findings": len(report.findings),
        "suspicious": report.is_suspicious,
    }


def run_analyse(ctx: StageContext) -> dict[str, Any]:
    """Classify each page as digital or scanned, and detect tables."""
    analyses = PdfPlumberExtractor().analyse(ctx.file_path)

    needs_ocr = [a.page_number for a in analyses if a.needs_ocr]
    has_tables = [a.page_number for a in analyses if a.has_tables]

    version = ctx.version
    version.page_count = len(analyses)
    version.save(update_fields=["page_count", "updated_at"])

    # Persist the per-page geometry now so later stages have it even if they
    # run in a different worker process.
    ctx.record_checkpoint(
        page_analysis={
            str(a.page_number): {
                "needs_ocr": a.needs_ocr,
                "has_tables": a.has_tables,
                "chars": a.digital_character_count,
                "width": a.width,
                "height": a.height,
                "rotation": a.rotation,
            }
            for a in analyses
        }
    )

    return {
        "page_count": len(analyses),
        "pages_needing_ocr": len(needs_ocr),
        "pages_with_tables": len(has_tables),
        "requires_ocr": bool(needs_ocr),
        "requires_table_extraction": bool(has_tables),
    }


def _analysis_map(ctx: StageContext) -> dict[int, dict[str, Any]]:
    raw = ctx.checkpoint.get("page_analysis") or {}
    return {int(k): v for k, v in raw.items()}


@transaction.atomic
def run_extract_text(ctx: StageContext) -> dict[str, Any]:
    """Extract the digital text layer.

    Idempotent: pages for this version are replaced wholesale.
    """
    version = ctx.version
    analysis = _analysis_map(ctx)
    digital_pages = [n for n, a in analysis.items() if not a.get("needs_ocr")]

    extracted = PdfPlumberExtractor().extract(ctx.file_path, page_numbers=digital_pages or None)

    DocumentPage.objects.filter(version=version, extraction_method=ExtractionMethod.DIGITAL).delete()

    rows = [
        DocumentPage(
            version=version,
            page_number=page.page_number,
            text=page.text,
            width=page.width,
            height=page.height,
            rotation=analysis.get(page.page_number, {}).get("rotation", 0),
            extraction_method=ExtractionMethod.DIGITAL,
            has_tables=bool(analysis.get(page.page_number, {}).get("has_tables")),
        )
        for page in extracted
    ]
    DocumentPage.objects.bulk_create(rows, batch_size=200)

    return {"pages_extracted": len(rows), "characters": sum(len(p.text) for p in rows)}


def run_ocr(ctx: StageContext) -> dict[str, Any]:
    """OCR the scanned pages, resuming from the last checkpoint.

    Pages are committed in batches rather than in one transaction at the end,
    so a failure part-way keeps the work already done. That is the whole point
    of checkpointing: on a 500-page scan, losing 400 completed pages to a
    failure at page 401 is hours of GPU time.
    """
    version = ctx.version
    analysis = _analysis_map(ctx)
    scanned = sorted(n for n, a in analysis.items() if a.get("needs_ocr"))

    resume_from = int(ctx.checkpoint.get("last_completed_page", 0))
    remaining = [n for n in scanned if n > resume_from]

    if not remaining:
        return {"pages_ocr": 0, "resumed_from": resume_from, "note": "nothing outstanding"}

    provider = DocTROCRProvider()
    batch = 8
    processed = 0
    confidences: list[float] = []

    for offset in range(0, len(remaining), batch):
        ctx.check_cancelled()
        chunk_pages = remaining[offset : offset + batch]
        pages = provider.extract(ctx.file_path, page_numbers=chunk_pages)

        with transaction.atomic():
            DocumentPage.objects.filter(
                version=version, page_number__in=chunk_pages
            ).delete()
            DocumentPage.objects.bulk_create(
                [
                    DocumentPage(
                        version=version,
                        page_number=page.page_number,
                        text=page.text,
                        ocr_confidence=page.confidence,
                        rotation=analysis.get(page.page_number, {}).get("rotation", 0),
                        extraction_method=ExtractionMethod.OCR,
                        has_tables=bool(
                            analysis.get(page.page_number, {}).get("has_tables")
                        ),
                    )
                    for page in pages
                ]
            )

        processed += len(pages)
        confidences.extend(p.confidence for p in pages if p.confidence is not None)
        ctx.record_checkpoint(last_completed_page=chunk_pages[-1], pages_done=processed)

    version.extraction_method = (
        ExtractionMethod.HYBRID
        if len(scanned) < version.page_count
        else ExtractionMethod.OCR
    )
    version.save(update_fields=["extraction_method", "updated_at"])

    return {
        "pages_ocr": processed,
        "resumed_from": resume_from,
        "mean_confidence": (
            round(sum(confidences) / len(confidences), 3) if confidences else None
        ),
    }


@transaction.atomic
def run_classify_pages(ctx: StageContext) -> dict[str, Any]:
    """Mark contents pages, indexes and front matter as non-content."""
    version = ctx.version
    pages = list(DocumentPage.objects.filter(version=version).order_by("page_number"))
    if not pages:
        raise ExtractionError(
            "No pages were extracted, so the document cannot be classified.",
            details={"version_id": str(version.id)},
        )

    excluded = 0
    for page in pages:
        is_content = clause_domain.classify_page(
            clause_domain.PageText(page_number=page.page_number, text=page.text)
        )
        if page.is_content_page != is_content:
            page.is_content_page = is_content
            page.save(update_fields=["is_content_page", "updated_at"])
        if not is_content:
            excluded += 1

    return {"pages": len(pages), "excluded_as_front_matter": excluded}


def _content_pages(version: DocumentVersion) -> list[clause_domain.PageText]:
    return [
        clause_domain.PageText(page_number=p.page_number, text=p.text)
        for p in DocumentPage.objects.filter(
            version=version, is_content_page=True
        ).order_by("page_number")
    ]


@transaction.atomic
def run_detect_clauses(ctx: StageContext) -> dict[str, Any]:
    """Detect clause headings and cross-references.

    Skipped for document types that do not carry numbered clauses — running a
    clause detector over a photograph or a timesheet produces noise, not
    structure.
    """
    version = ctx.version
    document_type = version.document.document_type
    if document_type not in DEFAULT_TAXONOMY.codes_expecting_clauses():
        return {"skipped": True, "reason": f"{document_type} does not carry clauses"}

    pages = _content_pages(version)
    result = clause_domain.detect(pages)

    ctx.record_checkpoint(
        headings=[
            {
                "number": h.number,
                "title": h.title,
                "page": h.page_number,
                "offset": h.char_offset,
                "confidence": h.confidence,
                "depth": h.depth,
            }
            for h in result.headings
        ]
    )

    return {
        "headings": len(result.headings),
        "citations": len(result.citations),
        "roots": len(result.roots),
        "skipped_pages": len(result.skipped_pages),
    }


def run_extract_tables(ctx: StageContext) -> dict[str, Any]:
    """Table extraction.

    Not implemented. The stage exists in the pipeline and is reported honestly
    rather than silently succeeding — a stage that claims success while doing
    nothing is worse than one that admits it is pending, because downstream
    quality scoring would treat missing tables as an extraction that found none.
    """
    raise ProcessingError(
        "Table extraction is not implemented in this release.",
        details={
            "stage": Stage.EXTRACT_TABLES.value,
            "remedy": (
                "Documents with tables are processed without table structure. "
                "Disable table detection to skip this stage."
            ),
        },
    )


@transaction.atomic
def run_assemble_sections(ctx: StageContext) -> dict[str, Any]:
    """Persist the detected clause hierarchy as DocumentSection rows."""
    version = ctx.version
    stored = ctx.checkpoint.get("headings") or []
    if not stored:
        return {"sections": 0, "note": "no clause structure detected"}

    headings = [
        clause_domain.ClauseHeading(
            number=h["number"],
            title=h["title"],
            page_number=int(h["page"]),
            line_index=0,
            char_offset=int(h["offset"]),
            confidence=float(h["confidence"]),
            depth=int(h["depth"]),
        )
        for h in stored
    ]
    roots = clause_domain.build_hierarchy(headings)

    DocumentSection.objects.filter(version=version).delete()

    created: dict[str, DocumentSection] = {}
    sequence = 0

    def persist(node: clause_domain.ClauseNode, parent: DocumentSection | None) -> None:
        nonlocal sequence
        section = DocumentSection.objects.create(
            version=version,
            parent=parent,
            kind=DocumentSection.SectionKind.CLAUSE,
            clause_number=node.number,
            title=node.title,
            depth=node.depth,
            sequence=sequence,
            start_page=node.page_number,
            end_page=node.page_number,
            detection_confidence=node.confidence,
        )
        created[node.number] = section
        sequence += 1
        for child in node.children:
            persist(child, section)

    for root in roots:
        persist(root, None)

    return {"sections": len(created), "roots": len(roots)}


@transaction.atomic
def run_chunk(ctx: StageContext) -> dict[str, Any]:
    """Chunk the document, respecting clause boundaries."""
    version = ctx.version
    pages = _content_pages(version)
    if not pages:
        raise ExtractionError("No content pages available to chunk.")

    stored = ctx.checkpoint.get("headings") or []
    headings = [
        clause_domain.ClauseHeading(
            number=h["number"],
            title=h["title"],
            page_number=int(h["page"]),
            line_index=0,
            char_offset=int(h["offset"]),
            confidence=float(h["confidence"]),
            depth=int(h["depth"]),
        )
        for h in stored
    ]

    chunks = chunking_domain.chunk_document(pages, headings)

    DocumentChunk.objects.filter(version=version).delete()

    sections = {
        s.clause_number: s
        for s in DocumentSection.objects.filter(version=version)
        if s.clause_number
    }

    rows = [
        DocumentChunk(
            version=version,
            section=sections.get(chunk.clause_number or ""),
            sequence=chunk.sequence,
            text=chunk.text,
            embedding_text=chunk.embedding_text(),
            clause_number=chunk.clause_number or "",
            clause_title=chunk.clause_title or "",
            heading_path=list(chunk.heading_path),
            start_page=chunk.primary_page,
            end_page=chunk.page_numbers[-1] if chunk.page_numbers else chunk.primary_page,
            start_offset=chunk.spans[0].start_offset if chunk.spans else 0,
            end_offset=chunk.spans[0].end_offset if chunk.spans else 0,
            is_complete_clause=chunk.is_complete_clause,
            char_count=len(chunk.text),
        )
        for chunk in chunks
    ]
    DocumentChunk.objects.bulk_create(rows, batch_size=200)

    with_clause = sum(1 for c in rows if c.clause_number)
    return {
        "chunks": len(rows),
        "with_clause": with_clause,
        "complete_clauses": sum(1 for c in rows if c.is_complete_clause),
    }


def run_embed(ctx: StageContext) -> dict[str, Any]:
    """Generate embeddings.

    Not implemented: the embedding provider is Phase 3. Raised rather than
    quietly skipped so a document is never reported as fully processed while
    being invisible to vector retrieval.
    """
    raise ProcessingError(
        "Embedding generation is not implemented in this release.",
        details={
            "stage": Stage.EMBED.value,
            "remedy": (
                "Documents are extracted, structured and chunked, and are "
                "searchable lexically once indexed. Vector retrieval requires "
                "the embedding provider from Phase 3."
            ),
        },
    )


@transaction.atomic
def run_index(ctx: StageContext) -> dict[str, Any]:
    """Populate the lexical search vectors."""
    from django.contrib.postgres.search import SearchVector

    version = ctx.version
    config = "claimiq_english"

    DocumentChunk.objects.filter(version=version).update(
        search_vector=SearchVector("embedding_text", config=config)
    )
    DocumentPage.objects.filter(version=version).update(
        search_vector=SearchVector("text", config=config)
    )

    return {
        "chunks_indexed": DocumentChunk.objects.filter(version=version).count(),
        "pages_indexed": DocumentPage.objects.filter(version=version).count(),
    }


def run_validate_quality(ctx: StageContext) -> dict[str, Any]:
    """Score the extraction and record it on the version."""
    version = ctx.version
    pages = list(DocumentPage.objects.filter(version=version).order_by("page_number"))

    page_stats = [
        quality_domain.PageQuality(
            page_number=p.page_number,
            character_count=len(p.text),
            is_content_page=p.is_content_page,
            ocr_confidence=p.ocr_confidence,
            was_ocr=p.extraction_method == ExtractionMethod.OCR,
        )
        for p in pages
    ]
    texts = [p.text for p in pages if p.is_content_page]
    detected = list(
        DocumentSection.objects.filter(version=version)
        .exclude(clause_number="")
        .values_list("clause_number", flat=True)
    )

    report = quality_domain.assess_quality(
        page_stats, texts=texts, detected_clauses=detected
    )

    version.extraction_quality = report.score
    version.save(update_fields=["extraction_quality", "updated_at"])

    if report.needs_verification:
        logger.warning(
            "ingestion.low_quality_extraction",
            extra={
                "version_id": str(version.id),
                "score": report.score,
                "band": report.band.value,
            },
        )

    return {
        "score": report.score,
        "band": report.band.value,
        "findings": [f.code for f in report.findings],
        "signals": report.signals,
    }


#: Stage → executor. The pipeline runner dispatches through this; a stage
#: without an entry is a configuration error rather than a silent no-op.
STAGE_EXECUTORS: dict[Stage, Callable[[StageContext], dict[str, Any]]] = {
    Stage.VALIDATE: run_validate,
    Stage.ANALYSE: run_analyse,
    Stage.EXTRACT_TEXT: run_extract_text,
    Stage.OCR: run_ocr,
    Stage.CLASSIFY_PAGES: run_classify_pages,
    Stage.DETECT_CLAUSES: run_detect_clauses,
    Stage.EXTRACT_TABLES: run_extract_tables,
    Stage.ASSEMBLE_SECTIONS: run_assemble_sections,
    Stage.CHUNK: run_chunk,
    Stage.EMBED: run_embed,
    Stage.INDEX: run_index,
    Stage.VALIDATE_QUALITY: run_validate_quality,
}


def execute(stage: Stage, ctx: StageContext) -> tuple[dict[str, Any], float]:
    """Run one stage, returning its metrics and duration in milliseconds."""
    executor = STAGE_EXECUTORS.get(stage)
    if executor is None:
        raise ProcessingError(
            f"No executor is registered for stage {stage.value!r}.",
            details={"stage": stage.value},
        )
    started = time.perf_counter()
    metrics = executor(ctx)
    return metrics, round((time.perf_counter() - started) * 1000, 1)
