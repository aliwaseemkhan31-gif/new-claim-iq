"""Build a chronology from persisted rows.

The bridge between the ORM and the framework-free assembler in
:mod:`claimiq.claims.domain.chronology`. All the logic — ordering, precision,
conflict detection — lives there and is unit-tested without a database; this
module only reads rows and maps them.
"""
from __future__ import annotations

from typing import Iterable
from uuid import UUID

from claimiq.claims.domain.chronology import (
    Chronology,
    DatePrecision,
    EntryKind,
    TimelineEntry,
    build_chronology,
)
from claimiq.claims.models import Claim, ClaimEvent
from claimiq.clauses.models import Deadline, DeadlineStatus
from claimiq.correspondence.models import Correspondence


def _precision(value: str) -> DatePrecision:
    try:
        return DatePrecision(value)
    except ValueError:
        return DatePrecision.DAY


def _event_entries(project_id: UUID, claim_id: UUID | None) -> list[TimelineEntry]:
    queryset = ClaimEvent.objects.filter(project_id=project_id).select_related(
        "party", "source_document", "claim"
    )
    if claim_id is not None:
        queryset = queryset.filter(claim_id=claim_id)

    return [
        TimelineEntry(
            entry_id=str(event.id),
            kind=EntryKind.EVENT,
            occurred_on=event.occurred_on,
            title=event.title,
            description=event.description,
            precision=_precision(event.occurred_at_precision),
            party=event.party.name if event.party else None,
            clause_references=tuple(event.clause_references or ()),
            source_document_id=str(event.source_document_id) if event.source_document_id else None,
            source_document_title=(
                event.source_document.title if event.source_document else None
            ),
            source_page=event.source_page,
            claim_id=str(event.claim_id) if event.claim_id else None,
            is_ai_extracted=event.is_ai_extracted,
            is_confirmed=event.is_confirmed,
            confidence=event.confidence,
        )
        for event in queryset
    ]


def _correspondence_entries(project_id: UUID) -> list[TimelineEntry]:
    queryset = Correspondence.objects.filter(project_id=project_id).select_related(
        "sender", "document"
    )
    entries: list[TimelineEntry] = []
    for item in queryset:
        # Receipt where known, despatch otherwise — the same preference the
        # notice-compliance engine applies, so the two never disagree about
        # which date an item carries.
        occurred = item.effective_date
        entries.append(
            TimelineEntry(
                entry_id=str(item.id),
                kind=EntryKind.NOTICE if item.notices.exists() else EntryKind.CORRESPONDENCE,
                occurred_on=occurred,
                title=item.subject or item.reference or "Correspondence",
                description=item.summary,
                precision=DatePrecision.DAY if occurred else DatePrecision.UNKNOWN,
                party=item.sender.name if item.sender else (item.sender_raw or None),
                clause_references=tuple(item.clause_references or ()),
                source_document_id=str(item.document_id) if item.document_id else None,
                source_document_title=item.document.title if item.document else None,
                is_ai_extracted=item.is_ai_extracted,
                is_confirmed=item.is_confirmed,
                confidence=item.extraction_confidence,
            )
        )
    return entries


def _deadline_entries(project_id: UUID, claim_id: UUID | None) -> list[TimelineEntry]:
    queryset = Deadline.objects.filter(project_id=project_id).exclude(
        status=DeadlineStatus.NOT_APPLICABLE
    )
    if claim_id is not None:
        queryset = queryset.filter(claim_id=claim_id)

    return [
        TimelineEntry(
            entry_id=str(deadline.id),
            kind=EntryKind.DEADLINE,
            occurred_on=deadline.due_date,
            title=deadline.title,
            description=deadline.notes,
            precision=DatePrecision.DAY if deadline.due_date else DatePrecision.UNKNOWN,
            clause_references=(deadline.clause_number,) if deadline.clause_number else (),
            claim_id=str(deadline.claim_id) if deadline.claim_id else None,
            # A deadline is computed from recorded dates, not extracted by a
            # model, so it needs no review flag.
            is_confirmed=True,
        )
        for deadline in queryset
    ]


def _claim_milestones(claims: Iterable[Claim]) -> list[TimelineEntry]:
    """The dates recorded directly on a claim.

    Included because a claim's own notice and submission dates are part of its
    story, and a chronology assembled only from linked events would omit them
    whenever nobody created a matching event row.
    """
    entries: list[TimelineEntry] = []
    for claim in claims:
        milestones = [
            ("event", claim.event_date, "Event occurred"),
            ("notice", claim.notice_date, "Notice given"),
            ("submission", claim.submission_date, "Claim submitted"),
            ("determination", claim.determination_date, "Determination issued"),
        ]
        for suffix, when, label in milestones:
            if when is None:
                continue
            entries.append(
                TimelineEntry(
                    entry_id=f"{claim.id}:{suffix}",
                    kind=EntryKind.CLAIM_MILESTONE,
                    occurred_on=when,
                    title=f"{label} — {claim.reference or claim.title}",
                    precision=DatePrecision.DAY,
                    clause_references=tuple(claim.contractual_basis or ()),
                    claim_id=str(claim.id),
                    is_confirmed=True,
                )
            )
    return entries


def build_project_chronology(
    *,
    project_id: UUID,
    claim_id: UUID | None = None,
    include_unreviewed: bool = True,
    kinds: Iterable[EntryKind] | None = None,
) -> Chronology:
    """Assemble the chronology for a project, or for one claim within it."""
    claims = Claim.objects.filter(project_id=project_id)
    if claim_id is not None:
        claims = claims.filter(pk=claim_id)

    entries: list[TimelineEntry] = []
    entries.extend(_event_entries(project_id, claim_id))
    entries.extend(_deadline_entries(project_id, claim_id))
    entries.extend(_claim_milestones(claims))

    # Correspondence is included project-wide even when scoped to one claim.
    # An item is relevant to a claim through its clause references and dates,
    # not only through an explicit link, and filtering to linked items would
    # hide the correspondence that contradicts the claim — which is exactly
    # what a reviewer needs to see.
    entries.extend(_correspondence_entries(project_id))

    return build_chronology(
        entries, include_unreviewed=include_unreviewed, kinds=kinds
    )
