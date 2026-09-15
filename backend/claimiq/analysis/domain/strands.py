"""Claim analysis plan: which strands run, in what order, with what inputs.

A claim is not one question, and it is not analysed with one prompt. Entitlement,
notice compliance, causation, time and money can each succeed or fail on their
own, and a single "is this claim valid?" call blends them into a verdict that
cannot say "entitled in principle, but notified late". The prototype did exactly
that: one validity prompt, one prose answer, a verdict scraped from it.

So analysis is split into strands. Two are deterministic and run first:

- **Evidence gaps** is arithmetic over the evidence on record.
- **Notice compliance** computes timing from recorded dates; a model is then
  asked only to explain the computed result, never to recalculate it.

The rest are grounded model calls, one strand per prompt, each validated
independently — so one strand failing grounding does not discard the others.

Pure stdlib; runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Iterable, Mapping, Sequence


class Strand(str, Enum):
    EVIDENCE_GAPS = "evidence_gaps"
    NOTICE_COMPLIANCE = "notice_compliance"
    ENTITLEMENT = "entitlement"
    CAUSATION = "causation"
    TIME_IMPACT = "time_impact"
    QUANTUM = "quantum"
    COUNTERARGUMENTS = "counterarguments"


#: Execution order. Deterministic strands lead: they are cheap, they cannot be
#: wrong about arithmetic, and a run that later fails on a model call still
#: leaves the computed results in place.
STRAND_ORDER: tuple[Strand, ...] = (
    Strand.EVIDENCE_GAPS,
    Strand.NOTICE_COMPLIANCE,
    Strand.ENTITLEMENT,
    Strand.CAUSATION,
    Strand.TIME_IMPACT,
    Strand.QUANTUM,
    Strand.COUNTERARGUMENTS,
)

STRAND_LABELS: Mapping[Strand, str] = {
    Strand.EVIDENCE_GAPS: "Evidence gaps",
    Strand.NOTICE_COMPLIANCE: "Notice compliance",
    Strand.ENTITLEMENT: "Contractual entitlement",
    Strand.CAUSATION: "Causation",
    Strand.TIME_IMPACT: "Time impact",
    Strand.QUANTUM: "Quantum",
    Strand.COUNTERARGUMENTS: "Counterarguments",
}

#: Strands answered by a model. The others are computed.
MODEL_STRANDS: frozenset[Strand] = frozenset(
    {
        Strand.NOTICE_COMPLIANCE,
        Strand.ENTITLEMENT,
        Strand.CAUSATION,
        Strand.TIME_IMPACT,
        Strand.QUANTUM,
        Strand.COUNTERARGUMENTS,
    }
)

PROMPT_FOR: Mapping[Strand, str] = {
    Strand.NOTICE_COMPLIANCE: "notice_compliance",
    Strand.ENTITLEMENT: "entitlement_analysis",
    Strand.CAUSATION: "causation_analysis",
    Strand.TIME_IMPACT: "time_impact_analysis",
    Strand.QUANTUM: "quantum_analysis",
    Strand.COUNTERARGUMENTS: "counterarguments_analysis",
}

#: The evidence element each strand bears on, from
#: :mod:`claimiq.claims.domain.evidence_gaps`. Used to cap a strand's confidence
#: by what the record actually holds for that element.
STRAND_ELEMENT: Mapping[Strand, str] = {
    Strand.NOTICE_COMPLIANCE: "notice",
    Strand.ENTITLEMENT: "responsibility",
    Strand.CAUSATION: "causation",
    Strand.TIME_IMPACT: "time_impact",
    Strand.QUANTUM: "quantum",
}

#: Claim types whose substance is time.
TIME_CLAIM_TYPES = frozenset(
    {"eot", "delay", "disruption", "acceleration", "compensation_event"}
)

#: Claim types whose substance is money.
MONEY_CLAIM_TYPES = frozenset(
    {"cost", "variation", "disruption", "acceleration", "payment", "compensation_event"}
)

CLAIM_TYPE_LABELS: Mapping[str, str] = {
    "eot": "Extension of Time",
    "variation": "Variation",
    "cost": "Additional Cost",
    "delay": "Delay",
    "disruption": "Disruption",
    "acceleration": "Acceleration",
    "payment": "Payment",
    "compensation_event": "Compensation Event",
    "other": "Other",
}

#: Topic words for each strand's retrieval query.
#:
#: Chosen with care, because the query is classified before it is searched: a
#: word such as "evidence", "records" or "supporting" moves a question to the
#: evidence-search intent, which excludes the knowledge base entirely — and an
#: entitlement analysis that never sees the conditions of contract is worthless.
#: A test asserts no strand's query is classified that way.
_TOPIC_TERMS: Mapping[Strand, str] = {
    Strand.NOTICE_COMPLIANCE: "notice of claim period time bar",
    Strand.ENTITLEMENT: "entitlement claim contractual basis",
    Strand.CAUSATION: "cause of delay event effect",
    Strand.TIME_IMPACT: "extension of time for completion delay",
    Strand.QUANTUM: "cost valuation payment amount",
    Strand.COUNTERARGUMENTS: "grounds to reject claim conditions",
}

#: Claim descriptions are free text written by the claimant. Beyond this the
#: prompt budget is better spent on sources.
MAX_DESCRIPTION_CHARS = 1500


class StrandStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    """Not applicable to this claim. Distinct from COMPLETED: a quantum strand
    skipped because no money is claimed is not a quantum analysis that found
    nothing."""

    FAILED = "failed"


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    """Some strands failed and the rest completed. The completed strands stand;
    the failures are reported, not hidden behind an overall success."""

    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STRAND_STATUSES = frozenset(
    {StrandStatus.COMPLETED, StrandStatus.SKIPPED, StrandStatus.FAILED}
)


@dataclass(frozen=True)
class ClaimFacts:
    """What the record says about a claim, and nothing more.

    Framework-free snapshot built from the Claim row. Everything a model sees
    about the claim comes from here, so a value absent from the record is
    absent from the prompt — rather than inferred, defaulted, or invented.
    """

    claim_type: str
    title: str
    reference: str = ""
    description: str = ""
    contractual_basis: tuple[str, ...] = ()
    event_date: date | None = None
    awareness_date: date | None = None
    notice_date: date | None = None
    submission_date: date | None = None
    amount_claimed: Decimal | None = None
    currency: str = ""
    time_claimed_days: int | None = None
    claimant: str = ""
    respondent: str = ""
    edition_code: str = ""
    edition_label: str = ""


@dataclass(frozen=True)
class StrandPlan:
    """What will happen for one strand."""

    strand: Strand
    label: str
    applies: bool
    uses_model: bool
    prompt_key: str | None
    retrieval_query: str
    element_code: str | None
    skip_reason: str = ""


def retrieval_query_for(
    strand: Strand, facts: ClaimFacts, extra_clauses: Sequence[str] = ()
) -> str:
    """The retrieval query for a strand.

    Built from topic words, the claim type and the clause numbers relied on.
    Clause numbers are written as ``Clause 20.2.1`` so exact clause lookup runs
    rather than similarity alone.

    The claim's title and description are deliberately left out. They are
    claimant-authored free text, and a single word in them can reclassify the
    query and silently drop the knowledge base from the search.
    """
    if strand not in _TOPIC_TERMS:
        return ""
    clauses: list[str] = []
    for number in tuple(facts.contractual_basis) + tuple(extra_clauses):
        number = number.strip()
        if number and number not in clauses:
            clauses.append(number)
    parts = [
        _TOPIC_TERMS[strand],
        CLAIM_TYPE_LABELS.get(facts.claim_type, "").lower(),
        " ".join(f"Clause {n}" for n in clauses),
    ]
    return " ".join(p for p in parts if p).strip()


def plan_analysis(
    facts: ClaimFacts, *, notice_clause_numbers: Sequence[str] = ()
) -> list[StrandPlan]:
    """Decide which strands apply to a claim.

    Args:
        facts: The claim as recorded.
        notice_clause_numbers: Clauses imposing notice requirements under the
            governing edition. Empty when none are registered — in which case
            notice compliance is skipped rather than computed against another
            edition's periods.

    Returns:
        One plan per strand in :data:`STRAND_ORDER`, including those that do not
        apply, each with the reason. A strand that silently vanishes from the
        output reads as an analysis that forgot to look.
    """
    plans: list[StrandPlan] = []

    for strand in STRAND_ORDER:
        applies = True
        skip_reason = ""
        extra: Sequence[str] = ()

        if strand is Strand.NOTICE_COMPLIANCE:
            extra = notice_clause_numbers
            if not facts.edition_code:
                applies = False
                skip_reason = (
                    "The project has not declared its governing conditions of "
                    "contract, so notice periods cannot be determined."
                )
            elif not notice_clause_numbers:
                applies = False
                skip_reason = (
                    f"No notice requirements are registered for "
                    f"{facts.edition_label or facts.edition_code}. Periods from "
                    f"another edition are deliberately not substituted."
                )
        elif strand is Strand.TIME_IMPACT:
            if facts.claim_type not in TIME_CLAIM_TYPES and facts.time_claimed_days is None:
                applies = False
                skip_reason = "The claim type is not time-related and no time is claimed."
        elif strand is Strand.QUANTUM:
            if facts.claim_type not in MONEY_CLAIM_TYPES and facts.amount_claimed is None:
                applies = False
                skip_reason = "No amount is claimed and the claim type does not seek money."

        uses_model = strand in MODEL_STRANDS
        plans.append(
            StrandPlan(
                strand=strand,
                label=STRAND_LABELS[strand],
                applies=applies,
                uses_model=uses_model,
                prompt_key=PROMPT_FOR.get(strand),
                retrieval_query=retrieval_query_for(strand, facts, extra) if uses_model else "",
                element_code=STRAND_ELEMENT.get(strand),
                skip_reason=skip_reason,
            )
        )

    return plans


def _render_date(value: date | None, *, note: str = "") -> str:
    if value is None:
        return f"not recorded{f' ({note})' if note else ''}"
    return value.isoformat()


def build_claim_summary(facts: ClaimFacts) -> str:
    """Render the claim as the model will see it.

    Every field is stated, including the missing ones, as "not recorded". A
    model given an incomplete summary tends to fill the gaps with plausible
    values; one told explicitly that a value is absent can say so instead.
    """
    if facts.amount_claimed is None:
        amount = "not recorded"
    else:
        amount = f"{facts.currency} {format(facts.amount_claimed, ',.2f')}".strip()

    if facts.time_claimed_days is None:
        time_claimed = "not recorded"
    else:
        time_claimed = f"{facts.time_claimed_days} days"

    basis = ", ".join(f"Clause {c}" for c in facts.contractual_basis if c) or "none recorded"

    description = facts.description.strip()
    if len(description) > MAX_DESCRIPTION_CHARS:
        cut = description[:MAX_DESCRIPTION_CHARS].rsplit(" ", 1)[0]
        description = f"{cut} [...truncated]"

    lines = [
        f"Reference: {facts.reference or 'not recorded'}",
        f"Title: {facts.title}",
        f"Type: {CLAIM_TYPE_LABELS.get(facts.claim_type, facts.claim_type)}",
        f"Claimant: {facts.claimant or 'not recorded'}",
        f"Respondent: {facts.respondent or 'not recorded'}",
        f"Contractual basis relied on: {basis}",
        f"Event date: {_render_date(facts.event_date)}",
        f"Date of awareness: {_render_date(facts.awareness_date, note='commonly disputed')}",
        f"Notice date: {_render_date(facts.notice_date)}",
        f"Submission date: {_render_date(facts.submission_date)}",
        f"Amount claimed: {amount}",
        f"Time claimed: {time_claimed}",
        f"Governing conditions: {facts.edition_label or facts.edition_code or 'not declared'}",
        f"Description: {description or 'not recorded'}",
    ]
    return "\n".join(lines)


def summarise_run(statuses: Iterable[StrandStatus]) -> RunStatus:
    """Overall status of a run from its strand statuses."""
    values = list(statuses)
    if not values:
        return RunStatus.COMPLETED
    if all(s is StrandStatus.PENDING for s in values):
        return RunStatus.QUEUED
    if any(s not in TERMINAL_STRAND_STATUSES for s in values):
        return RunStatus.RUNNING

    applicable = [s for s in values if s is not StrandStatus.SKIPPED]
    failed = sum(1 for s in applicable if s is StrandStatus.FAILED)
    if not applicable or failed == 0:
        return RunStatus.COMPLETED
    if failed == len(applicable):
        return RunStatus.FAILED
    return RunStatus.PARTIAL


def progress_percent(statuses: Iterable[StrandStatus]) -> int:
    values = list(statuses)
    if not values:
        return 100
    done = sum(1 for s in values if s in TERMINAL_STRAND_STATUSES)
    return int(round(100 * done / len(values)))
