"""Payloads for the document viewer.

Shared by project documents and knowledge-base source documents, which are
the same Document rows behind different permission checks.
"""
from __future__ import annotations

from typing import Any

from django.http import FileResponse

from claimiq.core.domain.errors import NotFoundError
from claimiq.documents.models import DocumentChunk, DocumentPage, DocumentSection
from claimiq.documents.services.rendering import ALLOWED_SCALES, file_kind_of, page_image_path
from claimiq.ingestion.domain.pipeline import STAGE_ORDER, PipelineState


def _current_version(document):
    version = document.current_version
    if version is None:
        raise NotFoundError("This document has no uploaded version.")
    return version


def page_payload(document, page_number: int) -> dict[str, Any]:
    version = _current_version(document)
    page = DocumentPage.objects.filter(version=version, page_number=page_number).first()
    if page is None and (not version.page_count or page_number > version.page_count or page_number < 1):
        raise NotFoundError(
            f"Page {page_number} does not exist.",
            details={"page_number": page_number, "page_count": version.page_count},
        )
    try:
        has_image = file_kind_of(version).has_page_images
    except (OSError, FileNotFoundError):
        has_image = False

    chunks = DocumentChunk.objects.filter(
        version=version, start_page__lte=page_number, end_page__gte=page_number
    ).order_by("sequence")

    return {
        "document_id": str(document.pk),
        "document_title": document.title,
        "version_id": str(version.pk),
        "page_number": page_number,
        "page_count": version.page_count,
        "has_image": has_image,
        "image_scales": list(ALLOWED_SCALES),
        # None when the page was not extracted — distinct from an empty page.
        "text": page.text if page else None,
        "extraction_method": page.extraction_method if page else None,
        "ocr_confidence": page.ocr_confidence if page else None,
        "is_content_page": page.is_content_page if page else None,
        "has_tables": page.has_tables if page else False,
        "processing_status": version.processing_status,
        "clauses": sorted(
            {c.clause_number for c in chunks if c.clause_number},
        ),
    }


def page_image_response(document, page_number: int, request) -> FileResponse:
    version = _current_version(document)
    try:
        scale = float(request.query_params.get("scale", "1.5"))
    except ValueError:
        scale = 1.5
    path = page_image_path(version, page_number, scale)
    response = FileResponse(open(path, "rb"), content_type="image/png")  # noqa: SIM115 - FileResponse closes it
    # Immutable: a version's file never changes after upload.
    response["Cache-Control"] = "private, max-age=86400, immutable"
    return response


def original_file_response(document) -> FileResponse:
    version = _current_version(document)
    response = FileResponse(
        version.file.open("rb"),
        content_type=version.content_type or "application/octet-stream",
        filename=version.original_filename,
        as_attachment=False,
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "sandbox"
    return response


def sections_payload(document) -> dict[str, Any]:
    version = _current_version(document)
    sections = DocumentSection.objects.filter(version=version).order_by("sequence")
    clauses = [s for s in sections if s.kind == DocumentSection.SectionKind.CLAUSE]
    tables = [s for s in sections if s.kind == DocumentSection.SectionKind.TABLE]
    return {
        "clauses": [
            {
                "id": str(s.pk),
                "parent": str(s.parent_id) if s.parent_id else None,
                "clause_number": s.clause_number,
                "title": s.title,
                "depth": s.depth,
                "page": s.start_page,
                "confidence": s.detection_confidence,
            }
            for s in clauses
        ],
        "tables": [
            {"id": str(s.pk), "title": s.title, "page": s.start_page, "text": s.text}
            for s in tables
        ],
    }


def processing_payload(document) -> dict[str, Any]:
    from claimiq.ingestion.models import ProcessingJob

    version = _current_version(document)
    job = ProcessingJob.objects.filter(document_version=version).order_by("-created_at").first()
    base = {
        "version_id": str(version.pk),
        "processing_status": version.processing_status,
        "processing_error": version.processing_error or None,
        "extraction_method": version.extraction_method,
        "extraction_quality": version.extraction_quality,
        "page_count": version.page_count,
        "job": None,
    }
    if job is None:
        return base

    state = job.load_state()
    stages = []
    for stage in STAGE_ORDER:
        outcome = state.outcome(stage)
        metrics = {
            k: v
            for k, v in (outcome.metrics or {}).items()
            # Bulky internals stay server-side.
            if k not in ("page_analysis", "headings")
        }
        stages.append(
            {
                "stage": stage.value,
                "status": outcome.status.value,
                "required": state.is_required(stage),
                "attempts": outcome.attempts,
                "error_code": outcome.error_code or None,
                "error_message": outcome.error_message or None,
                "metrics": metrics,
                "started_at": outcome.started_at.isoformat() if outcome.started_at else None,
                "completed_at": outcome.completed_at.isoformat() if outcome.completed_at else None,
            }
        )
    base["job"] = {
        "id": str(job.pk),
        "status": job.status,
        "current_stage": job.current_stage or None,
        "progress_percent": job.progress_percent,
        # Stage progress counts whole stages; OCR of a scanned set sits on one
        # number for most of the run, so the stage's own counter is reported too.
        "stage_detail": state.stage_detail(),
        "error_code": job.error_code or None,
        "error_message": job.error_message or None,
        "is_vector_searchable": _vector_searchable(state),
        "stages": stages,
        "created_at": job.created_at.isoformat(),
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }
    return base


def _vector_searchable(state: PipelineState) -> bool:
    try:
        return bool(state.is_vector_searchable)
    except Exception:  # noqa: BLE001 - a malformed legacy state is not a server error
        return False
