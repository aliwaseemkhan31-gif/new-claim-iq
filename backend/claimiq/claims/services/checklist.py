"""The claim checklist: is everything filed, and was it on time?

One row per thing a claim needs, in the order a claim is built — the event,
the notice, the claim itself, the evidence — each answering two questions: is
it on record, and if it has a deadline, was that deadline met?

Nothing here is computed twice. Timing comes from the same deadline service
screening and the analysis engine use, and evidence from the same gap
analysis, so the checklist cannot disagree with the rest of the claim.

Statuses:

- ``ok``       on record, and in time where there is a deadline
- ``late``     on record, after its deadline
- ``barred``   late under a provision expressed as a condition precedent
- ``missing``  not on record, and its deadline (if any) has passed
- ``due``      not on record yet, and its deadline has not passed
- ``unknown``  cannot be answered until something else is recorded
- ``optional`` not on record, and not essential
- ``contested`` evidence on record both supports and contradicts it
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from django.db.models import Prefetch
from django.utils import timezone

from claimiq.claims.domain.evidence_gaps import (
    ElementStatus,
    EvidenceItem,
    Relevance,
    analyse_gaps,
    element_code_for_issue_category,
)
from claimiq.claims.domain.notice_compliance import (
    ComplianceFinding,
    ComplianceStatus,
    Obligation,
)
from claimiq.claims.models import Claim, ClaimDocument
from claimiq.claims.services.deadlines import (
    assess_claim,
    requirements_for_project,
)

OK, LATE, BARRED, MISSING, DUE, UNKNOWN, OPTIONAL, CONTESTED = (
    "ok", "late", "barred", "missing", "due", "unknown", "optional", "contested",
)

#: Statuses that mean the claim needs work.
OUTSTANDING = frozenset({LATE, BARRED, MISSING, UNKNOWN, CONTESTED})


def _row(key: str, label: str, status: str, detail: str, **extra) -> dict:
    return {"key": key, "label": label, "status": status, "detail": detail, **extra}


def _date(value) -> Optional[str]:
    return value.isoformat() if value else None


def _deadline_row(finding: ComplianceFinding, filed_documents: set, today) -> dict:
    """One deadline, read as "is it on record, and was it in time"."""
    requirement = finding.requirement
    notice = finding.notice
    clause = f"Clause {requirement.clause_number}"
    period = f"{requirement.period_days} {requirement.day_count.value} days"
    if requirement.runs_from.value == "notice":
        period += f" after the notice under Clause {requirement.runs_from_clause}"
    amended = f" (as amended: {requirement.source})" if requirement.source else ""
    has_file = bool(notice and notice.document_id in filed_documents)

    if finding.status is ComplianceStatus.COMPLIANT:
        status = OK
        detail = (
            f"Given {notice.effective_date.isoformat()}, day {finding.days_used} of "
            f"{requirement.period_days}."
        )
    elif finding.status is ComplianceStatus.LATE:
        status = BARRED if finding.is_time_barred else LATE
        detail = (
            f"Given {notice.effective_date.isoformat()}, {finding.days_late} day(s) after "
            f"the deadline of {finding.deadline.isoformat()}."
        )
        if requirement.late_consequence:
            detail += " " + requirement.late_consequence
        elif finding.is_time_barred:
            detail += " The provision is expressed as a condition precedent."
    elif finding.status is ComplianceStatus.NOT_GIVEN:
        if finding.deadline and finding.deadline < today:
            status = MISSING
            detail = f"Nothing on record. The deadline was {finding.deadline.isoformat()}."
        else:
            status = DUE
            detail = (
                f"Nothing on record yet. Due by {finding.deadline.isoformat()}."
                if finding.deadline
                else "Nothing on record yet."
            )
    elif notice is not None:
        status = UNKNOWN
        detail = (
            f"On record ({notice.effective_date.isoformat()}), but whether it was in "
            f"time cannot be worked out yet. "
        ) + (" ".join(finding.warnings[:1]) or "A date it depends on is not recorded.")
    else:
        # Nothing filed is the answer to "is it on record", whatever the dates.
        status = MISSING
        detail = "Nothing on record. The deadline cannot be worked out yet either: " + (
            " ".join(finding.warnings[:1]) or "a date it depends on is not recorded."
        )

    return _row(
        f"deadline:{requirement.clause_number}:{requirement.obligation.value}",
        requirement.label,
        status,
        detail,
        clause=requirement.clause_number,
        rule=f"{clause}: {period}{amended}",
        deadline=_date(finding.deadline),
        filed=has_file,
        document_title=notice.document_title if notice else None,
        is_amended=requirement.is_amended,
    )


def _evidence_items(claim: Claim) -> List[EvidenceItem]:
    return [
        EvidenceItem(
            evidence_id=str(e.id),
            title=e.title,
            element_code=element_code_for_issue_category(e.issue.category if e.issue_id else None),
            relevance=Relevance(e.relevance) if e.relevance in Relevance._value2member_map_ else Relevance.UNASSESSED,
            weight=e.weight,
            document_type=e.document.document_type if e.document_id else None,
            is_reviewed=e.is_reviewed,
        )
        for e in claim.evidence.all()
        if e.deleted_at is None
    ]


def build(claim: Claim, *, project_requirements=None, today=None) -> dict:
    """The checklist for one claim."""
    today = today or timezone.localdate()
    links = list(claim.document_links.all())
    filed_documents = {str(link.document_id) for link in links}
    claim_documents = [link for link in links if link.role == ClaimDocument.Role.CLAIM_SUBMISSION]

    findings = assess_claim(
        claim, notices=list(claim.notices.all()), project_requirements=project_requirements
    )
    notices = [f for f in findings if f.requirement.obligation is Obligation.NOTICE_OF_CLAIM]
    detailed = [f for f in findings if f.requirement.obligation is Obligation.DETAILED_CLAIM]

    # -- The event --------------------------------------------------------
    gaps = analyse_gaps(claim.claim_type, _evidence_items(claim))
    event_element = next((a for a in gaps.assessments if a.element.code == "event"), None)
    event_rows = [
        _row(
            "event_date",
            "Date of the event",
            OK if claim.event_date else MISSING,
            f"Recorded as {claim.event_date.isoformat()}." if claim.event_date
            else "Not recorded. Record when the event happened.",
            value=_date(claim.event_date),
        ),
        _row(
            "awareness_date",
            "Date the contractor became aware",
            OK if claim.awareness_date else MISSING,
            f"Recorded as {claim.awareness_date.isoformat()}. Notice periods run from it."
            if claim.awareness_date
            else "Not recorded. Every notice deadline runs from this date, so none can be "
            "checked without it.",
            value=_date(claim.awareness_date),
        ),
    ]
    if event_element is not None:
        event_rows.append(_evidence_row(event_element))

    # -- The notice -------------------------------------------------------
    notice_rows = [_deadline_row(f, filed_documents, today) for f in notices]
    if not notice_rows:
        notice_rows.append(_no_requirements_row(claim, "notice"))

    # -- The claim itself -------------------------------------------------
    claim_rows = [
        _row(
            "claim_document",
            "Claim document uploaded",
            OK if claim_documents else MISSING,
            f"{len(claim_documents)} filed: "
            + ", ".join(link.document.title for link in claim_documents)
            if claim_documents
            else "No claim document is filed on this claim.",
            filed=bool(claim_documents),
        )
    ]
    claim_rows.extend(_deadline_row(f, filed_documents, today) for f in detailed)

    # -- The evidence -----------------------------------------------------
    evidence_rows = [_evidence_row(a) for a in gaps.assessments if a.element.code != "event"]

    sections = [
        {"code": "event", "label": "Event", "rows": event_rows},
        {"code": "notice", "label": "Notice", "rows": notice_rows},
        {"code": "claim", "label": "Claim submission", "rows": claim_rows},
        {"code": "evidence", "label": "Supporting evidence", "rows": evidence_rows},
    ]
    counts: Dict[str, int] = {}
    for section in sections:
        section_counts: Dict[str, int] = {}
        for row in section["rows"]:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
            section_counts[row["status"]] = section_counts.get(row["status"], 0) + 1
        section["counts"] = section_counts
        section["outstanding"] = sum(
            n for status, n in section_counts.items() if status in OUTSTANDING
        )

    outstanding = sum(n for status, n in counts.items() if status in OUTSTANDING)
    return {
        "claim": {
            "id": str(claim.pk),
            "reference": claim.reference,
            "title": claim.title,
            "claim_type": claim.claim_type,
            "claim_type_label": claim.get_claim_type_display(),
            "project": str(claim.project_id),
            "project_name": claim.project.name,
            "edition": claim.project.contract_edition or "",
        },
        "counts": counts,
        "outstanding": outstanding,
        "complete": outstanding == 0 and not counts.get(DUE),
        "sections": sections,
    }


def _evidence_row(assessment) -> dict:
    element = assessment.element
    status_map = {
        ElementStatus.ESTABLISHED: OK,
        ElementStatus.CONTESTED: CONTESTED,
    }
    if assessment.status in status_map:
        status = status_map[assessment.status]
    elif assessment.supporting or assessment.unreviewed:
        status = UNKNOWN
    else:
        status = MISSING if element.is_essential else OPTIONAL

    count = len(assessment.supporting) + len(assessment.contradicting) + len(assessment.unreviewed)
    if status == OK:
        detail = f"{len(assessment.supporting)} supporting document(s) on record."
    elif status == CONTESTED:
        detail = (
            f"Contested: {len(assessment.supporting)} supporting and "
            f"{len(assessment.contradicting)} contradicting."
        )
    elif status == UNKNOWN:
        detail = f"{count} document(s) on record, not yet reviewed as supporting."
    else:
        detail = assessment.suggestion()
    return _row(
        f"evidence:{element.code}",
        element.label,
        status,
        detail,
        essential=element.is_essential,
        documents=count,
        typical_documents=list(element.typical_documents),
    )


def _no_requirements_row(claim: Claim, kind: str) -> dict:
    edition = claim.project.contract_edition
    return _row(
        f"{kind}:none",
        "Notice requirements",
        UNKNOWN,
        "The project has not declared its conditions of contract, so no deadline can be "
        "looked up. Set the contract edition on the project."
        if not edition
        else f"No notice requirements are registered for {edition}.",
    )


def _prefetched(queryset):
    from claimiq.claims.models import Evidence
    from claimiq.correspondence.models import Notice

    return queryset.select_related("project").prefetch_related(
        Prefetch(
            "notices",
            queryset=Notice.objects.select_related("correspondence__recipient"),
        ),
        Prefetch("document_links", queryset=ClaimDocument.objects.select_related("document")),
        Prefetch(
            "evidence",
            queryset=Evidence.objects.select_related("issue", "document"),
        ),
    )


def for_claim(claim: Claim) -> dict:
    loaded = _prefetched(Claim.objects.filter(pk=claim.pk)).first()
    return build(loaded)


def for_claims(claims: Sequence[Claim]) -> List[dict]:
    """Checklists for many claims, reading each project's deadlines once."""
    ids = [c.pk for c in claims]
    loaded = list(_prefetched(Claim.objects.filter(pk__in=ids)).order_by("project__name", "reference", "title"))
    by_project: Dict[str, tuple] = {}
    today = timezone.localdate()
    results = []
    for claim in loaded:
        key = str(claim.project_id)
        if key not in by_project:
            by_project[key] = requirements_for_project(claim.project)
        results.append(build(claim, project_requirements=by_project[key], today=today))
    return results
