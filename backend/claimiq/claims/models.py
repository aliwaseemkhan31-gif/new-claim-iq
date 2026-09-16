"""Claims, their constituent parts, and evidence.

A claim is not a document. The prototype treated it as one — upload a claim
PDF, extract its text, ask questions of it — which makes the claim only as
structured as its prose. Here a claim is an entity with dates, parties, a
contractual basis, events, positions and linked evidence, so "which claims are
time-barred" and "what evidence is missing" become queries rather than reading
exercises.

The human-in-the-loop rule from ADR 0006 shows up in the column layout: AI
output and human assessment are separate, separately attributed fields, and AI
output is never overwritten.
"""
from __future__ import annotations

from django.db import models

from claimiq.accounts.models import User
from claimiq.core.models import BaseModel, BaseSoftDeleteModel
from claimiq.documents.models import Document
from claimiq.projects.models import Party, Project


class ClaimType(models.TextChoices):
    EXTENSION_OF_TIME = "eot", "Extension of Time"
    VARIATION = "variation", "Variation"
    COST = "cost", "Additional Cost"
    DELAY = "delay", "Delay"
    DISRUPTION = "disruption", "Disruption"
    ACCELERATION = "acceleration", "Acceleration"
    PAYMENT = "payment", "Payment"
    COMPENSATION_EVENT = "compensation_event", "Compensation Event"
    OTHER = "other", "Other"


class ClaimStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    NOTIFIED = "notified", "Notified"
    SUBMITTED = "submitted", "Submitted"
    UNDER_REVIEW = "under_review", "Under review"
    DETERMINED = "determined", "Determined"
    AGREED = "agreed", "Agreed"
    REJECTED = "rejected", "Rejected"
    DISPUTED = "disputed", "Disputed"
    WITHDRAWN = "withdrawn", "Withdrawn"


class AssessmentOutcome(models.TextChoices):
    """The conclusion on a claim or one of its issues.

    A typed field with a database constraint, not prose. The prototype
    recovered its verdict with a browser-side regex over free text, so any
    change in model phrasing silently changed the answer shown.
    """

    SUBSTANTIATED = "substantiated", "Substantiated"
    PARTIALLY_SUBSTANTIATED = "partially_substantiated", "Partially substantiated"
    UNSUBSTANTIATED = "unsubstantiated", "Unsubstantiated"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence", "Insufficient evidence"
    """Not a failure state. The honest outcome when the record does not
    establish the position either way, and a first-class result throughout."""

    NOT_ASSESSED = "not_assessed", "Not assessed"


