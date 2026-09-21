"""The claim analysis engine.

Runs a claim through its analysis strands (see
:mod:`claimiq.analysis.domain.strands`) and persists each result as it lands.

Structure:

- **Execution functions** (``compute_evidence_gaps``, ``compute_notice_timing``,
  ``run_model_strand``, ``run_notice_strand``) take plain inputs and return a
  :class:`StrandOutcome`. They touch no database, so the decisions that matter —
  how citations resolve, how confidence is capped, what survives a model failure
  — are tested without one.
- **The run loop** (``run_analysis``) reads rows, calls those functions, and
  writes results. It is resumable: a strand left running by a crashed worker is
  restarted, and completed strands are never repeated.

Failure is per strand. One strand failing grounding does not discard the others,
and a failed model step in the notice strand does not discard the computed
timing — the run is reported as partial, not silently successful.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

from claimiq.ai.domain.citations import EpistemicStatus, resolve_ref
from claimiq.analysis.domain.confidence import (
    Confidence,
    ConfidenceAssessment,
    GroundedInputs,
    assess_evidence_record,
    assess_grounded_answer,
    assess_notice_timing,
    combine,
)
from claimiq.analysis.domain.review import (
    ReviewAction,
    ReviewEntry,
    ReviewOutcome,
    resolve_review,
    validate_review,
)
from claimiq.analysis.domain.strands import (
    ClaimFacts,
    RunStatus,
    Strand,
    StrandPlan,
    StrandStatus,
    build_claim_summary,
    plan_analysis,
    progress_percent,
    summarise_run,
)
from claimiq.analysis.models import (
    ACTIVE_RUN_STATUSES,
    AIFinding,
    AnalysisStrandResult,
    ClaimAnalysis,
    FindingReview,
)
from claimiq.claims.domain.evidence_gaps import (
    EvidenceItem,
    GapReport,
    Relevance,
    analyse_gaps,
    element_code_for_issue_category,
)
from claimiq.claims.domain.notice_compliance import (
    ComplianceFinding,
    NoticeEvent,
    assess_notice,
    recipient_label,
    requirements_for_edition,
)
from claimiq.claims.models import Claim
from claimiq.core.domain.errors import ClaimIQError, ConflictError, ValidationError
from claimiq.core.logging import bind_context, get_logger
from claimiq.knowledge.domain.editions import DEFAULT_REGISTRY
from claimiq.search.domain.scope import RetrievalScope, combined_scope

logger = get_logger("analysis.engine")

#: An active run not updated for this long is treated as stalled and may be
#: re-dispatched. Comfortably longer than one model strand takes on a CPU.
STALE_AFTER = timedelta(minutes=30)

NOTICE_QUESTION = (
    "Was notice of this claim given as the contract requires, and what follows "
    "from the computed timing?"
)

#: Shown with every analysis. The engine assists professional judgement; it does
#: not replace it, and nothing it produces is a determination.
ADVISORY = (
    "This analysis is an aid to professional judgement, not a determination. "
    "Each finding is limited to the sources it cites, and a finding no one has "
    "reviewed has not been checked by a person."
)


# ---------------------------------------------------------------------------
# Outcome of one strand
# ---------------------------------------------------------------------------


@dataclass
class StrandOutcome:
    status: StrandStatus
    summary: str = ""
    insufficient_evidence: bool = False
    confidence: ConfidenceAssessment | None = None
    computed: dict[str, Any] = field(default_factory=dict)
    observability: dict[str, Any] = field(default_factory=dict)
    prompt_identifier: str = ""
    findings: list[dict[str, Any]] = field(default_factory=list)
    model_called: bool = False
    error_code: str = ""
    error_message: str = ""


# ---------------------------------------------------------------------------
# Claim facts: built once, snapshotted, reused for every strand
# ---------------------------------------------------------------------------


def facts_from_claim(claim: Claim) -> ClaimFacts:
    project = claim.project
    edition_code = project.contract_edition or ""
    if edition_code and DEFAULT_REGISTRY.has_edition(edition_code):
        edition_label = DEFAULT_REGISTRY.get_edition(edition_code).label
    else:
        edition_label = edition_code
    return ClaimFacts(
        claim_type=claim.claim_type,
        title=claim.title,
        reference=claim.reference,
        description=claim.description,
        contractual_basis=tuple(
            str(c).strip() for c in (claim.contractual_basis or ()) if str(c).strip()
        ),
        event_date=claim.event_date,
        awareness_date=claim.awareness_date,
        notice_date=claim.notice_date,
        submission_date=claim.submission_date,
        amount_claimed=claim.amount_claimed,
        currency=claim.currency,
        time_claimed_days=claim.time_claimed_days,
        claimant=claim.claimant.name if claim.claimant_id else "",
        respondent=claim.respondent.name if claim.respondent_id else "",
        edition_code=edition_code,
        edition_label=edition_label,
    )


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def facts_to_dict(facts: ClaimFacts) -> dict[str, Any]:
    return {
        "claim_type": facts.claim_type,
        "title": facts.title,
        "reference": facts.reference,
        "description": facts.description,
        "contractual_basis": list(facts.contractual_basis),
        "event_date": _iso(facts.event_date),
        "awareness_date": _iso(facts.awareness_date),
        "notice_date": _iso(facts.notice_date),
        "submission_date": _iso(facts.submission_date),
        "amount_claimed": (
            str(facts.amount_claimed) if facts.amount_claimed is not None else None
        ),
        "currency": facts.currency,
        "time_claimed_days": facts.time_claimed_days,
        "claimant": facts.claimant,
        "respondent": facts.respondent,
        "edition_code": facts.edition_code,
        "edition_label": facts.edition_label,
    }


def facts_from_dict(data: Mapping[str, Any]) -> ClaimFacts:
    def as_date(key: str) -> date | None:
        value = data.get(key)
        return date.fromisoformat(value) if value else None

    amount: Decimal | None = None
    raw_amount = data.get("amount_claimed")
    if raw_amount not in (None, ""):
        try:
            amount = Decimal(str(raw_amount))
        except InvalidOperation:
            amount = None

    days = data.get("time_claimed_days")
    return ClaimFacts(
        claim_type=str(data.get("claim_type") or "other"),
        title=str(data.get("title") or ""),
        reference=str(data.get("reference") or ""),
        description=str(data.get("description") or ""),
        contractual_basis=tuple(str(c) for c in (data.get("contractual_basis") or ())),
        event_date=as_date("event_date"),
        awareness_date=as_date("awareness_date"),
        notice_date=as_date("notice_date"),
        submission_date=as_date("submission_date"),
        amount_claimed=amount,
        currency=str(data.get("currency") or ""),
        time_claimed_days=int(days) if days is not None else None,
        claimant=str(data.get("claimant") or ""),
        respondent=str(data.get("respondent") or ""),
        edition_code=str(data.get("edition_code") or ""),
        edition_label=str(data.get("edition_label") or ""),
    )


def notice_clause_numbers_for(edition_code: str) -> list[str]:
    if not edition_code:
        return []
    return [r.clause_number for r in requirements_for_edition(edition_code)]


# ---------------------------------------------------------------------------
# Deterministic strands
# ---------------------------------------------------------------------------


def _relevance(value: str) -> Relevance:
    try:
        return Relevance(value)
    except ValueError:
        return Relevance.UNASSESSED


def gap_report_to_dict(report: GapReport) -> dict[str, Any]:
    return {
        "is_complete": report.is_complete,
        "completeness_ratio": round(report.completeness_ratio, 3),
        "elements": [
            {
                "code": a.element.code,
                "label": a.element.label,
                "is_essential": a.element.is_essential,
                "status": a.status.value,
                "is_gap": a.is_gap,
                "suggestion": a.suggestion(),
                "supporting": len(a.supporting),
                "contradicting": len(a.contradicting),
                "unreviewed": len(a.unreviewed),
            }
            for a in report.assessments
        ],
        "unmatched_evidence": [
            {"id": e.evidence_id, "title": e.title} for e in report.unmatched_evidence
        ],
    }


def compute_evidence_gaps(
    claim_type: str, items: Sequence[EvidenceItem]
) -> tuple[StrandOutcome, GapReport]:
    """The evidence-gaps strand. Arithmetic over recorded evidence; no model."""
    report = analyse_gaps(claim_type, items)
    unreviewed = sum(1 for item in items if not item.is_reviewed)
    outcome = StrandOutcome(
        status=StrandStatus.COMPLETED,
        summary=report.summary(),
        confidence=assess_evidence_record(len(items), unreviewed),
        computed=gap_report_to_dict(report),
    )
    return outcome, report


@dataclass
class NoticeComputation:
    findings: list[ComplianceFinding]
    computed: dict[str, Any]
    prompt_text: str
    summary_text: str
    confidence: ConfidenceAssessment


def compute_notice_timing(
    edition_code: str, awareness_date: date | None, notices: Sequence[NoticeEvent]
) -> NoticeComputation:
    """Deterministic notice timing for every requirement the edition registers.

    The model never recalculates this. It is handed ``prompt_text`` and asked
    only to explain what the computed result means.
    """
    requirements = requirements_for_edition(edition_code) if edition_code else ()
    findings = [
        assess_notice(requirement, awareness_date=awareness_date, notices=notices)
        for requirement in requirements
    ]

    computed = {
        "edition": edition_code,
        "awareness_date": _iso(awareness_date),
        "notices_considered": len(notices),
        "findings": [
            {
                "clause_number": f.requirement.clause_number,
                "description": f.requirement.description,
                "status": f.status.value,
                "summary": f.summary(),
                "deadline": _iso(f.deadline),
                "days_used": f.days_used,
                "days_late": f.days_late,
                "is_time_barred": f.is_time_barred,
                "is_condition_precedent": f.requirement.is_condition_precedent,
                "assumptions": list(f.assumptions),
                "warnings": list(f.warnings),
            }
            for f in findings
        ],
    }

    lines: list[str] = []
    for f in findings:
        lines.append(
            f"Clause {f.requirement.clause_number} ({f.requirement.description}): {f.summary()}"
        )
        lines.append(
            f"  Deadline: {f.deadline.isoformat() if f.deadline else 'cannot be computed'}"
        )
        for assumption in f.assumptions:
            lines.append(f"  Assumption: {assumption}")
        for warning in f.warnings:
            lines.append(f"  Caveat: {warning}")

    if findings:
        confidence = combine(
            [
                assess_notice_timing(f.status.value, assumptions=f.assumptions, warnings=f.warnings)
                for f in findings
            ]
        )
    else:
        confidence = ConfidenceAssessment(
            Confidence.LOW, ("No notice requirements are registered for this edition.",)
        )

    return NoticeComputation(
        findings=findings,
        computed=computed,
        prompt_text="\n".join(lines) or "No notice requirements apply.",
        summary_text="; ".join(f.summary() for f in findings),
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Model strands
# ---------------------------------------------------------------------------


def _citations_payload(finding, sources_by_ref: Mapping[str, Any]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for citation in finding.citations:
        ref = resolve_ref(citation.ref, sources_by_ref)
        source = sources_by_ref.get(ref) if ref else None
        payload.append(
            {
                "ref": ref or citation.ref,
                "quotation": citation.quotation,
                "rendered": source.render_citation() if source else None,
                "document_id": source.document_id if source else None,
                "document_title": source.document_title if source else None,
                "page_number": source.page_number if source else None,
                "clause_number": source.clause_number if source else None,
                "edition": source.edition if source else None,
                "is_knowledge_base": source.is_knowledge_base if source else None,
            }
        )
    return payload


def run_model_strand(
    plan: StrandPlan,
    facts: ClaimFacts,
    *,
    scope: RetrievalScope,
    answering,
    extra_variables: Mapping[str, Any] | None = None,
    evidence_status: str | None = None,
    retrieval_query: str | None = None,
) -> StrandOutcome:
    """Run one grounded model strand.

    A grounding or output failure is returned as a FAILED outcome rather than
    raised, so the run continues to the next strand. It is never converted into
    an answer.
    """
    variables: dict[str, Any] = {"claim_summary": build_claim_summary(facts)}
    variables.update(extra_variables or {})

    try:
        record = answering.ask(
            retrieval_query or plan.retrieval_query,
            scope,
            prompt_key=plan.prompt_key,
            extra_variables=variables,
        )
    except ClaimIQError as exc:
        logger.warning(
            "analysis.strand_failed",
            extra={"strand": plan.strand.value, "error_code": exc.code},
        )
        return StrandOutcome(
            status=StrandStatus.FAILED,
            prompt_identifier=plan.prompt_key or "",
            error_code=exc.code,
            error_message=exc.message,
            # Kept so a failure can be diagnosed afterwards. "The response
            # quoted text that does not appear in the cited source" is not
            # actionable without the quotation it objected to.
            observability={"failure": dict(exc.details or {})},
        )

    answer = record.answer
    sources_by_ref = {source.ref: source for source in record.sources}
    # With nothing retrieved the model is not called, and the "answer" is the
    # system explaining why. That is recorded as the strand summary, not stored
    # as a finding: a finding is something the AI asserted and a person reviews.
    findings = (
        [
            {
                "statement": finding.statement,
                "epistemic_status": finding.status.value,
                "citations": _citations_payload(finding, sources_by_ref),
            }
            for finding in answer.findings
        ]
        if record.called_model
        else []
    )

    confidence = assess_grounded_answer(
        GroundedInputs(
            insufficient_evidence=answer.insufficient_evidence or not record.called_model,
            cited_source_count=len(set(record.grounding.resolved_refs)),
            fact_findings=sum(1 for f in answer.findings if f.status is EpistemicStatus.FACT),
            unknown_findings=sum(
                1 for f in answer.findings if f.status is EpistemicStatus.UNKNOWN
            ),
            model_confidence=answer.confidence.value if record.called_model else None,
            evidence_status=evidence_status,
        )
    )

    return StrandOutcome(
        status=StrandStatus.COMPLETED,
        summary=answer.summary,
        insufficient_evidence=answer.insufficient_evidence,
        confidence=confidence,
        observability=record.as_observability_record(),
        prompt_identifier=record.prompt_identifier or (plan.prompt_key or ""),
        findings=findings,
        model_called=record.called_model,
    )


def run_notice_strand(
    plan: StrandPlan,
    facts: ClaimFacts,
    notices: Sequence[NoticeEvent],
    *,
    scope: RetrievalScope,
    answering,
    retrieval_query: str | None = None,
) -> StrandOutcome:
    """Compute notice timing, then ask the model to explain it.

    The computation is kept whatever happens to the explanation. If the model
    step fails, the strand is reported failed — the run is partial, not a
    success — but the computed timing and its confidence remain on the record.
    """
    computation = compute_notice_timing(facts.edition_code, facts.awareness_date, notices)

    outcome = run_model_strand(
        plan,
        facts,
        scope=scope,
        answering=answering,
        retrieval_query=retrieval_query,
        # Deliberately no evidence_status: the notice is on the record as
        # correspondence, and capping by untagged Evidence rows would mark a
        # notice that plainly exists as missing.
        extra_variables={
            "computed_finding": computation.prompt_text,
            "question": NOTICE_QUESTION,
        },
    )
    outcome.computed = computation.computed

    if outcome.status is StrandStatus.COMPLETED and outcome.model_called:
        outcome.confidence = combine([computation.confidence, outcome.confidence])
    elif outcome.status is StrandStatus.COMPLETED:
        # Nothing to explain it with. The timing stands on recorded dates alone,
        # so it keeps its own confidence, and the summary leads with it.
        outcome.confidence = ConfidenceAssessment(
            computation.confidence.level,
            computation.confidence.reasons
            + ("No contract sources were available to explain the computed timing.",),
        )
        outcome.summary = f"{computation.summary_text}. {outcome.summary}".strip()
    else:
        outcome.confidence = computation.confidence
        outcome.summary = outcome.summary or computation.summary_text
    return outcome


# ---------------------------------------------------------------------------
# Reading the claim's record
# ---------------------------------------------------------------------------


def evidence_items_for(claim: Claim) -> list[EvidenceItem]:
    return [
        EvidenceItem(
            evidence_id=str(e.id),
            title=e.title,
            element_code=element_code_for_issue_category(e.issue.category if e.issue_id else None),
            relevance=_relevance(e.relevance),
            weight=e.weight,
            document_type=e.document.document_type if e.document_id else None,
            is_reviewed=e.is_reviewed,
        )
        for e in claim.evidence.select_related("issue", "document")
    ]


def notice_events_for(claim: Claim) -> list[NoticeEvent]:
    events: list[NoticeEvent] = []
    notices = claim.notices.select_related("correspondence__recipient").filter(
        correspondence__deleted_at__isnull=True
    )
    for notice in notices:
        item = notice.correspondence
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


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------


def is_stalled(analysis, now=None) -> bool:
    if analysis.is_terminal:
        return False
    now = now or timezone.now()
    return analysis.updated_at is not None and now - analysis.updated_at > STALE_AFTER


@transaction.atomic
def start_analysis(
    claim: Claim, *, user, llm_model: str = "", embedding_model: str = ""
) -> tuple[ClaimAnalysis, bool]:
    """Create a run for ``claim``, or return its active run.

    Returns:
        ``(analysis, created)``. Asking twice for a run already in progress is
        not an error; it returns the same run.

    Raises:
        ValidationError: the project has not declared its governing edition.
            Refused rather than defaulted (ADR 0004).
    """
    project = claim.project
    if not project.contract_edition:
        raise ValidationError(
            "This project has not declared which conditions of contract govern "
            "it, so the claim cannot be analysed against them. Set the contract "
            "edition on the project first.",
            details={
                "project": str(project.pk),
                "available_editions": [e.code for e in DEFAULT_REGISTRY.editions()],
            },
        )

    existing = (
        ClaimAnalysis.objects.select_for_update()
        .filter(claim=claim, status__in=ACTIVE_RUN_STATUSES)
        .first()
    )
    if existing is not None:
        return existing, False

    facts = facts_from_claim(claim)
    plans = plan_analysis(facts, notice_clause_numbers=notice_clause_numbers_for(facts.edition_code))

    analysis = ClaimAnalysis.objects.create(
        project=project,
        claim=claim,
        requested_by=user,
        created_by=user,
        edition_code=facts.edition_code,
        llm_model=llm_model,
        embedding_model=embedding_model,
        claim_snapshot=facts_to_dict(facts),
        status=RunStatus.QUEUED.value,
    )
    AnalysisStrandResult.objects.bulk_create(
        [
            AnalysisStrandResult(
                analysis=analysis,
                strand=plan.strand.value,
                sequence=index,
                uses_model=plan.uses_model and plan.applies,
                status=(
                    StrandStatus.PENDING.value if plan.applies else StrandStatus.SKIPPED.value
                ),
                skip_reason=plan.skip_reason,
                prompt_identifier=plan.prompt_key or "",
                retrieval_query=plan.retrieval_query,
                finished_at=None if plan.applies else timezone.now(),
            )
            for index, plan in enumerate(plans)
        ]
    )
    analysis.progress_percent = progress_percent(
        StrandStatus.PENDING if p.applies else StrandStatus.SKIPPED for p in plans
    )
    analysis.save(update_fields=["progress_percent", "updated_at"])

    logger.info(
        "analysis.created",
        extra={
            "analysis_id": str(analysis.pk),
            "claim_id": str(claim.pk),
            "strands": sum(1 for p in plans if p.applies),
        },
    )
    return analysis, True


def request_cancellation(analysis: ClaimAnalysis) -> ClaimAnalysis:
    """Ask a run to stop before its next strand. A strand in progress finishes."""
    if analysis.is_terminal:
        raise ConflictError(
            "The analysis has already finished and cannot be cancelled.",
            details={"analysis_id": str(analysis.pk), "status": analysis.status},
        )
    update: dict[str, Any] = {"cancel_requested": True, "updated_at": timezone.now()}
    if analysis.status == RunStatus.QUEUED.value:
        update.update(status=RunStatus.CANCELLED.value, finished_at=timezone.now())
    ClaimAnalysis.objects.filter(pk=analysis.pk).update(**update)
    analysis.refresh_from_db()
    return analysis


def _element_status(report: GapReport | None, element_code: str | None) -> str | None:
    if report is None or element_code is None:
        return None
    for assessment in report.assessments:
        if assessment.element.code == element_code:
            return assessment.status.value
    return None


def _persist_outcome(
    row: AnalysisStrandResult, outcome: StrandOutcome, analysis: ClaimAnalysis, claim: Claim
) -> None:
    with transaction.atomic():
        # Idempotent: a strand re-run after a crash replaces any partial rows.
        AIFinding.objects.filter(strand_result=row).delete()

        row.status = outcome.status.value
        row.summary = outcome.summary
        row.insufficient_evidence = outcome.insufficient_evidence
        row.confidence = outcome.confidence.level.value if outcome.confidence else ""
        row.confidence_reasons = list(outcome.confidence.reasons) if outcome.confidence else []
        row.computed = outcome.computed
        row.observability = outcome.observability
        row.prompt_identifier = outcome.prompt_identifier or row.prompt_identifier
        row.error_code = outcome.error_code[:64]
        row.error_message = outcome.error_message
        row.finished_at = timezone.now()
        row.save()

        AIFinding.objects.bulk_create(
            [
                AIFinding(
                    analysis=analysis,
                    strand_result=row,
                    claim=claim,
                    sequence=index,
                    statement=finding["statement"],
                    epistemic_status=finding["epistemic_status"],
                    citations=finding["citations"],
                )
                for index, finding in enumerate(outcome.findings)
            ]
        )


def _strand_statuses(analysis: ClaimAnalysis) -> list[StrandStatus]:
    return [StrandStatus(s) for s in analysis.strands.values_list("status", flat=True)]


def run_analysis(analysis_id, *, answering_service=None) -> ClaimAnalysis:
    """Execute every pending strand of a run, persisting each as it completes."""
    analysis = ClaimAnalysis.objects.select_related("project", "claim").get(pk=analysis_id)
    if analysis.is_terminal:
        return analysis

    bind_context(analysis_id=str(analysis.pk), claim_id=str(analysis.claim_id))

    # Crash recovery. A strand left RUNNING by a dead worker, or by a development
    # server that reloaded mid-run, restarts from the beginning of that strand.
    analysis.strands.filter(status=StrandStatus.RUNNING.value).update(
        status=StrandStatus.PENDING.value, started_at=None
    )
    analysis.status = RunStatus.RUNNING.value
    analysis.started_at = analysis.started_at or timezone.now()
    analysis.save(update_fields=["status", "started_at", "updated_at"])

    facts = facts_from_dict(analysis.claim_snapshot)
    plans = {
        plan.strand: plan
        for plan in plan_analysis(
            facts, notice_clause_numbers=notice_clause_numbers_for(facts.edition_code)
        )
    }
    claim = analysis.claim
    project = analysis.project
    # Least privilege: a background run reads only this claim's project, whatever
    # else the requesting user could see.
    scope = combined_scope(
        organization_id=project.organization_id,
        project_id=project.pk,
        edition=analysis.edition_code,
        accessible_project_ids={project.pk},
    )

    answering = answering_service
    gap_report: GapReport | None = None
    cancelled = False

    for row in analysis.strands.order_by("sequence"):
        strand = Strand(row.strand)

        if (
            strand is Strand.EVIDENCE_GAPS
            and row.status == StrandStatus.COMPLETED.value
            and gap_report is None
        ):
            # Resumed run: rebuild the report so later strands are still capped by it.
            gap_report = analyse_gaps(facts.claim_type, evidence_items_for(claim))

        if row.status != StrandStatus.PENDING.value:
            continue

        if ClaimAnalysis.objects.filter(pk=analysis.pk, cancel_requested=True).exists():
            cancelled = True
            break

        row.status = StrandStatus.RUNNING.value
        row.started_at = timezone.now()
        row.save(update_fields=["status", "started_at", "updated_at"])
        bind_context(strand=strand.value)

        plan = plans[strand]
        try:
            if strand is Strand.EVIDENCE_GAPS:
                outcome, gap_report = compute_evidence_gaps(
                    facts.claim_type, evidence_items_for(claim)
                )
            else:
                if answering is None:
                    from claimiq.ai.services.answering import build_default_answering_service

                    answering = build_default_answering_service()
                if strand is Strand.NOTICE_COMPLIANCE:
                    outcome = run_notice_strand(
                        plan,
                        facts,
                        notice_events_for(claim),
                        scope=scope,
                        answering=answering,
                        retrieval_query=row.retrieval_query,
                    )
                else:
                    outcome = run_model_strand(
                        plan,
                        facts,
                        scope=scope,
                        answering=answering,
                        evidence_status=_element_status(gap_report, plan.element_code),
                        retrieval_query=row.retrieval_query,
                    )
        except ClaimIQError as exc:
            outcome = StrandOutcome(
                status=StrandStatus.FAILED, error_code=exc.code, error_message=exc.message
            )
        except Exception as exc:  # noqa: BLE001 - one strand must not take down the run
            logger.exception(
                "analysis.strand_crashed",
                extra={"strand": strand.value, "exception_type": type(exc).__name__},
            )
            outcome = StrandOutcome(
                status=StrandStatus.FAILED,
                error_code="internal_error",
                error_message=(
                    "An unexpected error occurred in this strand. It has been "
                    "logged for investigation."
                ),
            )

        _persist_outcome(row, outcome, analysis, claim)
        analysis.progress_percent = progress_percent(_strand_statuses(analysis))
        analysis.save(update_fields=["progress_percent", "updated_at"])
        logger.info(
            "analysis.strand_finished",
            extra={
                "strand": strand.value,
                "status": outcome.status.value,
                "confidence": outcome.confidence.level.value if outcome.confidence else None,
            },
        )

    statuses = _strand_statuses(analysis)
    analysis.status = (
        RunStatus.CANCELLED.value if cancelled else summarise_run(statuses).value
    )
    analysis.progress_percent = progress_percent(statuses)
    analysis.finished_at = timezone.now()
    if analysis.status == RunStatus.FAILED.value:
        first_failure = analysis.strands.filter(status=StrandStatus.FAILED.value).first()
        if first_failure is not None:
            analysis.error_code = first_failure.error_code
            analysis.error_message = first_failure.error_message
    analysis.save(
        update_fields=[
            "status", "progress_percent", "finished_at", "error_code",
            "error_message", "updated_at",
        ]
    )
    logger.info("analysis.finished", extra={"status": analysis.status})

    from claimiq.analysis.signals import analysis_finished

    analysis_finished.send(sender=ClaimAnalysis, analysis=analysis)
    return analysis


def _run_in_thread(analysis_id: str) -> None:
    close_old_connections()
    try:
        run_analysis(analysis_id)
    except Exception:  # noqa: BLE001 - a thread has no caller to report to
        logger.exception("analysis.thread_crashed", extra={"analysis_id": analysis_id})
        ClaimAnalysis.objects.filter(
            pk=analysis_id, status__in=ACTIVE_RUN_STATUSES
        ).update(
            status=RunStatus.FAILED.value,
            error_code="internal_error",
            error_message="The analysis stopped unexpectedly. It has been logged for investigation.",
            finished_at=timezone.now(),
        )
    finally:
        close_old_connections()


def dispatch_analysis(analysis: ClaimAnalysis) -> None:
    """Start a run without blocking the request that asked for it.

    With a broker, the run goes to the ``ai`` queue. In local development there
    is no broker and Celery runs tasks inline, which would hold the HTTP request
    open for the minutes a CPU takes to answer six prompts. A daemon thread
    keeps the request fast instead. A thread killed by a server reload leaves
    the run resumable; the next request for it re-dispatches once it is stale.
    """
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        threading.Thread(
            target=_run_in_thread,
            args=(str(analysis.pk),),
            name=f"claim-analysis-{analysis.pk}",
            daemon=True,
        ).start()
        return

    from claimiq.analysis.tasks import run_claim_analysis

    result = run_claim_analysis.apply_async(args=[str(analysis.pk)], queue="ai")
    ClaimAnalysis.objects.filter(pk=analysis.pk).update(celery_task_id=result.id or "")


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


def review_entries(reviews: Sequence[FindingReview]) -> list[ReviewEntry]:
    return [
        ReviewEntry(
            action=ReviewAction(review.action),
            reviewer_id=review.reviewer_email,
            reviewed_at=review.created_at,
            reason=review.reason,
            amended_statement=review.amended_statement,
        )
        for review in reviews
    ]


def review_outcome(finding: AIFinding, reviews: Sequence[FindingReview]) -> ReviewOutcome:
    return resolve_review(finding.statement, review_entries(reviews))


@transaction.atomic
def review_finding(
    finding: AIFinding,
    *,
    user,
    action: str,
    reason: str = "",
    amended_statement: str = "",
) -> FindingReview:
    """Record a person's decision on a finding. The finding itself is untouched."""
    parsed = validate_review(
        action,
        reason=reason,
        amended_statement=amended_statement,
        original_statement=finding.statement,
    )
    review = FindingReview.objects.create(
        finding=finding,
        action=parsed.value,
        reason=(reason or "").strip(),
        amended_statement=(amended_statement or "").strip() if parsed is ReviewAction.AMEND else "",
        reviewer=user,
        reviewer_email=user.email,
        created_by=user,
    )
    logger.info(
        "analysis.finding_reviewed",
        extra={"finding_id": str(finding.pk), "action": parsed.value},
    )
    return review
