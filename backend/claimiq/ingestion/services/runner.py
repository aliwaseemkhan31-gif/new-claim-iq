"""Pipeline runner.

Drives the state machine: pick the next stage, execute it, record the outcome,
repeat. The runner owns the coupling between the domain state machine and the
database; the stages own the work; the domain owns the rules.

Capability resolution lives here because whether a stage *can* run is a
deployment fact, not a document fact.
"""
from __future__ import annotations

from typing import Any

from django.db import transaction

from claimiq.core.domain.errors import ClaimIQError, JobCancelledError, ProcessingError
from claimiq.core.logging import bind_context, get_logger
from claimiq.documents.models import DocumentVersion, ProcessingStatus
from claimiq.ingestion.domain.pipeline import PipelineState, Stage, StageStatus
from claimiq.ingestion.models import JobStatus, ProcessingJob, StageLog
from claimiq.ingestion.services import stages as stage_impl

logger = get_logger("ingestion.runner")


def embedding_provider_available() -> bool:
    """Whether this deployment can generate embeddings right now.

    Requires a configured model and a reachable runtime. When False the EMBED
    stage is skipped with its reason recorded: the document is lexically
    searchable, and a later reprocess from EMBED adds vectors.
    """
    from claimiq.ai.services.configuration import resolve_ai_settings

    ai_settings = resolve_ai_settings()
    if not ai_settings.get("DEFAULT_EMBEDDING_MODEL"):
        return False
    from claimiq.ai.providers.ollama import OllamaEmbeddingProvider

    provider = OllamaEmbeddingProvider(ai_settings["OLLAMA_BASE_URL"], 5)
    check = getattr(provider, "is_available", None)
    try:
        return bool(check()) if callable(check) else True
    except Exception:  # noqa: BLE001 - an unreachable runtime is a capability fact
        return False


def table_extraction_available() -> bool:
    """Whether table structure can be extracted (pdfplumber is installed)."""
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        return False
    return True


def _resolve_capabilities(state: PipelineState, analyse_metrics: dict[str, Any]) -> None:
    """Set the conditional-stage flags from analysis output and deployment capability.

    A document may need OCR (a document fact) but the deployment may lack an
    embedding provider (a deployment fact). Both feed the same mechanism.
    """
    state.requires_ocr = bool(analyse_metrics.get("requires_ocr"))
    state.requires_table_extraction = bool(
        analyse_metrics.get("requires_table_extraction")
    ) and table_extraction_available()
    state.embedding_available = embedding_provider_available()


def _skip_unavailable(state: PipelineState, job: ProcessingJob) -> None:
    """Record conditional stages that will not run, with the reason."""
    if not state.requires_ocr and state.status(Stage.OCR) is StageStatus.PENDING:
        state.skip(Stage.OCR, reason="no scanned pages detected")
    if (
        not state.requires_table_extraction
        and state.status(Stage.EXTRACT_TABLES) is StageStatus.PENDING
    ):
        reason = (
            "table extraction is not available in this release"
            if not table_extraction_available()
            else "no tables detected"
        )
        state.skip(Stage.EXTRACT_TABLES, reason=reason)
    if not state.embedding_available and state.status(Stage.EMBED) is StageStatus.PENDING:
        state.skip(
            Stage.EMBED,
            reason=(
                "no embedding provider is configured; the document is "
                "searchable lexically but not by vector similarity"
            ),
        )


