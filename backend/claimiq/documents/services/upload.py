"""Document upload.

Turns an uploaded file into a Document, a DocumentVersion and a queued
ProcessingJob, with safety validation before any of it is written.

Order matters here: validate, then hash, then store, then enqueue. Storing
first would leave a rejected executable on disk; enqueueing before the file is
committed would race the worker.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import BinaryIO

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction

from claimiq.core.domain.errors import ConflictError, NotFoundError, ValidationError
from claimiq.core.logging import get_logger
from claimiq.documents.domain.taxonomy import DEFAULT_TAXONOMY
from claimiq.documents.models import Document, DocumentVersion, ProcessingStatus
from claimiq.ingestion.domain.file_safety import SafetyReport, enforce_safety, validate_upload
from claimiq.projects.models import Project

logger = get_logger("documents.upload")

#: Read size for hashing. Streams rather than loading the file, because
#: contract sets run to hundreds of megabytes.
HASH_CHUNK_BYTES = 1024 * 1024


@dataclass
class UploadResult:
    document: Document
    version: DocumentVersion
    safety: SafetyReport
    job_id: str | None
    is_new_document: bool
    is_duplicate: bool


def compute_checksum(handle: BinaryIO) -> str:
    """SHA-256 of the file, read in chunks."""
    digest = hashlib.sha256()
    handle.seek(0)
    while True:
        block = handle.read(HASH_CHUNK_BYTES)
        if not block:
            break
        digest.update(block)
    handle.seek(0)
    return digest.hexdigest()


def _refuse_duplicate_of_another_document(project: Project, checksum: str, exclude) -> None:
    """Stop the same file being uploaded a second time as a new document.

    The existing check only looked at the document being uploaded *to*, so
    re-uploading a file under a new title sailed through — which is how one
    installation acquired the Silver Book three times and the Yellow Book
    twice, each copy separately chunked, embedded and returned in search.

    Refused rather than warned-and-written, because by the time a warning is
    read the second copy is already indexed. The caller is told exactly which
    document holds the file and may repeat the request with
    ``allow_duplicate`` when both copies are genuinely wanted.
    """
    existing = (
        DocumentVersion.objects.filter(
            document__project=project,
            checksum_sha256=checksum,
            document__deleted_at__isnull=True,
        )
        .exclude(document=exclude)
        .select_related("document")
        .order_by("created_at")
        .first()
    )
    if existing is None:
        return

    raise ConflictError(
        "This file is already in this project, uploaded as "
        f"\u201c{existing.document.title}\u201d.",
        details={
            "reason": "duplicate_of_another_document",
            "document_id": str(existing.document_id),
            "document_title": existing.document.title,
            "document_type": existing.document.document_type,
            "version_number": existing.version_number,
            "uploaded_at": existing.created_at.isoformat(),
            "remedy": (
                "Open the existing document, or upload again with "
                "allow_duplicate set if both copies are wanted."
            ),
        },
    )


def upload_document(
    *,
    project: Project,
    upload: UploadedFile,
    document_type: str,
    user,
    title: str = "",
    reference: str = "",
    document_date=None,
    replaces_document_id: str | None = None,
    allow_duplicate: bool = False,
) -> UploadResult:
    """Validate, store and queue an uploaded document.

    Args:
        project: Owning project.
        upload: The uploaded file.
        document_type: Taxonomy code.
        user: Uploading user, recorded as author.
        title: Display title. Defaults to the sanitised filename.
        reference: Project document reference.
        document_date: Date on the document itself, not the upload date.
        replaces_document_id: When set, this becomes a new version of that
            document rather than a new document.
        allow_duplicate: Proceed even though the same bytes are already in the
            project under another document. The caller has been told which one
            and has decided to keep both.

    Raises:
        ValidationError: unknown document type.
        FileSafetyError / UnsupportedMediaTypeError / PayloadTooLargeError:
            the file failed validation.
        ConflictError: byte-identical content already exists on the target
            document, or elsewhere in the project and ``allow_duplicate`` is
            not set.
    """
    if not DEFAULT_TAXONOMY.has(document_type):
        raise ValidationError(
            f"Unknown document type {document_type!r}.",
            details={"valid": [t.code for t in DEFAULT_TAXONOMY.all()]},
        )

    # Validate before anything is written. The prototype's parser opened the
    # file first; here nothing touches it until the checks pass.
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

    with transaction.atomic():
        if replaces_document_id:
            document = Document.objects.filter(
                pk=replaces_document_id, project=project
            ).first()
            if document is None:
                raise NotFoundError(
                    "The document being replaced does not exist in this project.",
                    details={"document_id": replaces_document_id},
                )
            is_new_document = False
        else:
            document = Document.objects.create(
                project=project,
                title=title.strip() or report.safe_filename,
                document_type=document_type,
                reference=reference.strip(),
                document_date=document_date,
                created_by=user,
            )
            is_new_document = True

        # Re-uploading identical bytes is almost always an accident. Refusing
        # is kinder than silently creating a second identical version, which
        # would then be indexed twice and appear twice in every result set.
        duplicate = DocumentVersion.objects.filter(
            document=document, checksum_sha256=checksum
        ).first()
        if duplicate is not None:
            raise ConflictError(
                "This exact file has already been uploaded to this document.",
                details={
                    "document_id": str(document.id),
                    "existing_version": duplicate.version_number,
                },
            )

        if is_new_document and not allow_duplicate:
            _refuse_duplicate_of_another_document(project, checksum, document)

        previous = DocumentVersion.objects.filter(document=document).order_by(
            "-version_number"
        ).first()
        next_number = (previous.version_number + 1) if previous else 1

        if previous is not None:
            DocumentVersion.objects.filter(document=document, is_superseded=False).update(
                is_superseded=True
            )

        version = DocumentVersion.objects.create(
            document=document,
            version_number=next_number,
            file=upload,
            original_filename=report.safe_filename,
            content_type=report.detected_type or upload.content_type or "",
            file_size_bytes=upload.size,
            checksum_sha256=checksum,
            processing_status=ProcessingStatus.PENDING,
            created_by=user,
            notes=(
                "Flagged during upload validation: "
                + "; ".join(f.message for f in report.findings if f.level.value == "suspicious")
                if report.is_suspicious
                else ""
            ),
        )

        document.current_version = version
        document.save(update_fields=["current_version", "updated_at"])

    # Enqueued after the transaction commits. Inside it, the worker could pick
    # the job up before the row it needs is visible.
    job_id = _enqueue(version)

    logger.info(
        "documents.uploaded",
        extra={
            "document_id": str(document.id),
            "version": version.version_number,
            "size_bytes": upload.size,
            "risk_level": report.risk_level.value,
            "job_id": job_id,
        },
    )

    return UploadResult(
        document=document,
        version=version,
        safety=report,
        job_id=job_id,
        is_new_document=is_new_document,
        is_duplicate=False,
    )


def _enqueue(version: DocumentVersion) -> str | None:
    """Queue ingestion. A broker failure must not lose the upload.

    The file is stored and the version row exists; if dispatch fails the
    document is simply unprocessed, which the stalled-job sweeper and a manual
    reprocess can both recover. Losing the upload because Redis blinked would
    be far worse.
    """
    try:
        from claimiq.ingestion.services.dispatch import dispatch_job
        from claimiq.ingestion.services.runner import enqueue_ingestion

        job = enqueue_ingestion(version)
        dispatch_job(str(job.id))
        return str(job.id)
    except Exception as exc:  # noqa: BLE001 - dispatch must not fail the upload
        logger.error(
            "documents.enqueue_failed",
            extra={
                "version_id": str(version.id),
                "exception_type": type(exc).__name__,
            },
        )
        return None
