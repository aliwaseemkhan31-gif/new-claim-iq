"""AI question history.

Every question asked through the AI workspace is kept with the exact answer
returned, including failures. A grounded answer that cannot be found again the
next day is of little use in a claims file, and "what did the system tell us
on 3 March" must be answerable.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from claimiq.core.models import BaseModel


class AIQuestionStatus(models.TextChoices):
    ANSWERED = "answered", "Answered"
    FAILED = "failed", "Failed"


class AIQuestion(BaseModel):
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="ai_questions"
    )
    asked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    question = models.TextField()
    include_knowledge_base = models.BooleanField(default=True)
    edition_code = models.CharField(max_length=64, blank=True)

    status = models.CharField(max_length=16, choices=AIQuestionStatus.choices)
    response = models.JSONField(
        default=dict,
        blank=True,
        help_text="The answer exactly as returned: summary, findings, citations, sources.",
    )
    model = models.CharField(max_length=128, blank=True)
    prompt_identifier = models.CharField(max_length=128, blank=True)
    latency_ms = models.FloatField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        db_table = "ai_question"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["project", "-created_at"])]

    def __str__(self) -> str:
        return self.question[:80]
