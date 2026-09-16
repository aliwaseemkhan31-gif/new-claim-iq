"""Celery tasks for document ingestion.

Tasks are thin: they resolve the job, delegate to the runner, and decide
whether to re-dispatch. All logic lives in the service and domain layers, which
is what makes it testable without a broker.

Routed to the ``ingestion`` queue (see ``CELERY_TASK_ROUTES``) so OCR cannot
starve interactive AI requests.
"""
from __future__ import annotations

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

from claimiq.core.logging import bind_context, get_logger
from claimiq.ingestion.models import JobStatus, ProcessingJob
from claimiq.ingestion.services import runner

logger = get_logger("ingestion.tasks")

#: Stages are dispatched one per task rather than looping inside one task. Two
#: reasons: a worker yields between stages so cancellation stays responsive,
#: and a soft time limit kills at most one stage's work rather than a whole
#: document's.
MAX_CHAINED_DISPATCHES = 40


@shared_task(
    bind=True,
    name="claimiq.ingestion.tasks.process_document",
    autoretry_for=(),
    acks_late=True,
)
def process_document(self, job_id: str, dispatch_count: int = 0) -> dict:
    """Advance one job by a single stage, then re-dispatch if more remain.

    Idempotent by construction: the state machine records what is already done,
    so a redelivered task re-reads the state and continues rather than
    repeating completed work.
    """
    bind_context(job_id=job_id, task_name=self.name)

    try:
        job = ProcessingJob.objects.select_related("document_version__document").get(
            pk=job_id
        )
    except ProcessingJob.DoesNotExist:
        # The job was deleted (document removed mid-flight). Nothing to do, and
        # retrying will never succeed.
        logger.warning("ingestion.job_missing", extra={"job_id": job_id})
        return {"status": "missing"}

    if job.is_terminal:
        return {"status": job.status, "note": "already finished"}

    if job.celery_task_id != self.request.id:
        ProcessingJob.objects.filter(pk=job.pk).update(celery_task_id=self.request.id)

    try:
        stage = runner.run_next_stage(job)
    except SoftTimeLimitExceeded:
        # The stage exceeded its budget. Its checkpoint is already persisted,
        # so a retry resumes rather than restarting.
        logger.warning("ingestion.soft_time_limit", extra={"job_id": job_id})
        raise

    job.refresh_from_db()
    state = job.load_state()

    if state.next_stage() is not None and not job.is_terminal:
        if dispatch_count + 1 >= MAX_CHAINED_DISPATCHES:
            # Guard against a state machine that fails to advance. Better to
            # stop and be visibly stuck than to spin a worker indefinitely.
            logger.error(
                "ingestion.dispatch_limit_reached",
                extra={"job_id": job_id, "stage": state.describe()},
            )
            return {"status": "dispatch_limit", "state": state.describe()}
        process_document.apply_async(args=[job_id, dispatch_count + 1], queue="ingestion")

    if job.is_terminal:
        from claimiq.ingestion.services.completion import on_job_finished

        on_job_finished(job_id)

    return {
        "status": job.status,
        "stage": stage.value if stage else None,
        "progress": job.progress_percent,
        "state": state.describe(),
    }


@shared_task(name="claimiq.ingestion.tasks.start_ingestion", acks_late=True)
def start_ingestion(document_version_id: str) -> dict:
    """Create a job for a version and dispatch it."""
    from claimiq.documents.models import DocumentVersion

    bind_context(document_version_id=document_version_id)
    try:
        version = DocumentVersion.objects.select_related("document").get(
            pk=document_version_id
        )
    except DocumentVersion.DoesNotExist:
        logger.warning(
            "ingestion.version_missing", extra={"version_id": document_version_id}
        )
        return {"status": "missing"}

    job = runner.enqueue_ingestion(version)
    process_document.apply_async(args=[str(job.id)], queue="ingestion")
    return {"status": "queued", "job_id": str(job.id)}


@shared_task(name="claimiq.ingestion.tasks.retry_failed_job", acks_late=True)
def retry_failed_job(job_id: str) -> dict:
    """Retry a failed job from its failed stage."""
    try:
        job = ProcessingJob.objects.get(pk=job_id)
    except ProcessingJob.DoesNotExist:
        return {"status": "missing"}

    runner.retry_job(job)
    process_document.apply_async(args=[job_id], queue="ingestion")
    return {"status": "requeued", "job_id": job_id}


@shared_task(name="claimiq.ingestion.tasks.reap_stalled_jobs")
def reap_stalled_jobs(stale_minutes: int = 120) -> dict:
    """Re-dispatch jobs that stopped advancing.

    A worker killed by the OOM killer mid-stage leaves a job RUNNING with no
    task attached. ``acks_late`` re-queues the task in most cases, but not when
    the broker also lost it. This periodic sweep is the backstop.

    It re-dispatches rather than failing the job: every stage is idempotent and
    checkpointed, so resuming is safe and loses nothing.
    """
    from datetime import timedelta

    from django.utils import timezone

    cutoff = timezone.now() - timedelta(minutes=stale_minutes)
    stalled = ProcessingJob.objects.filter(
        status=JobStatus.RUNNING, updated_at__lt=cutoff, cancel_requested=False
    )

    requeued = 0
    for job in stalled.iterator():
        logger.warning(
            "ingestion.job_stalled",
            extra={"job_id": str(job.id), "stage": job.current_stage},
        )
        process_document.apply_async(args=[str(job.id)], queue="ingestion")
        requeued += 1

    return {"requeued": requeued, "stale_minutes": stale_minutes}
