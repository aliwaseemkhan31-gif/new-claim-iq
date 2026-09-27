"""Assemble a claim for preliminary screening.

The rules live in :mod:`claimiq.claims.domain.screening` and know nothing about
Django. This reads the ORM and hands them a snapshot.

Computed on demand rather than stored, like evidence gaps and notice
compliance: a screening result written once goes stale the moment someone
records the missing date, and a stale "not assessable" is worse than none.
Nothing here writes.

Two entry points. :func:`screen` does one claim and costs about seven queries.
:func:`screen_many` does a page of them for a register, and costs a handful in
total however many claims it is given — a register that screened each row on
its own would spend three seconds and a hundred and seventy queries rendering
twenty-five rows.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Iterable, List, Sequence, Tuple

from django.db.models import Count, Prefetch
from django.utils import timezone

from claimiq.claims.domain.evidence_gaps import elements_for
from claimiq.claims.domain.notice_compliance import (
    NoticeEvent,
    assess_notice,
    recipient_label,
    requirements_for_edition,
)
from claimiq.claims.domain.screening import ScreeningInput, ScreeningReport, screen_claim
from claimiq.core.domain.errors import NotFoundError

if TYPE_CHECKING:  # pragma: no cover - import for typing only
    from claimiq.claims.models import Claim

#: Correspondence kinds that count as an instruction for EV5.
_INSTRUCTION_KINDS = ("instruction", "site_instruction")


class _EditionFacts:
    """Per-edition lookups, cached across a batch.

    Every claim on a project shares an edition, so looking the standard form up
    once per claim is the difference between five queries for a register page
    and fifty.
    """

    def __init__(self) -> None:
        self._published: Dict[str, bool] = {}
        self._clauses: Dict[str, frozenset] = {}
        self._labels: Dict[str, str] = {}

    def label(self, edition_code: str) -> str:
        if not edition_code:
            return ""
        if edition_code not in self._labels:
            from claimiq.knowledge.domain.editions import DEFAULT_REGISTRY

            try:
                self._labels[edition_code] = DEFAULT_REGISTRY.get_edition(
                    edition_code
                ).label
            except NotFoundError:
                # An unregistered code is a data problem, not a reason to fail
                # screening: show the raw code.
                self._labels[edition_code] = edition_code
        return self._labels[edition_code]

    def is_published(self, edition_code: str) -> bool:
        if not edition_code:
            return False
        if edition_code not in self._published:
            from claimiq.knowledge.models import KnowledgeBase, KnowledgeBaseStatus

            self._published[edition_code] = KnowledgeBase.objects.filter(
                edition_code=edition_code, status=KnowledgeBaseStatus.PUBLISHED
            ).exists()
        return self._published[edition_code]

    def clauses(self, edition_code: str) -> frozenset:
        """Clause numbers citable from the published standard form.

        Published and non-quarantined only — the same corpus an answer may
        cite. A clause present only in a quarantined passage is not citable, so
        reporting it as found would promise something retrieval will not
        deliver.
        """
        if not edition_code or not self.is_published(edition_code):
            return frozenset()
        if edition_code not in self._clauses:
            from claimiq.knowledge.models import (
                KnowledgeBase,
                KnowledgeBaseChunk,
                KnowledgeBaseStatus,
            )

            self._clauses[edition_code] = frozenset(
                KnowledgeBaseChunk.objects.filter(
                    knowledge_base__in=KnowledgeBase.objects.filter(
                        edition_code=edition_code, status=KnowledgeBaseStatus.PUBLISHED
                    ),
                    is_quarantined=False,
                )
                .exclude(clause_number="")
                .values_list("clause_number", flat=True)
                .distinct()
            )
        return self._clauses[edition_code]


def _notice_events(notices: Iterable) -> List[NoticeEvent]:
    """Build notice events from already-loaded Notice rows.

    Takes rows rather than a claim so the same code serves one claim and a
    prefetched page of them.
    """
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
            )
        )
    return events


def _build(
    claim: "Claim",
    *,
    notices: Sequence,
    evidence_count: int,
    document_count: int,
    editions: _EditionFacts,
) -> ScreeningInput:
    """Assemble the snapshot from data already loaded."""
    edition_code = claim.project.contract_edition or ""
    clauses = tuple(
        str(c).strip() for c in (claim.contractual_basis or []) if str(c).strip()
    )
    found = editions.clauses(edition_code)

    requirements = requirements_for_edition(edition_code) if edition_code else ()
    events = _notice_events(notices)
    findings = tuple(
        assess_notice(requirement, awareness_date=claim.awareness_date, notices=events)
        for requirement in requirements
    )

    return ScreeningInput(
        claim_type=claim.claim_type,
        today=timezone.localdate(),
        edition_code=edition_code,
        edition_label=editions.label(edition_code),
        contractual_basis=clauses,
        clauses_found=tuple(c for c in clauses if c in found),
        knowledge_base_published=editions.is_published(edition_code),
        has_claimant=claim.claimant_id is not None,
        has_respondent=claim.respondent_id is not None,
        notice_requirements_registered=bool(requirements),
        notice_findings=findings,
        event_date=claim.event_date,
        awareness_date=claim.awareness_date,
        description=claim.description or "",
        instruction_linked=any(
            n.correspondence.kind in _INSTRUCTION_KINDS
            and n.correspondence.deleted_at is None
            for n in notices
        ),
        evidence_count=evidence_count,
        document_count=document_count,
        required_records=tuple(
            (element.label, tuple(element.typical_documents))
            for element in elements_for(claim.claim_type)
            if element.is_essential
        ),
        amount_claimed=claim.amount_claimed,
        currency=claim.currency or "",
        time_claimed_days=claim.time_claimed_days,
    )


def build_input(claim: "Claim") -> ScreeningInput:
    """Read everything the checks need for one claim."""
    from claimiq.correspondence.models import Notice

    notices = list(
        Notice.objects.filter(claim=claim).select_related(
            "correspondence__recipient"
        )
    )
    return _build(
        claim,
        notices=notices,
        evidence_count=claim.evidence.count(),
        document_count=claim.source_documents.count(),
        editions=_EditionFacts(),
    )


def screen(claim: "Claim") -> ScreeningReport:
    """Screen a claim as it is recorded right now."""
    return screen_claim(build_input(claim))


def screen_many(claims: Sequence["Claim"]) -> "Dict[str, ScreeningReport]":
    """Screen a page of claims in a bounded number of queries.

    Args:
        claims: Claims to screen. Re-fetched here with everything the checks
            need, so the caller need not know what to prefetch.

    Returns:
        Report by claim id, as a string. Claims that no longer exist are
        omitted rather than raising: a register row that vanished mid-request
        is not an error.
    """
    from claimiq.claims.models import Claim
    from claimiq.correspondence.models import Notice

    ids = [c.pk for c in claims]
    if not ids:
        return {}

    loaded = (
        Claim.objects.filter(pk__in=ids)
        .select_related("project")
        .annotate(
            screening_evidence_count=Count("evidence", distinct=True),
            screening_document_count=Count("source_documents", distinct=True),
        )
        .prefetch_related(
            Prefetch(
                "notices",
                queryset=Notice.objects.select_related("correspondence__recipient"),
            )
        )
    )

    editions = _EditionFacts()
    reports: Dict[str, ScreeningReport] = {}
    for claim in loaded:
        data = _build(
            claim,
            notices=list(claim.notices.all()),
            evidence_count=claim.screening_evidence_count,
            document_count=claim.screening_document_count,
            editions=editions,
        )
        reports[str(claim.pk)] = screen_claim(data)
    return reports


def payload(report: ScreeningReport) -> dict:
    """Render a report for the API.

    Includes every check, satisfied ones too: a reader needs to see what was
    looked at, not only what failed.
    """
    from claimiq.claims.domain.screening import (
        AREA_LABELS,
        OUTCOME_CAVEAT,
        OUTCOME_LABELS,
        Area,
    )

    return {
        "claim_type": report.claim_type,
        "outcome": report.outcome.value,
        "outcome_label": OUTCOME_LABELS[report.outcome],
        "summary": report.summary(),
        "caveat": OUTCOME_CAVEAT,
        "blocking_outstanding": len(report.blocking_outstanding),
        "advisory_outstanding": len(report.advisory_outstanding),
        "areas": [
            {
                "code": area.value,
                "label": AREA_LABELS[area],
                # Area-level counts so the UI can show five states at a glance
                # rather than making a reader count twenty-five rows.
                "blocking_outstanding": sum(
                    1
                    for r in report.for_area(area)
                    if r.is_outstanding and r.check.weight.value == "blocking"
                ),
                "outstanding": sum(
                    1 for r in report.for_area(area) if r.is_outstanding
                ),
                "barred": sum(
                    1 for r in report.for_area(area) if r.status.value == "barred"
                ),
                "checks": [
                    {
                        "code": result.check.code,
                        "question": result.check.question,
                        "why": result.check.why,
                        "weight": result.check.weight.value,
                        "status": result.status.value,
                        "detail": result.detail,
                        "items": list(result.items),
                        "remedy": result.check.remedy,
                    }
                    for result in report.for_area(area)
                ],
            }
            for area in Area
        ],
    }


def summary_payload(report: ScreeningReport) -> dict:
    """The outcome alone, for a register row or a header chip."""
    from claimiq.claims.domain.screening import OUTCOME_LABELS

    return {
        "outcome": report.outcome.value,
        "outcome_label": OUTCOME_LABELS[report.outcome],
        "summary": report.summary(),
        "blocking_outstanding": len(report.blocking_outstanding),
        "advisory_outstanding": len(report.advisory_outstanding),
    }
