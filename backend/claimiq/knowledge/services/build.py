"""Knowledge base builds: upload a standard form, process, validate, publish.

The path (docs/PHASE7.md, D2):

    upload ─► reference Document (no project) ─► ingestion pipeline
          ─► materialise chunks into the edition ─► validate + quarantine
          ─► READY ─► explicit publish ─► retrievable

Two properties this module is responsible for:

1. **Nothing is retrievable until a person publishes it.** A build that
   validates cleanly stops at READY. Retrieval reads PUBLISHED only.
2. **Contamination is quarantined, not published around.** Contents pages,
   guidance notes and page furniture are marked quarantined — kept, so a rule
   change can be re-run without re-ingesting — and the edition is judged on
   the chunks that remain.
"""
from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.postgres.search import SearchVector
from django.db import transaction
from django.utils import timezone

from claimiq.core.domain.errors import ConflictError, ValidationError
from claimiq.core.logging import get_logger
from claimiq.documents.models import (
    Document,
    DocumentChunk,
    DocumentPage,
    DocumentVersion,
    ProcessingStatus,
)
from claimiq.documents.services.upload import compute_checksum
from claimiq.ingestion.domain.file_safety import enforce_safety, validate_upload
from claimiq.knowledge.domain.editions import DEFAULT_REGISTRY
from claimiq.knowledge.domain.running_text import find_running_lines, strip_running_lines
from claimiq.knowledge.domain.validation import (
    KBChunk,
    ValidationReport,
    validate_knowledge_base,
)
from claimiq.knowledge.models import KnowledgeBase, KnowledgeBaseChunk, KnowledgeBaseStatus
from claimiq.knowledge.signals import knowledge_base_status_changed

logger = get_logger("knowledge.build")

SEARCH_CONFIG = "claimiq_english"
STANDARD_FORM_TYPE = "standard_form"

#: Rules whose chunks are quarantined. Mirrors ValidationReport.contaminated_chunk_ids.
CONTAMINATION_RULES = (
    "contents_contamination",
    "guidance_text",
    "running_header_footer",
    "malformed_chunk",
)


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


def _set_status(kb: KnowledgeBase, status: str, *, actor=None, **fields: Any) -> None:
    previous = kb.status
    kb.status = status
    for name, value in fields.items():
        setattr(kb, name, value)
    kb.save(update_fields=["status", "updated_at", *fields.keys()])
    if previous != status:
        logger.info(
            "knowledge.status_changed",
            extra={"knowledge_base_id": str(kb.pk), "from": previous, "to": status},
        )
        transaction.on_commit(
            lambda: knowledge_base_status_changed.send(
                sender=KnowledgeBase, knowledge_base=kb, previous_status=previous, actor=actor
            )
        )


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def _refuse_same_file_under_another_edition(
    organization_id, checksum: str, *, exclude_kb_id=None
) -> None:
    """Stop one file being registered as two different editions.

    The guard above already prevents a second knowledge base for the *same*
    edition. It cannot see the opposite mistake: the same PDF uploaded again
    under a different edition code — the 1999 Silver Book filed as the 2017
    one, say. That is how an installation ends up holding the Silver Book three
    times, and it is worse than clutter. Retrieval is edition-scoped precisely
    so a 2017 question is never answered from 1999 text (ADR 0004); a file
    registered under the wrong edition defeats that silently, and every answer
    it grounds is confidently wrong.

    Checked by checksum, because the file is the only thing that tells us the
    two entries are the same document.
    """
    if not checksum:
        return
    query = KnowledgeBase.objects.filter(
        organization_id=organization_id, source_checksum=checksum
    )
    if exclude_kb_id is not None:
        query = query.exclude(pk=exclude_kb_id)
    existing = query.first()
    if existing is None:
        return

    label = (
        DEFAULT_REGISTRY.get_edition(existing.edition_code).label
        if DEFAULT_REGISTRY.has_edition(existing.edition_code)
        else existing.edition_code
    )
    raise ConflictError(
        f"This exact file is already published as {label}. One file cannot be "
        f"two editions, and answers are grounded in whichever edition a "
        f"project declares — so a file filed under the wrong one produces "
        f"confident answers from the wrong contract.",
        details={
            "reason": "duplicate_source_under_another_edition",
            "knowledge_base_id": str(existing.pk),
            "edition_code": existing.edition_code,
            "edition_label": label,
            "name": existing.name,
            "remedy": (
                "Check which edition this file really is. To correct an "
                "existing entry, replace its source rather than adding another."
            ),
        },
    )


