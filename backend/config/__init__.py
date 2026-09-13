"""ClaimIQ Enterprise Django project configuration."""
from __future__ import annotations

# Ensure the Celery app is created when Django starts, so shared_task
# registration works regardless of import order.
from config.celery import app as celery_app

__all__ = ("celery_app",)
