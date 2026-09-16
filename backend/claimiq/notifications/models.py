"""Notifications.

Each row is one message to one person about one real event. Nothing here is
generated to fill a panel: rows are written only by the handlers in
:mod:`claimiq.notifications.services`, each reacting to a state change that
happened, or to a deadline computed from recorded dates.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from claimiq.core.models import BaseModel


class NotificationCategory(models.TextChoices):
    DOCUMENT_PROCESSED = "document_processed", "Document processed"
    DOCUMENT_FAILED = "document_failed", "Document processing failed"
    KNOWLEDGE_BASE_READY = "knowledge_base_ready", "Knowledge base ready to publish"
    KNOWLEDGE_BASE_QUARANTINED = "knowledge_base_quarantined", "Knowledge base blocked by validation"
    KNOWLEDGE_BASE_FAILED = "knowledge_base_failed", "Knowledge base build failed"
    KNOWLEDGE_BASE_PUBLISHED = "knowledge_base_published", "Knowledge base published"
    ANALYSIS_COMPLETED = "analysis_completed", "Claim analysis completed"
    ANALYSIS_FAILED = "analysis_failed", "Claim analysis failed"
    DEADLINE_APPROACHING = "deadline_approaching", "Notice deadline approaching"
    DEADLINE_PASSED = "deadline_passed", "Notice deadline passed"


class NotificationSeverity(models.TextChoices):
    INFO = "info", "Information"
    SUCCESS = "success", "Success"
    WARNING = "warning", "Warning"
    DANGER = "danger", "Action required"


class Notification(BaseModel):
    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="notifications"
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    project = models.ForeignKey(
        "projects.Project", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )

    category = models.CharField(max_length=40, choices=NotificationCategory.choices, db_index=True)
    severity = models.CharField(
        max_length=16, choices=NotificationSeverity.choices, default=NotificationSeverity.INFO
    )
    title = models.CharField(max_length=255)
    message = models.TextField(blank=True)
    link = models.JSONField(
        default=dict,
        blank=True,
        help_text="Where the UI should take the reader: {name, params, query}.",
    )
    dedupe_key = models.CharField(
        max_length=255,
        help_text=(
            "Identifies the event. A redelivered task or a repeated deadline scan "
            "must not notify the same person about the same event twice."
        ),
    )
    read_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = "notifications_notification"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["recipient", "dedupe_key"], name="uniq_notification_event")
        ]
        indexes = [models.Index(fields=["recipient", "read_at", "-created_at"])]

    def __str__(self) -> str:
        return self.title