def _store_source(*, organization_id, upload, user, title: str, document: Document | None = None):
    """Validate and store the source file as a reference document version."""
    header = upload.read(8192)
    upload.seek(0)
    report = validate_upload(
        filename=upload.name or "unnamed",
        declared_content_type=upload.content_type or "application/octet-stream",
        size_bytes=upload.size,
        header=header,
        allowed_types=frozenset(settings.ALLOWED_UPLOAD_MIME_TYPES),
        max_bytes=settings.MAX_UPLOAD_BYTES,
    )
    enforce_safety(report)
    checksum = compute_checksum(upload)

    if document is None:
        document = Document.objects.create(
            project=None,
            organization_id=organization_id,
            title=title,
            document_type=STANDARD_FORM_TYPE,
            created_by=user,
        )
    elif DocumentVersion.objects.filter(document=document, checksum_sha256=checksum).exists():
        raise ConflictError(
            "This exact file has already been used as the source of this knowledge base."
        )

    previous = DocumentVersion.objects.filter(document=document).order_by("-version_number").first()
    DocumentVersion.objects.filter(document=document, is_superseded=False).update(is_superseded=True)
    version = DocumentVersion.objects.create(
        document=document,
        version_number=(previous.version_number + 1) if previous else 1,
        file=upload,
        original_filename=report.safe_filename,
        content_type=report.detected_type or upload.content_type or "",
        file_size_bytes=upload.size,
        checksum_sha256=checksum,
        processing_status=ProcessingStatus.PENDING,
        created_by=user,
    )
    document.current_version = version
    document.save(update_fields=["current_version", "updated_at"])
    return document, version, checksum


def _start_processing(version: DocumentVersion) -> None:
    from claimiq.ingestion.services.dispatch import dispatch_job
    from claimiq.ingestion.services.runner import enqueue_ingestion

    job = enqueue_ingestion(version)
    transaction.on_commit(lambda: dispatch_job(str(job.id)))


@transaction.atomic
def create_knowledge_base(
    *, organization_id, user, edition_code: str, upload, name: str = "", description: str = ""
) -> KnowledgeBase:
    """Create an edition's knowledge base from an uploaded standard form.

    Raises:
        ValidationError: the edition is not registered.
        ConflictError: the organization already has a knowledge base for the
            edition. One per edition keeps edition-scoped retrieval
            unambiguous; replace the existing one's source instead.
    """
    if not DEFAULT_REGISTRY.has_edition(edition_code):
        raise ValidationError(
            f"Unknown contract edition {edition_code!r}.",
            details={"valid": [e.code for e in DEFAULT_REGISTRY.editions()]},
        )
    edition = DEFAULT_REGISTRY.get_edition(edition_code)

    existing = KnowledgeBase.objects.filter(
        organization_id=organization_id, edition_code=edition_code
    ).first()
    if existing is not None:
        raise ConflictError(
            f"A knowledge base for {edition.label} already exists. Replace its "
            f"source document rather than creating a second one.",
            details={"knowledge_base_id": str(existing.pk)},
        )

    title = name.strip() or edition.label
    document, version, checksum = _store_source(
        organization_id=organization_id, upload=upload, user=user, title=title
    )
    _refuse_same_file_under_another_edition(organization_id, checksum)
    kb = KnowledgeBase.objects.create(
        organization_id=organization_id,
        name=title,
        edition_code=edition_code,
        form_code=edition.form_code,
        description=description.strip(),
        status=KnowledgeBaseStatus.PROCESSING,
        source_document=document,
        source_filename=version.original_filename,
        source_checksum=checksum,
        created_by=user,
    )
    _start_processing(version)
    logger.info(
        "knowledge.created",
        extra={"knowledge_base_id": str(kb.pk), "edition": edition_code},
    )
    return kb


