"""Generated reports.

A report is an immutable snapshot: the data it was composed from, the composed
document, and the rendered PDF and DOCX, all frozen at generation. Regenerating
creates a new version. A report that changed when the underlying claim changed
would be useless as a record of what was known and submitted at the time.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from claimiq.core.models import BaseModel


class ReportType(models.TextChoices):
    CLAIM_ASSESSMENT = "claim_assessment", "Claim assessment"
    CLAIMS_REGISTER = "claims_register", "Claims register"


class Report(BaseModel):
    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="reports"
    )
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="reports")
    claim = models.ForeignKey(
        "claims.Claim", null=True, blank=True, on_delete=models.CASCADE, related_name="reports"
    )
    report_type = models.CharField(max_length=32, choices=ReportType.choices, db_index=True)
    title = models.CharField(max_length=512)
    version_number = models.PositiveIntegerField()

    snapshot = models.JSONField(help_text="The data the report was composed from, as it was.")
    document = models.JSONField(help_text="The composed report: sections and blocks.")

    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    pdf_file = models.FileField(upload_to="reports/%Y/%m/", blank=True)
    docx_file = models.FileField(upload_to="reports/%Y/%m/", blank=True)

    class Meta:
        db_table = "reports_report"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "-created_at"]),
            models.Index(fields=["claim", "report_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} (v{self.version_number})"