def run_next_stage(job: ProcessingJob) -> Stage | None:
    """Execute the next outstanding stage.

    Returns the stage that ran, or None when there is nothing left to do.
    Each call handles exactly one stage so a worker yields between stages,
    which keeps cancellation responsive and stops one enormous document
    monopolising a worker slot.
    """
    job.refresh_from_db()
    state = job.load_state()

    if job.cancel_requested and not state.cancelled:
        state.cancel()
        job.save_state(state)
        _sync_version_status(job, state)
        return None

    stage = state.resume_point()
    if stage is None:
        return None

    # A stage left RUNNING by a crashed worker, or one that failed, is retried
    # from its checkpoint rather than being blocked by its own state.
    outcome = state.outcome(stage)
    if outcome.status is StageStatus.FAILED:
        state.reset_for_retry(stage)
    if state.status(stage) is StageStatus.RUNNING:
        state.outcome(stage).status = StageStatus.PENDING

    bind_context(job_id=str(job.id), stage=stage.value)

    version = job.document_version
    checkpoint = dict(outcome.checkpoint)
    accumulated: dict[str, Any] = {}

    def record_checkpoint(**values: Any) -> None:
        accumulated.update(values)
        state.checkpoint(stage, **values)
        # Persisted immediately: the point of a checkpoint is to survive the
        # crash that is about to happen.
        job.state = state.to_dict()
        job.save(update_fields=["state", "updated_at"])

    ctx = stage_impl.StageContext(
        version=version,
        checkpoint=checkpoint,
        cancel_requested=lambda: _cancel_requested(job),
        record_checkpoint=record_checkpoint,
        prior_metrics={s.value: dict(o.metrics) for s, o in state.outcomes.items()},
    )

    state.start(stage)
    job.save_state(state)
    _sync_version_status(job, state)

    attempt = state.outcome(stage).attempts

    try:
        metrics, duration_ms = stage_impl.execute(stage, ctx)
    except JobCancelledError:
        state.cancel()
        job.save_state(state)
        _sync_version_status(job, state)
        logger.info("ingestion.cancelled", extra={"stage": stage.value})
        return stage
    except ClaimIQError as exc:
        state.fail(
            stage,
            error_code=exc.code,
            error_message=exc.message,
            checkpoint={**checkpoint, **accumulated, **exc.details.get("checkpoint", {})},
        )
        job.save_state(state)
        _sync_version_status(job, state)
        _log_stage(job, stage, attempt, StageStatus.FAILED, None, exc.code, exc.details)
        logger.warning(
            "ingestion.stage_failed",
            extra={"stage": stage.value, "error_code": exc.code},
        )
        return stage
    except Exception as exc:  # noqa: BLE001 - the boundary must not leak
        # An unexpected exception is a bug. It is recorded with a generic code
        # and logged in full; the message stored for the operator never
        # contains internals.
        logger.exception(
            "ingestion.stage_crashed",
            extra={"stage": stage.value, "exception_type": type(exc).__name__},
        )
        state.fail(
            stage,
            error_code="internal_error",
            error_message=(
                "An unexpected error occurred during this stage. The failure "
                "has been logged for investigation."
            ),
            checkpoint={**checkpoint, **accumulated},
        )
        job.save_state(state)
        _sync_version_status(job, state)
        _log_stage(job, stage, attempt, StageStatus.FAILED, None, "internal_error", {})
        return stage

    if stage is Stage.ANALYSE:
        _resolve_capabilities(state, metrics)
        _skip_unavailable(state, job)

    state.complete(stage, metrics=metrics)
    job.save_state(state)
    _sync_version_status(job, state)
    _log_stage(job, stage, attempt, StageStatus.COMPLETED, duration_ms, "", metrics)

    logger.info(
        "ingestion.stage_completed",
        extra={"stage": stage.value, "duration_ms": duration_ms, **_scalar(metrics)},
    )
    return stage


def run_pipeline(job: ProcessingJob, *, max_stages: int = 50) -> PipelineState:
    """Run stages until the pipeline finishes, fails, or is cancelled.

    ``max_stages`` is a loop guard. The state machine should always advance,
    but a bug that leaves a stage neither complete nor failed would otherwise
    spin a worker indefinitely.
    """
    for _ in range(max_stages):
        if run_next_stage(job) is None:
            break
    job.refresh_from_db()
    return job.load_state()


def _cancel_requested(job: ProcessingJob) -> bool:
    return ProcessingJob.objects.filter(pk=job.pk, cancel_requested=True).exists()


def _sync_version_status(job: ProcessingJob, state: PipelineState) -> None:
    """Mirror job state onto the document version.

    The version carries the status users see on a document; the job carries the
    detail an operator needs. Keeping both avoids a join on every list view.
    """
    version = job.document_version
    if state.cancelled:
        status = ProcessingStatus.CANCELLED
    elif state.is_failed:
        status = ProcessingStatus.FAILED
    elif state.is_complete:
        status = ProcessingStatus.COMPLETED
    else:
        status = ProcessingStatus.PROCESSING

    failed = state.failed_stage
    error = state.outcome(failed).error_message if failed else ""

    updates = {"processing_status": status, "processing_error": error}
    if status == ProcessingStatus.COMPLETED:
        from django.utils import timezone

        updates["processed_at"] = timezone.now()

    DocumentVersion.objects.filter(pk=version.pk).update(**updates)


