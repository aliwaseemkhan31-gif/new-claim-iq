"""Celery application."""
from __future__ import annotations

import os

from celery import Celery
from celery.signals import setup_logging, task_postrun, task_prerun

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

app = Celery("claimiq")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@setup_logging.connect
def configure_logging(**_kwargs) -> None:
    """Use Django's LOGGING rather than Celery's own configuration.

    Without this, worker logs are plain text while web logs are JSON, and the
    two cannot be correlated.
    """
    from logging.config import dictConfig

    from django.conf import settings

    dictConfig(settings.LOGGING)


@task_prerun.connect
def bind_task_context(task_id=None, task=None, **_kwargs) -> None:
    """Put the task id into the logging context.

    A job's logs are then filterable by ``job_id``, which is what makes a failed
    ingestion diagnosable after the fact.
    """
    from claimiq.core.logging import bind_context

    bind_context(
        request_id=task_id,
        job_id=task_id,
        task_name=getattr(task, "name", None),
    )


@task_postrun.connect
def clear_task_context(**_kwargs) -> None:
    from claimiq.core.logging import clear_context

    clear_context()
