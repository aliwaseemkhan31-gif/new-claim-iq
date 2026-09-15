"""Claim analysis runs, their strand results, AI findings and human reviews.

The shape follows two rules.

**Reproducibility.** A run records the claim as it stood when the run started
(``claim_snapshot``), the model, the edition, and for each strand the prompt
version, retrieval query, retrieval trace and grounding outcome. An analysis
read months later — possibly in a dispute about the analysis — can be traced to
exactly what produced it, even if the claim has since been edited.

**AI output is never overwritten (ADR 0006).** Findings are immutable once
written. A person's decision is a separate, append-only review row with its own
author, timestamp and reason. The difference between what the AI concluded and
what a professional concluded stays visible for the life of the record.
"""
from __future__ import annotations

from django.db import models

from claimiq.accounts.models import User
from claimiq.analysis.domain.review import ReviewAction
from claimiq.analysis.domain.strands import STRAND_LABELS, RunStatus, Strand, StrandStatus
from claimiq.claims.models import Claim
from claimiq.core.domain.errors import ConflictError
from claimiq.core.models import BaseModel
from claimiq.projects.models import Project

RUN_STATUS_CHOICES = [(s.value, s.value.replace("_", " ").title()) for s in RunStatus]
STRAND_CHOICES = [(s.value, STRAND_LABELS[s]) for s in Strand]
STRAND_STATUS_CHOICES = [(s.value, s.value.title()) for s in StrandStatus]
CONFIDENCE_CHOICES = [("high", "High"), ("medium", "Medium"), ("low", "Low")]
EPISTEMIC_CHOICES = [
    ("fact", "Fact"),
    ("inference", "Inference"),
    ("opinion", "Opinion"),
    ("unknown", "Unknown"),
]
REVIEW_ACTION_CHOICES = [(a.value, a.value.title()) for a in ReviewAction]

ACTIVE_RUN_STATUSES = [RunStatus.QUEUED.value, RunStatus.RUNNING.value]


class ClaimAnalysis(BaseModel):
    """One run of the claim analysis engine over one claim."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="analyses")
    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="analyses")

    status = models.CharField(
        max_length=16,
        choices=RUN_STATUS_CHOICES,
        default=RunStatus.QUEUED.value,
        db_index=True,
    )
    progress_percent = models.PositiveSmallIntegerField(default=0)

    requested_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    edition_code = models.CharField(max_length=64)
    llm_model = models.CharField(max_length=128, blank=True)
    embedding_model = models.CharField(max_length=128, blank=True)

    claim_snapshot = models.JSONField(
        default=dict,
        help_text=(
            "The claim as recorded when the run started. The analysis is of "
            "these facts, not of whatever the claim says today."
        ),
    )

    cancel_requested = models.BooleanField(default=False)
    celery_task_id = models.CharField(max_length=255, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(
        blank=True,
        help_text="Operator-facing. Never shown as, or mistaken for, an analysis finding.",
    )

    class Meta:
        db_table = "analysis_claim_analysis"
        ordering = ["-created_at"]
        constraints = [
            # One active run per claim. Two concurrent runs would double the
            # model calls and leave two contradictory "latest" analyses.
            models.UniqueConstraint(
                fields=["claim"],
                condition=models.Q(status__in=ACTIVE_RUN_STATUSES),
                name="uniq_active_analysis_per_claim",
            )
        ]
        indexes = [
            models.Index(fields=["claim", "-created_at"]),
            models.Index(fields=["project", "status"]),
        ]

    def __str__(self) -> str:
        return f"Analysis of {self.claim_id} ({self.status})"

    @property
    def is_terminal(self) -> bool:
        return self.status not in ACTIVE_RUN_STATUSES


class AnalysisStrandResult(BaseModel):
    """The outcome of one strand within a run."""

    analysis = models.ForeignKey(
        ClaimAnalysis, on_delete=models.CASCADE, related_name="strands"
    )
    strand = models.CharField(max_length=32, choices=STRAND_CHOICES)
    sequence = models.PositiveSmallIntegerField(default=0)

    status = models.CharField(
        max_length=16, choices=STRAND_STATUS_CHOICES, default=StrandStatus.PENDING.value
    )
    uses_model = models.BooleanField(default=False)
    skip_reason = models.TextField(blank=True)

    summary = models.TextField(blank=True)
    insufficient_evidence = models.BooleanField(default=False)
    confidence = models.CharField(max_length=8, choices=CONFIDENCE_CHOICES, blank=True)
    confidence_reasons = models.JSONField(
        default=list,
        blank=True,
        help_text="Why the confidence is what it is. Computed, not self-reported.",
    )

    computed = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Deterministic output for computed strands — notice timing, the "
            "evidence gap report. Kept even when the model step fails."
        ),
    )

    prompt_identifier = models.CharField(max_length=128, blank=True)
    retrieval_query = models.TextField(blank=True)
    observability = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Model, prompt version, retrieval trace, sources, citations and "
            "grounding outcome. No chain-of-thought is stored."
        ),
    )

    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "analysis_strand_result"
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["analysis", "strand"], name="uniq_strand_per_analysis"
            )
        ]

    def __str__(self) -> str:
        return f"{self.strand} ({self.status})"


class AIFinding(BaseModel):
    """One grounded assertion produced by a model strand.

    Immutable once written: :meth:`save` refuses to update an existing row. A
    person who disagrees records a :class:`FindingReview`.
    """

    analysis = models.ForeignKey(
        ClaimAnalysis, on_delete=models.CASCADE, related_name="findings"
    )
    strand_result = models.ForeignKey(
        AnalysisStrandResult, on_delete=models.CASCADE, related_name="findings"
    )
    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="ai_findings")

    sequence = models.PositiveSmallIntegerField(default=0)
    statement = models.TextField()
    epistemic_status = models.CharField(max_length=16, choices=EPISTEMIC_CHOICES)
    citations = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Resolved citations: source ref, quotation, and the document, page, "
            "clause and edition each resolves to."
        ),
    )

    class Meta:
        db_table = "analysis_ai_finding"
        ordering = ["strand_result__sequence", "sequence"]
        indexes = [
            models.Index(fields=["claim", "epistemic_status"]),
            models.Index(fields=["analysis", "sequence"]),
        ]

    def __str__(self) -> str:
        return self.statement[:80]

    def save(self, *args, **kwargs) -> None:
        if not self._state.adding:
            raise ConflictError(
                "AI findings are immutable. Record a review instead of editing the finding.",
                details={"finding_id": str(self.pk)},
            )
        super().save(*args, **kwargs)


class FindingReview(BaseModel):
    """A person's decision on an AI finding. Append-only."""

    finding = models.ForeignKey(AIFinding, on_delete=models.CASCADE, related_name="reviews")
    action = models.CharField(max_length=16, choices=REVIEW_ACTION_CHOICES)
    reason = models.TextField(blank=True)
    amended_statement = models.TextField(blank=True)

    reviewer = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="finding_reviews"
    )
    reviewer_email = models.EmailField(
        help_text=(
            "Recorded at review time. The review keeps its author even if the "
            "account is later removed."
        ),
    )

    class Meta:
        db_table = "analysis_finding_review"
        ordering = ["created_at"]
        indexes = [models.Index(fields=["finding", "created_at"])]

    def __str__(self) -> str:
        return f"{self.action} by {self.reviewer_email}"

    def save(self, *args, **kwargs) -> None:
        if not self._state.adding:
            raise ConflictError(
                "Reviews are append-only. Record a new review instead of editing one.",
                details={"review_id": str(self.pk)},
            )
        super().save(*args, **kwargs)