@transaction.atomic
def replace_source(kb: KnowledgeBase, *, user, upload) -> KnowledgeBase:
    """Rebuild an edition from a new source file.

    The edition leaves retrieval until the new build is validated and
    published again: serving the old chunks while claiming a new source would
    misstate what answers are grounded in.
    """
    document, version, checksum = _store_source(
        organization_id=kb.organization_id,
        upload=upload,
        user=user,
        title=kb.name,
        document=kb.source_document,
    )
    _refuse_same_file_under_another_edition(
        kb.organization_id, checksum, exclude_kb_id=kb.pk
    )
    KnowledgeBaseChunk.objects.filter(knowledge_base=kb).delete()
    _set_status(
        kb,
        KnowledgeBaseStatus.PROCESSING,
        actor=user,
        source_document=document,
        source_filename=version.original_filename,
        source_checksum=checksum,
        chunk_count=0,
        validation_report={},
        validated_at=None,
        build_error="",
        published_at=None,
        published_by=None,
    )
    _start_processing(version)
    return kb


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def on_source_processed(job) -> None:
    """Ingestion listener: build the edition once its source finishes processing.

    Idempotent. Ignores project documents, superseded versions, and editions
    that have already moved past processing.
    """
    version = job.document_version
    version.refresh_from_db()
    document = version.document
    if document.project_id is not None:
        return

    kb = KnowledgeBase.objects.filter(source_document=document).first()
    if kb is None or document.current_version_id != version.pk:
        return
    if kb.status not in (KnowledgeBaseStatus.PROCESSING, KnowledgeBaseStatus.FAILED):
        return

    if version.processing_status != ProcessingStatus.COMPLETED:
        _set_status(
            kb,
            KnowledgeBaseStatus.FAILED,
            build_error=(
                version.processing_error
                or job.error_message
                or "Processing of the source document did not complete."
            ),
        )
        return

    build_from_source(kb)


def build_from_source(kb: KnowledgeBase) -> dict[str, Any]:
    """Materialise the processed source into edition chunks, then validate."""
    version = kb.source_document.current_version
    _set_status(kb, KnowledgeBaseStatus.VALIDATING, build_error="")

    pages = list(
        DocumentPage.objects.filter(version=version, is_content_page=True).values_list(
            "text", flat=True
        )
    )
    running = find_running_lines(pages)

    with transaction.atomic():
        KnowledgeBaseChunk.objects.filter(knowledge_base=kb).delete()
        rows: list[KnowledgeBaseChunk] = []
        for chunk in DocumentChunk.objects.filter(version=version).order_by("sequence").iterator(
            chunk_size=500
        ):
            text = strip_running_lines(chunk.text, running)
            if not text.strip():
                continue
            rows.append(
                KnowledgeBaseChunk(
                    knowledge_base=kb,
                    edition_code=kb.edition_code,
                    sequence=chunk.sequence,
                    text=text,
                    embedding_text=strip_running_lines(chunk.embedding_text or chunk.text, running),
                    clause_number=chunk.clause_number,
                    clause_title=chunk.clause_title,
                    heading_path=chunk.heading_path,
                    page_number=chunk.start_page,
                    is_complete_clause=chunk.is_complete_clause,
                    char_count=len(text),
                    # Vectors come from the pipeline's EMBED stage. They were
                    # computed before running lines were stripped; the
                    # difference is a few tokens of page furniture.
                    embedding=chunk.embedding,
                    embedding_model=chunk.embedding_model,
                    embedded_at=chunk.embedded_at,
                )
            )
        KnowledgeBaseChunk.objects.bulk_create(rows, batch_size=200)
        KnowledgeBaseChunk.objects.filter(knowledge_base=kb).update(
            search_vector=SearchVector("embedding_text", config=SEARCH_CONFIG)
        )

    return validate(kb, running_lines_removed=len(running))


def _serialise_issues(report: ValidationReport) -> list[dict[str, Any]]:
    return [
        {
            "rule": issue.rule,
            "severity": issue.severity.value,
            "message": issue.message,
            "chunk_count": len(issue.chunk_ids),
            "details": issue.details,
        }
        for issue in report.issues
    ]