def _log_stage(
    job: ProcessingJob,
    stage: Stage,
    attempt: int,
    status: StageStatus,
    duration_ms: float | None,
    error_code: str,
    metrics: dict[str, Any],
) -> None:
    StageLog.objects.create(
        job=job,
        stage=stage.value,
        attempt=attempt,
        status=status.value,
        duration_ms=int(duration_ms) if duration_ms is not None else None,
        error_code=error_code[:64],
        metrics=_scalar(metrics),
    )


def _scalar(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep only JSON-safe scalars, so a stage cannot break logging or the DB."""
    return {
        key: value
        for key, value in payload.items()
        if isinstance(value, (str, int, float, bool, type(None)))
    }


# ---------------------------------------------------------------------------
# Job lifecycle
# ---------------------------------------------------------------------------


@transaction.atomic
def enqueue_ingestion(version: DocumentVersion, *, kind: str = "ingest") -> ProcessingJob:
    """Create a job for ``version``, or return the active one.

    A unique constraint permits only one queued/running job per version, so two
    concurrent uploads of the same document cannot duplicate every chunk. This
    returns the existing job rather than raising: asking twice for something
    already in progress is not an error.
    """
    existing = (
        ProcessingJob.objects.select_for_update()
        .filter(document_version=version, status__in=[JobStatus.QUEUED, JobStatus.RUNNING])
        .first()
    )
    if existing is not None:
        return existing

    state = PipelineState(document_version_id=str(version.id))
    job = ProcessingJob(document_version=version, kind=kind, status=JobStatus.QUEUED)
    job.state = state.to_dict()
    job.save()

    DocumentVersion.objects.filter(pk=version.pk).update(
        processing_status=ProcessingStatus.QUEUED, processing_error=""
    )
    return job


@transaction.atomic
def request_cancellation(job: ProcessingJob) -> ProcessingJob:
    """Ask a running job to stop at the next safe point."""
    if job.is_terminal:
        raise ProcessingError(
            "The job has already finished and cannot be cancelled.",
            details={"job_id": str(job.id), "status": job.status},
        )
    ProcessingJob.objects.filter(pk=job.pk).update(cancel_requested=True)
    job.refresh_from_db()
    return job


@transaction.atomic
def retry_job(job: ProcessingJob) -> ProcessingJob:
    """Clear a failure so the job can be re-dispatched.

    Retries from the failed stage using its checkpoint, not from the beginning.
    """
    state = job.load_state()
    failed = state.failed_stage
    if failed is None:
        raise ProcessingError(
            "The job has not failed and cannot be retried.",
            details={"job_id": str(job.id), "status": job.status},
        )
    state.reset_for_retry(failed)
    job.cancel_requested = False
    job.status = JobStatus.QUEUED
    job.error_code = ""
    job.error_message = ""
    job.finished_at = None
    job.save(
        update_fields=[
            "cancel_requested", "status", "error_code", "error_message",
            "finished_at", "updated_at",
        ]
    )
    job.save_state(state)
    return job


@transaction.atomic
def reprocess_from(version: DocumentVersion, stage: Stage) -> ProcessingJob:
    """Re-run the pipeline from ``stage`` onward, preserving earlier work.

    Used when configuration changes invalidate part of the output — a new
    embedding model invalidates EMBED and INDEX but not the expensive OCR.
    """
    previous = (
        ProcessingJob.objects.filter(document_version=version)
        .order_by("-created_at")
        .first()
    )
    state = previous.load_state() if previous else PipelineState(
        document_version_id=str(version.id)
    )
    state.cancelled = False
    state.reset_from(stage)

    job = ProcessingJob(
        document_version=version, kind="reprocess", status=JobStatus.QUEUED
    )
    job.state = state.to_dict()
    job.save()
    return job
