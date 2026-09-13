"""Project correspondence and formal notices.

Correspondence is where notice compliance is won or lost, so it is modelled
separately from documents in general: it has a sender, a recipient, a date sent
and a date received, and it points at what it replies to. Those fields are what
make "Notice -> Response -> Follow-up -> Determination" a traversable chain
rather than a pile of PDFs someone has to read in order.
"""
from __future__ import annotations

from django.db import models

from claimiq.core.models import BaseModel, BaseSoftDeleteModel
from claimiq.documents.models import Document
from claimiq.projects.models import Party, Project


class CorrespondenceDirection(models.TextChoices):
    INBOUND = "inbound", "Inbound"
    OUTBOUND = "outbound", "Outbound"
    INTERNAL = "internal", "Internal"


class CorrespondenceKind(models.TextChoices):
    LETTER = "letter", "Letter"
    EMAIL = "email", "Email"
    NOTICE = "notice", "Notice"
    INSTRUCTION = "instruction", "Engineer's Instruction"
    DETERMINATION = "determination", "Determination"
    RESPONSE = "response", "Response"
    MINUTES = "minutes", "Meeting minutes"
    RFI = "rfi", "Request for Information"
    SITE_INSTRUCTION = "site_instruction", "Site instruction"
    OTHER = "other", "Other"


class Correspondence(BaseSoftDeleteModel):
    """One item of project correspondence."""

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="correspondence"
    )
    document = models.ForeignKey(
        Document,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="correspondence",
    )

    reference = models.CharField(max_length=128, blank=True, db_index=True)
    subject = models.CharField(max_length=512)
    summary = models.TextField(blank=True)

    kind = models.CharField(
        max_length=32, choices=CorrespondenceKind.choices, default=CorrespondenceKind.LETTER,
        db_index=True,
    )
    direction = models.CharField(
        max_length=16, choices=CorrespondenceDirection.choices, blank=True
    )

    sender = models.ForeignKey(
        Party, null=True, blank=True, on_delete=models.SET_NULL, related_name="sent_correspondence"
    )
    recipient = models.ForeignKey(
        Party,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="received_correspondence",
    )
    sender_raw = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            "Sender exactly as written on the document. Retained alongside the "
            "resolved Party so an attribution can be checked, and so a wrong "
            "match is correctable rather than invisible."
        ),
    )
    recipient_raw = models.CharField(max_length=255, blank=True)

    sent_date = models.DateField(null=True, blank=True, db_index=True)
    received_date = models.DateField(
        null=True,
        blank=True,
        help_text=(
            "Most notice provisions turn on receipt, so this is preferred over "
            "sent_date when computing compliance."
        ),
    )

    clause_references = models.JSONField(default=list, blank=True)
    in_reply_to = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="replies"
    )

    is_ai_extracted = models.BooleanField(default=False)
    extraction_confidence = models.FloatField(null=True, blank=True)
    is_confirmed = models.BooleanField(default=False)

    class Meta:
        db_table = "correspondence_correspondence"
        ordering = ["-sent_date", "-created_at"]
        verbose_name_plural = "correspondence"
        indexes = [
            models.Index(fields=["project", "-sent_date"]),
            models.Index(fields=["project", "kind"]),
            models.Index(fields=["sender", "-sent_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.reference} {self.subject}".strip()

    @property
    def effective_date(self):
        """The date used for compliance calculations.

        Receipt where known, otherwise despatch. Which one applied is recorded
        as an assumption on the finding rather than hidden.
        """
        return self.received_date or self.sent_date


class Notice(BaseModel):
    """A correspondence item asserted to be a contractual Notice.

    Separate from :class:`Correspondence` because "is this a Notice" is a
    contested question, not a document property. A letter may be argued to
    constitute a Notice without saying so; a document headed "Notice" may fail
    to satisfy the provision. The assertion, who made it, and whether anyone
    has confirmed it are therefore recorded explicitly.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="notices")
    correspondence = models.ForeignKey(
        Correspondence, on_delete=models.CASCADE, related_name="notices"
    )
    claim = models.ForeignKey(
        "claims.Claim", null=True, blank=True, on_delete=models.CASCADE, related_name="notices"
    )

    clause_number = models.CharField(
        max_length=32, help_text="The provision the Notice is given under, e.g. 20.2.1."
    )
    edition_code = models.CharField(
        max_length=64,
        blank=True,
        help_text=(
            "Governing edition. Recorded because notice periods and their "
            "consequences differ between editions (ADR 0004)."
        ),
    )

    is_confirmed_notice = models.BooleanField(
        default=False,
        help_text=(
            "A human has confirmed this satisfies the provision. Until then the "
            "compliance finding carries a caveat rather than treating it as one."
        ),
    )
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "correspondence_notice"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["correspondence", "clause_number"], name="uniq_notice_per_clause"
            )
        ]
        indexes = [
            models.Index(fields=["project", "clause_number"]),
            models.Index(fields=["claim"]),
        ]

    def __str__(self) -> str:
        return f"Notice under {self.clause_number}"