def validate(kb: KnowledgeBase, *, running_lines_removed: int | None = None, actor=None) -> dict[str, Any]:
    """Validate the edition, quarantine contamination, and judge what remains.

    Two passes. The first finds contamination over every chunk and quarantines
    it. The second validates only the retained chunks: the edition is
    publishable when *they* raise no error. Without the second pass every real
    standard form — which always has a contents page — would be unpublishable.
    """
    edition = DEFAULT_REGISTRY.get_edition(kb.edition_code)
    source = str(kb.source_document_id) if kb.source_document_id else None
    rows = list(
        KnowledgeBaseChunk.objects.filter(knowledge_base=kb).values(
            "id", "text", "edition_code", "page_number", "clause_number", "embedding_model"
        )
    )

    def as_chunk(row) -> KBChunk:
        return KBChunk(
            chunk_id=str(row["id"]),
            text=row["text"],
            edition=row["edition_code"],
            page_number=row["page_number"],
            clause_number=row["clause_number"] or None,
            source_document=source,
        )

    expected = sorted({t.clause_number for t in edition.topic_map.values()})
    first = validate_knowledge_base(
        [as_chunk(r) for r in rows],
        edition=kb.edition_code,
        expected_clauses=expected,
        absent_clauses=edition.absent_clauses,
    )

    reasons: dict[str, str] = {}
    for issue in first.issues:
        if issue.rule in CONTAMINATION_RULES:
            for chunk_id in issue.chunk_ids:
                reasons.setdefault(chunk_id, issue.rule)

    with transaction.atomic():
        KnowledgeBaseChunk.objects.filter(knowledge_base=kb).update(
            is_quarantined=False, quarantine_reason=""
        )
        by_rule: dict[str, list[str]] = {}
        for chunk_id, rule in reasons.items():
            by_rule.setdefault(rule, []).append(chunk_id)
        for rule, ids in by_rule.items():
            KnowledgeBaseChunk.objects.filter(pk__in=ids).update(
                is_quarantined=True, quarantine_reason=rule
            )

    retained_rows = [r for r in rows if str(r["id"]) not in reasons]
    retained = validate_knowledge_base(
        [as_chunk(r) for r in retained_rows],
        edition=kb.edition_code,
        expected_clauses=expected,
        absent_clauses=edition.absent_clauses,
    )
    embedded = sum(1 for r in retained_rows if r["embedding_model"])
    publishable = retained.is_publishable and bool(retained_rows)

    report = {
        "is_publishable": publishable,
        "summary": retained.summary(),
        "total_chunks": len(rows),
        "quarantined_chunks": len(reasons),
        "retained_chunks": len(retained_rows),
        "embedded_chunks": embedded,
        "running_lines_removed": (
            running_lines_removed
            if running_lines_removed is not None
            else (kb.validation_report or {}).get("running_lines_removed")
        ),
        "quarantine_by_rule": {rule: len(ids) for rule, ids in by_rule.items()},
        "stats": retained.stats,
        "issues": _serialise_issues(first),
        "blocking_issues": [
            i for i in _serialise_issues(retained) if i["severity"] == "error"
        ],
        "expected_clauses": expected,
    }

    if kb.status == KnowledgeBaseStatus.PUBLISHED and publishable:
        next_status = KnowledgeBaseStatus.PUBLISHED
    else:
        next_status = KnowledgeBaseStatus.READY if publishable else KnowledgeBaseStatus.QUARANTINED

    _set_status(
        kb,
        next_status,
        actor=actor,
        validation_report=report,
        validated_at=timezone.now(),
        chunk_count=len(rows),
    )
    return report


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------


@transaction.atomic
def publish(kb: KnowledgeBase, *, user) -> KnowledgeBase:
    """Make a validated edition retrievable."""
    if kb.status == KnowledgeBaseStatus.PUBLISHED:
        return kb
    if kb.status != KnowledgeBaseStatus.READY or not (kb.validation_report or {}).get("is_publishable"):
        raise ConflictError(
            "Only a validated knowledge base with no blocking findings can be published.",
            details={"status": kb.status},
        )
    _set_status(
        kb,
        KnowledgeBaseStatus.PUBLISHED,
        actor=user,
        published_at=timezone.now(),
        published_by=user,
    )
    return kb


@transaction.atomic
def unpublish(kb: KnowledgeBase, *, user) -> KnowledgeBase:
    """Withdraw an edition from retrieval without discarding the build."""
    if kb.status != KnowledgeBaseStatus.PUBLISHED:
        raise ConflictError("The knowledge base is not published.", details={"status": kb.status})
    _set_status(kb, KnowledgeBaseStatus.READY, actor=user, published_at=None, published_by=None)
    return kb


@transaction.atomic
def delete_knowledge_base(kb: KnowledgeBase, *, user) -> None:
    """Soft-delete the edition and its source, freeing the edition for a new build."""
    if kb.source_document_id:
        kb.source_document.soft_delete(user=user)
    kb.soft_delete(user=user)
