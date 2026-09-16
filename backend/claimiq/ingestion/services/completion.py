"""What happens when an ingestion job finishes.

Other apps react to a finished job — the knowledge base materialises a
processed standard form, notifications tell the uploader. They register a
listener here in their ``AppConfig.ready`` rather than ingestion importing them,
so ingestion does not depend on the apps that depend on it.

Listeners must be idempotent: with ``acks_late`` a finished job can be reported
more than once.
"""
from __future__ import annotations

from typing import Callable

from claimiq.core.logging import get_logger

logger = get_logger("ingestion.completion")

Listener = Callable[[object], None]
_listeners: list[Listener] = []


def register(listener: Listener) -> None:
    if listener not in _listeners:
        _listeners.append(listener)


def on_job_finished(job_id: str) -> None:
    """Notify listeners that ``job_id`` reached a terminal state."""
    from claimiq.ingestion.models import ProcessingJob

    job = (
        ProcessingJob.objects.select_related("document_version__document")
        .filter(pk=job_id)
        .first()
    )
    if job is None or not job.is_terminal:
        return
    for listener in list(_listeners):
        try:
            listener(job)
        except Exception:  # noqa: BLE001 - one listener must not block the others
            logger.exception(
                "ingestion.completion_listener_failed",
                extra={"job_id": str(job_id), "listener": getattr(listener, "__name__", "?")},
            )
