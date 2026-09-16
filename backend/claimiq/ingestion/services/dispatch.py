"""Start or resume an ingestion job without blocking the caller.

With a broker, a job goes to the ``ingestion`` queue and advances one stage per
task. Without one (local development runs Celery eagerly), dispatching the task
would run every stage inside the upload request — an hour of OCR for a scanned
contract, killed by the development server's autoreload. A daemon thread runs
the pipeline instead. Every stage is idempotent and checkpointed, so a thread
killed mid-stage leaves a job that resumes where it stopped.
"""
from __future__ import annotations

import threading

from django.conf import settings
from django.db import close_old_connections

from claimiq.core.logging import get_logger

logger = get_logger("ingestion.dispatch")

_running: set[str] = set()
_lock = threading.Lock()


def _run_in_thread(job_id: str) -> None:
    from claimiq.ingestion.models import ProcessingJob
    from claimiq.ingestion.services.runner import run_pipeline

    close_old_connections()
    try:
        job = ProcessingJob.objects.select_related("document_version__document").get(pk=job_id)
        run_pipeline(job, max_stages=60)
        from claimiq.ingestion.services.completion import on_job_finished

        on_job_finished(job_id)
    except Exception:  # noqa: BLE001 - a thread has no caller to report to
        logger.exception("ingestion.thread_crashed", extra={"job_id": job_id})
    finally:
        with _lock:
            _running.discard(job_id)
        close_old_connections()


def is_running_locally(job_id: str) -> bool:
    with _lock:
        return str(job_id) in _running


def dispatch_job(job_id: str) -> None:
    """Advance ``job_id`` to completion in the background."""
    job_id = str(job_id)
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        with _lock:
            if job_id in _running:
                return
            _running.add(job_id)
        threading.Thread(
            target=_run_in_thread, args=(job_id,), name=f"ingest-{job_id}", daemon=True
        ).start()
        return

    from claimiq.ingestion.tasks import process_document

    process_document.apply_async(args=[job_id], queue="ingestion")
