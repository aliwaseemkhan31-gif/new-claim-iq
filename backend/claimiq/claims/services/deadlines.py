"""The notice and submission deadlines that govern a claim.

One place answers "which deadlines apply to this claim", so screening, the
notice tab, the checklist and the analysis engine cannot disagree. The answer
is built in three steps, each in the domain layer:

1. The standard form's requirements for the project's edition.
2. Amended by the project's own contract, from ``ContractDeadline`` rows a
   person has confirmed. Suggestions are not applied.
3. Narrowed to the claim: a claim for time is not assessed against a
   provision that only governs claims for money.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, List, Optional, Sequence, Tuple

from claimiq.claims.domain.notice_compliance import (
    Amendment,
    AmendmentAction,
    Applies,
    ComplianceFinding,
    DayCount,
    NoticeEvent,
    NoticeRequirement,
    Obligation,
    PeriodStart,
    applicable_requirements,
    apply_amendments,
    assess_requirements,
    recipient_label,
    requirements_for_edition,
)

if TYPE_CHECKING:  # pragma: no cover - import for typing only
    from claimiq.claims.models import Claim, ContractDeadline
    from claimiq.projects.models import Project


def _enum(cls, value):
    return cls(value) if value else None


def amendment_from(row: "ContractDeadline") -> Amendment:
    """The domain view of one confirmed row."""
    return Amendment(
        clause_number=row.clause_number.strip(),
        obligation=Obligation(row.obligation),
        action=AmendmentAction(row.action),
        period_days=row.period_days,
        day_count=_enum(DayCount, row.day_count),
        is_condition_precedent=row.is_condition_precedent,
        recipient=row.recipient or None,
        runs_from=_enum(PeriodStart, row.runs_from),
        runs_from_clause=row.runs_from_clause or None,
        applies_to=_enum(Applies, row.applies_to),
        title=row.title,
        description=row.note,
        late_consequence=row.late_consequence or None,
        source=row.source_label,
    )


def requirements_for_project(
    project: "Project", *, rows: Optional[Iterable["ContractDeadline"]] = None
) -> Tuple[NoticeRequirement, ...]:
    """The standard requirements, as this project's contract amends them.

    Args:
        project: The project.
        rows: Confirmed amendment rows, when the caller has already loaded
            them. Read from the database otherwise.
    """
    edition = project.contract_edition or ""
    if not edition:
        return ()
    if rows is None:
        from claimiq.claims.models import ContractDeadline

        rows = ContractDeadline.objects.filter(
            project=project, status=ContractDeadline.Status.CONFIRMED
        ).select_related("source_document")
    amendments = [amendment_from(row) for row in rows]
    return apply_amendments(requirements_for_edition(edition), amendments, edition=edition)


def requirements_for_claim(
    claim: "Claim", *, project_requirements: Optional[Sequence[NoticeRequirement]] = None
) -> Tuple[NoticeRequirement, ...]:
    """The requirements that govern this claim."""
    requirements = (
        project_requirements
        if project_requirements is not None
        else requirements_for_project(claim.project)
    )
    return applicable_requirements(
        requirements,
        claim_type=claim.claim_type,
        seeks_time=claim.time_claimed_days is not None,
        seeks_money=claim.amount_claimed is not None,
    )


def notice_events(notices: Iterable) -> List[NoticeEvent]:
    """Notice events from loaded ``Notice`` rows, skipping deleted letters."""
    events: List[NoticeEvent] = []
    for notice in notices:
        item = notice.correspondence
        if item.deleted_at is not None:
            continue
        recipient = (
            recipient_label(item.recipient.name, item.recipient.role)
            if item.recipient_id
            else item.recipient_raw
        )
        events.append(
            NoticeEvent(
                document_id=str(item.document_id or item.id),
                document_title=item.subject or item.reference or "Notice",
                sent_date=item.sent_date,
                received_date=item.received_date,
                subject=item.subject,
                recipient=recipient,
                is_confirmed_notice=notice.is_confirmed_notice,
                clause_number=notice.clause_number,
                obligation=_enum(Obligation, notice.obligation),
                record_id=str(item.id),
            )
        )
    return events


def assess_claim(
    claim: "Claim",
    *,
    notices: Optional[Sequence] = None,
    project_requirements: Optional[Sequence[NoticeRequirement]] = None,
) -> List[ComplianceFinding]:
    """Assess every deadline governing ``claim`` against its notices on record."""
    if notices is None:
        from claimiq.correspondence.models import Notice

        notices = list(
            Notice.objects.filter(claim=claim).select_related("correspondence__recipient")
        )
    return assess_requirements(
        requirements_for_claim(claim, project_requirements=project_requirements),
        awareness_date=claim.awareness_date,
        notices=notice_events(notices),
    )


def finding_payload(finding: ComplianceFinding) -> dict:
    """One finding, as every endpoint renders it."""
    requirement = finding.requirement
    notice = finding.notice
    return {
        "clause_number": requirement.clause_number,
        "title": requirement.label,
        "obligation": requirement.obligation.value,
        "description": requirement.description,
        "period_days": requirement.period_days,
        "day_count": requirement.day_count.value,
        "runs_from": requirement.runs_from.value,
        "runs_from_clause": requirement.runs_from_clause,
        "recipient": requirement.recipient,
        "source": requirement.source,
        "is_amended": requirement.is_amended,
        "status": finding.status.value,
        "summary": finding.summary(),
        "deadline": finding.deadline.isoformat() if finding.deadline else None,
        "days_used": finding.days_used,
        "days_late": finding.days_late,
        "is_time_barred": finding.is_time_barred,
        "is_condition_precedent": requirement.is_condition_precedent,
        "late_consequence": requirement.late_consequence,
        "notice": (
            {
                "document_id": notice.document_id,
                "record_id": notice.record_id,
                "title": notice.document_title,
                "sent_date": notice.sent_date.isoformat() if notice.sent_date else None,
                "received_date": (
                    notice.received_date.isoformat() if notice.received_date else None
                ),
                "is_confirmed": notice.is_confirmed_notice,
            }
            if notice
            else None
        ),
        "assumptions": list(finding.assumptions),
        "warnings": list(finding.warnings),
    }
