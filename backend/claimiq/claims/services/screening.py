"""Assemble a claim for preliminary screening.

The rules live in :mod:`claimiq.claims.domain.screening` and know nothing about
Django. This reads the ORM and hands them a snapshot.

Computed on demand rather than stored, like evidence gaps and notice
compliance: a screening result that is written once goes stale the moment
someone records the missing date, and a stale "not assessable" is worse than
none. Nothing here writes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from django.utils import timezone

from claimiq.claims.domain.evidence_gaps import elements_for
from claimiq.core.domain.errors import NotFoundError
from claimiq.claims.domain.screening import ScreeningInput, ScreeningReport, screen_claim

if TYPE_CHECKING:  # pragma: no cover - import for typing only
    from claimiq.claims.models import Claim


def _clauses_found(edition_code: str, clauses: tuple) -> tuple:
    """Which of ``clauses`` appear in the published standard form.

    Looks only at published, non-quarantined passages for that edition — the
    same corpus an answer may cite. A clause present only in a quarantined
    passage is not citable, so reporting it as found would promise something
    retrieval will not deliver.
    """
    if not edition_code or not clauses:
        return ()

    from claimiq.knowledge.models import (
        KnowledgeBase,
        KnowledgeBaseChunk,
        KnowledgeBaseStatus,
    )

    published = KnowledgeBase.objects.filter(
        edition_code=edition_code, status=KnowledgeBaseStatus.PUBLISHED
    )
    if not published.exists():
        return ()

    found = set(
        KnowledgeBaseChunk.objects.filter(
            knowledge_base__in=published,
            is_quarantined=False,
            clause_number__in=list(clauses),
        ).values_list("clause_number", flat=True)
    )
    return tuple(c for c in clauses if c in found)


def _knowledge_base_published(edition_code: str) -> bool:
    if not edition_code:
        return False
    from claimiq.knowledge.models import KnowledgeBase, KnowledgeBaseStatus

    return KnowledgeBase.objects.filter(
        edition_code=edition_code, status=KnowledgeBaseStatus.PUBLISHED
    ).exists()


def _instruction_linked(claim: "Claim") -> bool:
    """Whether an instruction is on record for this claim.

    An instruction reaches a claim through a Notice asserted over the
    correspondence, which is the only link the model holds between a claim and
    a letter.
    """
    from claimiq.correspondence.models import CorrespondenceKind

    return claim.notices.filter(
        correspondence__kind__in=(
            CorrespondenceKind.INSTRUCTION,
            CorrespondenceKind.SITE_INSTRUCTION,
        ),
        correspondence__deleted_at__isnull=True,
    ).exists()


def build_input(claim: "Claim") -> ScreeningInput:
    """Read everything the checks need from the record."""
    from claimiq.analysis.services.claim_analysis import (
        compute_notice_timing,
        notice_events_for,
    )
    from claimiq.knowledge.domain.editions import DEFAULT_REGISTRY

    project = claim.project
    edition_code = project.contract_edition or ""
    edition_label = ""
    if edition_code:
        # An unregistered code is a data problem, not a reason to fail
        # screening: fall back to showing the raw code.
        try:
            edition_label = DEFAULT_REGISTRY.get_edition(edition_code).label
        except NotFoundError:
            edition_label = edition_code

    clauses = tuple(
        str(c).strip() for c in (claim.contractual_basis or []) if str(c).strip()
    )

    computation = compute_notice_timing(
        edition_code, claim.awareness_date, notice_events_for(claim)
    )

    return ScreeningInput(
        claim_type=claim.claim_type,
        today=timezone.localdate(),
        edition_code=edition_code,
        edition_label=edition_label,
        contractual_basis=clauses,
        clauses_found=_clauses_found(edition_code, clauses),
        knowledge_base_published=_knowledge_base_published(edition_code),
        has_claimant=claim.claimant_id is not None,
        has_respondent=claim.respondent_id is not None,
        notice_requirements_registered=bool(computation.findings),
        notice_findings=tuple(computation.findings),
        event_date=claim.event_date,
        awareness_date=claim.awareness_date,
        description=claim.description or "",
        instruction_linked=_instruction_linked(claim),
        evidence_count=claim.evidence.count(),
        document_count=claim.source_documents.count(),
        required_records=tuple(
            (element.label, tuple(element.typical_documents))
            for element in elements_for(claim.claim_type)
            if element.is_essential
        ),
        amount_claimed=claim.amount_claimed,
        currency=claim.currency or "",
        time_claimed_days=claim.time_claimed_days,
    )


def screen(claim: "Claim") -> ScreeningReport:
    """Screen a claim as it is recorded right now."""
    return screen_claim(build_input(claim))


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
