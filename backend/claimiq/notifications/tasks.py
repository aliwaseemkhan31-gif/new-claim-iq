"""Periodic notification tasks."""
from __future__ import annotations

from celery import shared_task


@shared_task(name="claimiq.notifications.tasks.scan_notice_deadlines")
def scan_notice_deadlines() -> dict:
    """Run the notice-deadline scan for every active organization."""
    from claimiq.accounts.models import Organization
    from claimiq.notifications.services.handlers import refresh_deadline_notifications

    created = 0
    for organization_id in Organization.objects.filter(is_active=True).values_list("pk", flat=True):
        created += refresh_deadline_notifications(organization_id, force=True)
    return {"created": created}
