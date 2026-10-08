"""The documents filed on a claim: the claim itself, its notices, its support.

A claim already had every structure these need — ``ClaimDocument`` with a role,
``Notice`` over correspondence, ``Evidence`` tied to an issue — but nothing
connected an uploaded file to them. Recording a notice took four steps on
three screens, and a file uploaded to the project counted for nothing until
it had been through them all.

Attaching a file here does the whole job in one step, so the checks that read
those structures see it straight away:

- **Claim document** — linked as the claim submission, and recorded as the
  detailed claim under the provision it answers (1987 Sub-Clause 44.2(b) or
  53.3, 1999 Sub-Clause 20.1, 2017 Sub-Clause 20.2.4). Its date fills the
  claim's submission date where none is recorded.
- **Notice** — linked as a notice, and asserted as a Notice under the provision
  and obligation the person names.
- **Supporting document** — linked as supporting, and recorded as evidence of
  the element it is said to prove, under an issue of that category.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from django.db import transaction
from django.utils import timezone

from claimiq.claims.domain.evidence_gaps import elements_for
from claimiq.claims.models import Claim, ClaimDocument, ClaimIssue, Evidence, EvidenceRelevance
from claimiq.core.domain.errors import NotFoundError, ValidationError
from claimiq.documents.models import Document

ROLE_CLAIM = ClaimDocument.Role.CLAIM_SUBMISSION
ROLE_NOTICE = ClaimDocument.Role.NOTICE
ROLE_SUPPORTING = ClaimDocument.Role.SUPPORTING

#: The three places a person files a document on a claim.
ATTACHABLE_ROLES = (ROLE_CLAIM, ROLE_NOTICE, ROLE_SUPPORTING)

#: Evidence elements whose issue category differs from the element code.
#: The inverse of ``evidence_gaps.ISSUE_CATEGORY_TO_ELEMENT``.
_ELEMENT_TO_CATEGORY = {"notice": "notice_compliance", "responsibility": "entitlement"}


@dataclass
class Attachment:
    """What a person says about a file they are filing on a claim."""

    role: str
    sent_date: Optional[date] = None
    received_date: Optional[date] = None
    clause_number: str = ""
    obligation: str = ""
    confirmed: bool = True
    element: str = ""
    relevance: str = EvidenceRelevance.SUPPORTS
    note: str = ""


def _issue_for(claim: Claim, element_code: str, user) -> ClaimIssue:
    """The claim's issue for an evidence element, created if it has none.

    Evidence reaches the element it proves through the category of the issue
    it is attached to, so evidence filed against "time impact" needs a time
    impact issue to land on.
    """
    category = _ELEMENT_TO_CATEGORY.get(element_code, element_code)
    issue = claim.issues.filter(category=category).order_by("sequence", "created_at").first()
    if issue is not None:
        return issue
    label = next(
        (e.label for e in elements_for(claim.claim_type) if e.code == element_code),
        ClaimIssue.Category(category).label,
    )
    return ClaimIssue.objects.create(
        claim=claim,
        category=category,
        title=label,
        sequence=claim.issues.count(),
        created_by=user,
    )


def _record_notice(claim: Claim, document: Document, details: Attachment, *, obligation: str, user):
    """Record ``document`` as correspondence and assert it as a Notice."""
    from claimiq.correspondence.models import (
        Correspondence,
        CorrespondenceDirection,
        CorrespondenceKind,
        Notice,
    )

    clause = details.clause_number.strip()
    item = (
        Correspondence.objects.filter(
            project=claim.project, document=document, deleted_at__isnull=True
        )
        .order_by("created_at")
        .first()
    )
    if item is None:
        item = Correspondence.objects.create(
            project=claim.project,
            document=document,
            subject=document.title,
            reference=document.reference,
            kind=(
                CorrespondenceKind.LETTER
                if obligation == Notice.Obligation.DETAILED_CLAIM
                else CorrespondenceKind.NOTICE
            ),
            direction=CorrespondenceDirection.OUTBOUND,
            sent_date=details.sent_date,
            received_date=details.received_date,
            clause_references=[clause],
            is_confirmed=True,
            created_by=user,
        )
    else:
        item.sent_date = details.sent_date
        item.received_date = details.received_date
        item.save(update_fields=["sent_date", "received_date", "updated_at"])

    notice, _ = Notice.objects.update_or_create(
        correspondence=item,
        clause_number=clause,
        obligation=obligation,
        defaults={
            "project": claim.project,
            "claim": claim,
            "edition_code": claim.project.contract_edition or "",
            "is_confirmed_notice": details.confirmed,
            "notes": details.note,
            "updated_by": user,
        },
    )
    return notice


def validate(details: Attachment) -> None:
    """Refuse an incomplete filing before anything is written.

    Called before an upload as well as inside :func:`attach`, so a filing that
    is missing its date or clause does not leave an orphaned upload behind.
    """
    from claimiq.correspondence.models import Notice

    if details.role not in ATTACHABLE_ROLES:
        raise ValidationError(
            "Choose where this document goes: the claim, a notice, or support.",
            details={"field": "role", "valid": list(ATTACHABLE_ROLES)},
        )
    if details.role in (ROLE_CLAIM, ROLE_NOTICE):
        if not details.clause_number.strip():
            raise ValidationError(
                "Name the provision this was given under, for example 44.2 or 20.2.1.",
                details={"field": "clause_number"},
            )
        if details.sent_date is None:
            raise ValidationError(
                "The date it was sent is needed to check it against its deadline.",
                details={"field": "sent_date"},
            )
        if details.received_date and details.received_date < details.sent_date:
            raise ValidationError(
                "The received date is before the sent date.",
                details={"field": "received_date"},
            )
    if details.role == ROLE_NOTICE and details.obligation and (
        details.obligation not in Notice.Obligation.values
    ):
        raise ValidationError(
            "Say whether this is the notice or the detailed particulars.",
            details={"field": "obligation"},
        )
    if details.role == ROLE_SUPPORTING:
        if not details.element:
            raise ValidationError(
                "Say what this document helps prove, so it counts towards that element.",
                details={"field": "element"},
            )
        if details.relevance not in EvidenceRelevance.values:
            raise ValidationError("Unknown relevance.", details={"field": "relevance"})
        category = _ELEMENT_TO_CATEGORY.get(details.element, details.element)
        if category not in {c.value for c in ClaimIssue.Category}:
            raise ValidationError(
                f"Unknown evidence element {details.element!r}.",
                details={"field": "element"},
            )


@transaction.atomic
def attach(claim: Claim, document: Document, details: Attachment, *, user) -> ClaimDocument:
    """File ``document`` on ``claim`` in the role given, recording what it is."""
    from claimiq.correspondence.models import Notice

    validate(details)
    if document.project_id != claim.project_id:
        raise ValidationError(
            "That document belongs to a different project.", details={"field": "document"}
        )

    link, _ = ClaimDocument.objects.get_or_create(
        claim=claim,
        document=document,
        role=details.role,
        defaults={"note": details.note, "created_by": user},
    )

    claim_fields: List[str] = []
    if details.role == ROLE_CLAIM:
        _record_notice(
            claim, document, details, obligation=Notice.Obligation.DETAILED_CLAIM, user=user
        )
        if claim.submission_date is None:
            claim.submission_date = details.sent_date
            claim_fields.append("submission_date")

    elif details.role == ROLE_NOTICE:
        obligation = details.obligation or Notice.Obligation.NOTICE_OF_CLAIM
        _record_notice(claim, document, details, obligation=obligation, user=user)
        if obligation == Notice.Obligation.NOTICE_OF_CLAIM:
            # The notice is also the evidence that notice was given, so the
            # gap analysis, here and in the AI strand, agrees with the
            # deadline check rather than reporting the notice missing.
            _record_evidence(
                claim, document, "notice", EvidenceRelevance.SUPPORTS, details.note, user=user
            )
        if obligation == Notice.Obligation.NOTICE_OF_CLAIM and claim.notice_date is None:
            claim.notice_date = details.sent_date
            claim_fields.append("notice_date")
        if obligation == Notice.Obligation.DETAILED_CLAIM and claim.submission_date is None:
            claim.submission_date = details.sent_date
            claim_fields.append("submission_date")

    else:
        _record_evidence(
            claim, document, details.element, details.relevance, details.note, user=user
        )

    if claim_fields:
        claim.save(update_fields=[*claim_fields, "updated_at"])
    return link


def _record_evidence(claim: Claim, document: Document, element: str, relevance: str, note: str, *, user):
    issue = _issue_for(claim, element, user)
    existing = Evidence.objects.filter(
        claim=claim, document=document, issue=issue, deleted_at__isnull=True
    ).first()
    if existing is not None:
        return existing
    return Evidence.objects.create(
        project=claim.project,
        claim=claim,
        issue=issue,
        document=document,
        title=document.title,
        description=note,
        relevance=relevance,
        # The person filing it has said what it shows; that is a review.
        reviewed_by=user,
        reviewed_at=timezone.now(),
        created_by=user,
    )


@transaction.atomic
def detach(claim: Claim, link_id, *, user) -> None:
    """Take a document off a claim, with the notice or evidence it was filed as.

    The document stays in the project. The letter's correspondence record
    stays too — it is still a letter — but it no longer counts for this claim.
    """
    from claimiq.correspondence.models import Notice

    link = ClaimDocument.objects.filter(claim=claim, pk=link_id).select_related("document").first()
    if link is None:
        raise NotFoundError("That document is not filed on this claim.")

    document = link.document
    if link.role == ROLE_SUPPORTING:
        for evidence in Evidence.objects.filter(
            claim=claim, document=document, deleted_at__isnull=True
        ):
            evidence.soft_delete(user=user)
    else:
        notices = Notice.objects.filter(claim=claim, correspondence__document=document)
        if link.role == ROLE_CLAIM:
            notices = notices.filter(obligation=Notice.Obligation.DETAILED_CLAIM)
        elif ClaimDocument.objects.filter(
            claim=claim, document=document, role=ROLE_CLAIM
        ).exclude(pk=link.pk).exists():
            # Also filed as the claim itself: keep that record.
            notices = notices.exclude(obligation=Notice.Obligation.DETAILED_CLAIM)
        notices.delete()
        if link.role == ROLE_NOTICE:
            for evidence in Evidence.objects.filter(
                claim=claim,
                document=document,
                issue__category="notice_compliance",
                deleted_at__isnull=True,
            ):
                evidence.soft_delete(user=user)
    link.delete()


def files_payload(claim: Claim) -> List[dict]:
    """Every document filed on the claim, with what it was filed as."""
    from claimiq.correspondence.models import Notice

    links = (
        ClaimDocument.objects.filter(claim=claim, document__deleted_at__isnull=True)
        .select_related("document__current_version")
        .order_by("role", "created_at")
    )
    notices = {}
    for notice in Notice.objects.filter(claim=claim).select_related("correspondence"):
        if notice.correspondence.deleted_at is None and notice.correspondence.document_id:
            notices.setdefault(notice.correspondence.document_id, []).append(notice)
    evidence = {}
    for item in Evidence.objects.filter(
        claim=claim, deleted_at__isnull=True, document__isnull=False
    ).select_related("issue"):
        evidence.setdefault(item.document_id, []).append(item)

    rows = []
    for link in links:
        document = link.document
        version = document.current_version
        rows.append(
            {
                "id": str(link.pk),
                "role": link.role,
                "role_label": link.get_role_display(),
                "note": link.note,
                "document": {
                    "id": str(document.pk),
                    "title": document.title,
                    "document_type": document.document_type,
                    "document_date": (
                        document.document_date.isoformat() if document.document_date else None
                    ),
                    "processing_status": version.processing_status if version else None,
                    "page_count": version.page_count if version else None,
                },
                "notices": [
                    {
                        "id": str(n.pk),
                        "clause_number": n.clause_number,
                        "obligation": n.obligation,
                        "sent_date": (
                            n.correspondence.sent_date.isoformat()
                            if n.correspondence.sent_date
                            else None
                        ),
                        "received_date": (
                            n.correspondence.received_date.isoformat()
                            if n.correspondence.received_date
                            else None
                        ),
                        "is_confirmed_notice": n.is_confirmed_notice,
                    }
                    for n in notices.get(document.pk, [])
                ]
                if link.role != ROLE_SUPPORTING
                else [],
                "evidence": [
                    {
                        "id": str(e.pk),
                        "title": e.title,
                        "page_number": e.page_number,
                        "description": e.description,
                        "issue_category": e.issue.category if e.issue_id else None,
                        "issue_title": e.issue.title if e.issue_id else None,
                        "relevance": e.relevance,
                    }
                    for e in evidence.get(document.pk, [])
                ]
                if link.role == ROLE_SUPPORTING
                else [],
                "created_at": link.created_at.isoformat(),
            }
        )
    return rows