class Claim(BaseSoftDeleteModel):
    """A claim raised under the contract."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="claims")
    reference = models.CharField(max_length=64, blank=True)
    title = models.CharField(max_length=512)
    description = models.TextField(blank=True)

    claim_type = models.CharField(max_length=32, choices=ClaimType.choices, db_index=True)
    status = models.CharField(
        max_length=32, choices=ClaimStatus.choices, default=ClaimStatus.DRAFT, db_index=True
    )

    claimant = models.ForeignKey(
        Party, null=True, blank=True, on_delete=models.SET_NULL, related_name="claims_made"
    )
    respondent = models.ForeignKey(
        Party, null=True, blank=True, on_delete=models.SET_NULL, related_name="claims_received"
    )

    # -- Dates. Each is separate because the gaps between them are the
    # -- substance of a notice-compliance argument.
    event_date = models.DateField(
        null=True, blank=True, db_index=True, help_text="When the event occurred."
    )
    awareness_date = models.DateField(
        null=True,
        blank=True,
        help_text=(
            "When the claiming Party became aware, or should have become aware. "
            "Frequently disputed, and the date notice periods run from. Null "
            "means not established — never assume one."
        ),
    )
    notice_date = models.DateField(null=True, blank=True)
    submission_date = models.DateField(null=True, blank=True)
    determination_date = models.DateField(null=True, blank=True)

    # -- Quantum
    amount_claimed = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    amount_assessed = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    currency = models.CharField(max_length=3, blank=True)
    time_claimed_days = models.IntegerField(null=True, blank=True)
    time_awarded_days = models.IntegerField(null=True, blank=True)

    # -- Contractual basis
    contractual_basis = models.JSONField(
        default=list,
        blank=True,
        help_text="Clause numbers relied on, e.g. ['20.2.1', '8.5'].",
    )
    source_documents = models.ManyToManyField(
        Document, blank=True, related_name="claims", through="ClaimDocument"
    )

    # -- Human assessment. Separate from any AI finding, and never overwritten
    # -- by one (ADR 0006).
    human_outcome = models.CharField(
        max_length=32,
        choices=AssessmentOutcome.choices,
        default=AssessmentOutcome.NOT_ASSESSED,
    )
    human_assessment = models.TextField(blank=True)
    assessed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="claims_assessed"
    )
    assessed_at = models.DateTimeField(null=True, blank=True)

    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "claims_claim"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "reference"],
                condition=models.Q(deleted_at__isnull=True) & ~models.Q(reference=""),
                name="uniq_claim_reference_per_project",
            ),
            models.CheckConstraint(
                condition=models.Q(amount_claimed__isnull=True)
                | models.Q(amount_claimed__gte=0),
                name="claim_amount_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "claim_type"]),
            models.Index(fields=["project", "-event_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.reference} {self.title}".strip()

    @property
    def is_assessed(self) -> bool:
        return self.human_outcome != AssessmentOutcome.NOT_ASSESSED

    @property
    def has_awareness_date(self) -> bool:
        """Whether notice compliance can be computed at all.

        Without this date the period has no start, and the analysis returns
        INDETERMINATE rather than guessing.
        """
        return self.awareness_date is not None


class ClaimDocument(BaseModel):
    """Links a document to a claim, recording why it is attached."""

    class Role(models.TextChoices):
        CLAIM_SUBMISSION = "claim_submission", "Claim submission"
        NOTICE = "notice", "Notice"
        SUPPORTING = "supporting", "Supporting"
        RESPONSE = "response", "Response"
        DETERMINATION = "determination", "Determination"

    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="document_links")
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="claim_links")
    role = models.CharField(max_length=32, choices=Role.choices, default=Role.SUPPORTING)
    note = models.TextField(blank=True)

    class Meta:
        db_table = "claims_claim_document"
        constraints = [
            models.UniqueConstraint(
                fields=["claim", "document", "role"], name="uniq_claim_document_role"
            )
        ]

    def __str__(self) -> str:
        return f"{self.document} ({self.role})"


class ClaimEventType(models.TextChoices):
    TRIGGERING_EVENT = "triggering_event", "Triggering event"
    NOTICE_GIVEN = "notice_given", "Notice given"
    NOTICE_ACKNOWLEDGED = "notice_acknowledged", "Notice acknowledged"
    INSTRUCTION = "instruction", "Instruction"
    SUBMISSION = "submission", "Submission"
    RESPONSE = "response", "Response"
    DETERMINATION = "determination", "Determination"
    MEETING = "meeting", "Meeting"
    SITE_EVENT = "site_event", "Site event"
    PROGRAMME_UPDATE = "programme_update", "Programme update"
    PAYMENT = "payment", "Payment"
    OTHER = "other", "Other"


class ClaimEvent(BaseModel):
    """A dated event in a claim's history.

    The unit the chronology is built from. Every event carries provenance —
    which document and page it came from — because an undated, unsourced
    timeline entry is an assertion, not evidence.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="events")
    claim = models.ForeignKey(
        Claim, null=True, blank=True, on_delete=models.CASCADE, related_name="events"
    )

    event_type = models.CharField(max_length=32, choices=ClaimEventType.choices, db_index=True)
    occurred_on = models.DateField(db_index=True)
    occurred_at_precision = models.CharField(
        max_length=8,
        default="day",
        choices=[("day", "Day"), ("month", "Month"), ("year", "Year")],
        help_text=(
            "How precisely the date is known. A source saying 'in March' is not "
            "the same as one saying '14 March', and a chronology that renders "
            "both identically overstates what the record establishes."
        ),
    )
    title = models.CharField(max_length=512)
    description = models.TextField(blank=True)

    party = models.ForeignKey(
        Party, null=True, blank=True, on_delete=models.SET_NULL, related_name="events"
    )
    clause_references = models.JSONField(default=list, blank=True)

    # -- Provenance
    source_document = models.ForeignKey(
        Document, null=True, blank=True, on_delete=models.SET_NULL, related_name="events"
    )
    source_page = models.PositiveIntegerField(null=True, blank=True)

    is_ai_extracted = models.BooleanField(default=False)
    confidence = models.FloatField(
        null=True, blank=True, help_text="AI extraction confidence, when applicable."
    )
    is_confirmed = models.BooleanField(
        default=False,
        help_text="A human has confirmed this event. Unconfirmed AI events are "
        "shown distinctly rather than presented as established.",
    )
    confirmed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        db_table = "claims_claim_event"
        ordering = ["occurred_on", "created_at"]
        indexes = [
            models.Index(fields=["project", "occurred_on"]),
            models.Index(fields=["claim", "occurred_on"]),
            models.Index(fields=["project", "event_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.occurred_on} {self.title}"


class ClaimIssue(BaseModel):
    """One analysable strand of a claim.

    A claim is rarely one question. Entitlement, notice compliance, causation
    and quantum can each succeed or fail independently, and a single overall
    verdict hides that. Splitting them is what lets the analysis say "entitled
    in principle, but the notice was late".
    """

    class Category(models.TextChoices):
        """Strands of a claim.

        Every category an issue can take must reach an element the evidence-gap
        engine looks for, directly or through ``ISSUE_CATEGORY_TO_ELEMENT``.
        Evidence is tagged by the issue it is attached to, so a category with no
        element leaves that element permanently unestablished — which is how
        "The triggering event" stayed a gap on every claim regardless of the
        site records on file.
        """

        ENTITLEMENT = "entitlement", "Contractual entitlement"
        EVENT = "event", "The triggering event"
        NOTICE_COMPLIANCE = "notice_compliance", "Notice compliance"
        CAUSATION = "causation", "Causation"
        RESPONSIBILITY = "responsibility", "Responsibility"
        INSTRUCTION = "instruction", "The instruction"
        TIME_IMPACT = "time_impact", "Time impact"
        COST_IMPACT = "cost_impact", "Cost incurred"
        QUANTUM = "quantum", "Quantum"
        MITIGATION = "mitigation", "Mitigation"
        OTHER = "other", "Other"

    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="issues")
    category = models.CharField(max_length=32, choices=Category.choices, db_index=True)
    title = models.CharField(max_length=512)
    description = models.TextField(blank=True)
    sequence = models.PositiveIntegerField(default=0)

    human_outcome = models.CharField(
        max_length=32,
        choices=AssessmentOutcome.choices,
        default=AssessmentOutcome.NOT_ASSESSED,
    )
    human_assessment = models.TextField(blank=True)
    assessed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    assessed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "claims_claim_issue"
        ordering = ["sequence", "created_at"]
        indexes = [models.Index(fields=["claim", "category"])]

    def __str__(self) -> str:
        return f"{self.get_category_display()}: {self.title}"


class ClaimPosition(BaseModel):
    """A party's stated position on a claim or one of its issues.

    Modelled explicitly because a claim file contains competing accounts, and
    flattening them into one narrative loses the disagreement that is the whole
    point of the record.
    """

    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="positions")
    issue = models.ForeignKey(
        ClaimIssue, null=True, blank=True, on_delete=models.CASCADE, related_name="positions"
    )
    party = models.ForeignKey(
        Party, null=True, blank=True, on_delete=models.SET_NULL, related_name="positions"
    )
    summary = models.TextField()
    stated_on = models.DateField(null=True, blank=True)

    source_document = models.ForeignKey(
        Document, null=True, blank=True, on_delete=models.SET_NULL, related_name="positions"
    )
    source_page = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "claims_claim_position"
        ordering = ["stated_on", "created_at"]
        indexes = [models.Index(fields=["claim", "issue"])]

    def __str__(self) -> str:
        return f"{self.party or 'Unattributed'}: {self.summary[:60]}"


class EvidenceRelevance(models.TextChoices):
    SUPPORTS = "supports", "Supports"
    CONTRADICTS = "contradicts", "Contradicts"
    """Kept as a first-class value. A claim file that only records supporting
    evidence is a case summary, not an assessment."""

    NEUTRAL = "neutral", "Neutral"
    UNASSESSED = "unassessed", "Unassessed"


class Evidence(BaseSoftDeleteModel):
    """A piece of evidence linked to a claim or issue."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="evidence")
    claim = models.ForeignKey(
        Claim, null=True, blank=True, on_delete=models.CASCADE, related_name="evidence"
    )
    issue = models.ForeignKey(
        ClaimIssue, null=True, blank=True, on_delete=models.CASCADE, related_name="evidence"
    )

    title = models.CharField(max_length=512)
    description = models.TextField(blank=True)

    document = models.ForeignKey(
        Document, null=True, blank=True, on_delete=models.SET_NULL, related_name="evidence"
    )
    page_number = models.PositiveIntegerField(null=True, blank=True)
    excerpt = models.TextField(blank=True)

    relevance = models.CharField(
        max_length=16,
        choices=EvidenceRelevance.choices,
        default=EvidenceRelevance.UNASSESSED,
        db_index=True,
    )
    weight = models.CharField(
        max_length=16,
        blank=True,
        choices=[("strong", "Strong"), ("moderate", "Moderate"), ("weak", "Weak")],
    )

    is_ai_suggested = models.BooleanField(default=False)
    reviewed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "claims_evidence"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["claim", "relevance"]),
            models.Index(fields=["project", "relevance"]),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_reviewed(self) -> bool:
        return self.reviewed_at is not None
