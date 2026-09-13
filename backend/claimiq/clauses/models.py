"""Clauses, the obligations they impose, and the deadlines those create.

The requirement is that obligations be structured data, not prose buried in an
LLM response. So an obligation records WHO must do WHAT, WITHIN WHAT PERIOD,
AFTER WHAT TRIGGER, BY WHAT METHOD — each in its own column, inspectable and
correctable by a user who disagrees.

That structure is also what makes the deadline engine possible: a period and a
trigger are computable, a paragraph describing them is not.
"""
from __future__ import annotations

from django.db import models

from claimiq.accounts.models import User
from claimiq.core.models import BaseModel
from claimiq.documents.models import Document, DocumentSection
from claimiq.projects.models import Party, Project


class Clause(BaseModel):
    """A clause as it applies to a specific project.

    Distinct from a knowledge-base chunk, which is the standard form's text.
    This is the provision *governing this project*, which may be the standard
    form, or the standard form as amended by Particular Conditions. Keeping
    them apart is what lets the system answer "what does the contract say" for
    a project whose conditions have been amended, rather than reciting the
    unamended form.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="clauses")
    number = models.CharField(max_length=32, db_index=True)
    title = models.CharField(max_length=512, blank=True)
    text = models.TextField(blank=True)

    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="children"
    )
    depth = models.PositiveSmallIntegerField(default=1)

    edition_code = models.CharField(max_length=64, blank=True, db_index=True)
    is_amended = models.BooleanField(
        default=False,
        help_text="True where Particular Conditions amend the standard form.",
    )
    amended_by_document = models.ForeignKey(
        Document, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    source_section = models.ForeignKey(
        DocumentSection, null=True, blank=True, on_delete=models.SET_NULL, related_name="clauses"
    )
    source_page = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "clauses_clause"
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(fields=["project", "number"], name="uniq_clause_per_project")
        ]
        indexes = [
            models.Index(fields=["project", "number"]),
            models.Index(fields=["project", "is_amended"]),
        ]

    def __str__(self) -> str:
        return f"{self.number} {self.title}".strip()


class ObligationCategory(models.TextChoices):
    NOTICE = "notice", "Give notice"
    SUBMIT = "submit", "Submit"
    PAY = "pay", "Pay"
    DETERMINE = "determine", "Determine"
    INSTRUCT = "instruct", "Instruct"
    PERFORM = "perform", "Perform"
    CERTIFY = "certify", "Certify"
    RESPOND = "respond", "Respond"
    OTHER = "other", "Other"


class ContractualObligation(BaseModel):
    """A structured obligation extracted from a clause.

    The WHO / WHAT / WHEN / AFTER / HOW decomposition is deliberate: it is what
    turns a provision into something checkable. "The Contractor shall notify
    the Engineer within 28 days of becoming aware" becomes four queryable
    fields plus a period, and the notice-compliance engine can then compute
    against it.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="obligations")
    clause = models.ForeignKey(
        Clause, null=True, blank=True, on_delete=models.CASCADE, related_name="obligations"
    )
    clause_number = models.CharField(max_length=32, db_index=True)
    edition_code = models.CharField(max_length=64, blank=True)

    category = models.CharField(
        max_length=32, choices=ObligationCategory.choices, db_index=True
    )
    summary = models.CharField(max_length=512)

    # -- WHO must act, and to WHOM
    obligated_party_role = models.CharField(
        max_length=32,
        blank=True,
        help_text="Role as written in the contract, e.g. 'contractor', 'engineer'.",
    )
    obligated_party = models.ForeignKey(
        Party, null=True, blank=True, on_delete=models.SET_NULL, related_name="obligations"
    )
    counterparty_role = models.CharField(max_length=32, blank=True)

    # -- WHAT and HOW
    required_action = models.TextField(blank=True)
    required_content = models.TextField(
        blank=True, help_text="What the communication or submission must contain."
    )
    required_method = models.CharField(
        max_length=128, blank=True, help_text="e.g. 'in writing', 'Notice'."
    )

    # -- WHEN
    period_days = models.IntegerField(null=True, blank=True)
    period_is_working_days = models.BooleanField(default=False)
    trigger_description = models.TextField(
        blank=True, help_text="What starts the period running, in the contract's words."
    )

    is_condition_precedent = models.BooleanField(
        default=False,
        help_text=(
            "Whether compliance is a condition of entitlement. Drives severity, "
            "not the arithmetic — and differs between editions for the same "
            "obligation."
        ),
    )
    consequence_of_breach = models.TextField(blank=True)

    # -- Provenance and review (ADR 0006)
    is_ai_extracted = models.BooleanField(default=False)
    extraction_confidence = models.FloatField(null=True, blank=True)
    source_page = models.PositiveIntegerField(null=True, blank=True)
    is_confirmed = models.BooleanField(default=False)
    confirmed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "clauses_contractual_obligation"
        ordering = ["clause_number"]
        indexes = [
            models.Index(fields=["project", "category"]),
            models.Index(fields=["project", "clause_number"]),
            models.Index(fields=["project", "is_condition_precedent"]),
        ]

    def __str__(self) -> str:
        return f"{self.clause_number}: {self.summary}"

    @property
    def is_computable(self) -> bool:
        """Whether a deadline can be derived from this obligation.

        Requires a period and a trigger. An obligation with neither is still
        worth recording — it is a real duty — but no date can be computed from
        it, and the UI should not imply one.
        """
        return self.period_days is not None and bool(self.trigger_description)


class DeadlineStatus(models.TextChoices):
    UPCOMING = "upcoming", "Upcoming"
    DUE_SOON = "due_soon", "Due soon"
    OVERDUE = "overdue", "Overdue"
    MET = "met", "Met"
    MISSED = "missed", "Missed"
    NOT_APPLICABLE = "not_applicable", "Not applicable"
    INDETERMINATE = "indeterminate", "Indeterminate"
    """The trigger date is not established, so no due date exists. A
    first-class state — the alternative is inventing a start date."""


class Deadline(BaseModel):
    """A concrete date derived from an obligation and a trigger event."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="deadlines")
    obligation = models.ForeignKey(
        ContractualObligation,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="deadlines",
    )
    claim = models.ForeignKey(
        "claims.Claim", null=True, blank=True, on_delete=models.CASCADE, related_name="deadlines"
    )

    title = models.CharField(max_length=512)
    clause_number = models.CharField(max_length=32, blank=True)

    trigger_date = models.DateField(
        null=True,
        blank=True,
        help_text="When the period started. Null means not established.",
    )
    due_date = models.DateField(null=True, blank=True, db_index=True)
    satisfied_on = models.DateField(null=True, blank=True)

    status = models.CharField(
        max_length=16,
        choices=DeadlineStatus.choices,
        default=DeadlineStatus.INDETERMINATE,
        db_index=True,
    )
    is_condition_precedent = models.BooleanField(default=False)

    satisfied_by_correspondence = models.ForeignKey(
        "correspondence.Correspondence",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="satisfied_deadlines",
    )
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "clauses_deadline"
        ordering = ["due_date"]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "due_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} due {self.due_date or 'unknown'}"

    @property
    def is_actionable(self) -> bool:
        """Whether this deadline can appear on a dashboard with a real date."""
        return self.due_date is not None and self.status not in (
            DeadlineStatus.NOT_APPLICABLE,
            DeadlineStatus.INDETERMINATE,
        )
