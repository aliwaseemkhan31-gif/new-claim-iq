"""Confidence for analysis strands, derived from checkable facts.

Every AI conclusion must carry a confidence indicator. The easy implementation
is to ask the model and display its answer. That is the wrong one: models are
reliably overconfident, and a "high confidence" label on a finding that rests on
one paragraph of one document is exactly the kind of false certainty this
product must not produce.

So confidence is computed from things that can be verified — whether the answer
was grounded, how many sources it actually cites, whether any finding is
established directly by a source, what the evidence record holds for the
element in question — and the model's self-reported confidence may only
*lower* the result, never raise it. Every cap records its reason, so a reader
can see why a strand is rated as it is.

Pure stdlib; runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Sequence


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def rank(self) -> int:
        return {"low": 0, "medium": 1, "high": 2}[self.value]


def lowest(levels: Iterable[Confidence]) -> Confidence:
    """The least confident of ``levels``."""
    values = list(levels)
    if not values:
        raise ValueError("lowest() needs at least one confidence level")
    return min(values, key=lambda level: level.rank)


@dataclass(frozen=True)
class ConfidenceAssessment:
    level: Confidence
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class GroundedInputs:
    """Facts about a grounded model answer that bear on confidence.

    Attributes:
        insufficient_evidence: The answer reported that the sources do not
            answer the question.
        cited_source_count: Distinct sources actually cited — not merely
            retrieved. Retrieving ten sources and citing one is one source.
        fact_findings: Findings typed FACT, each of which grounding validation
            has already required to carry a citation.
        unknown_findings: Findings typed UNKNOWN.
        model_confidence: The model's own rating, if it gave one.
        evidence_status: Status of the related element in the evidence gap
            analysis (``established``/``partial``/``contested``/``missing``), or
            None when no element maps to the strand.
    """

    insufficient_evidence: bool
    cited_source_count: int
    fact_findings: int
    unknown_findings: int
    model_confidence: str | None = None
    evidence_status: str | None = None


def _parse(value: str | None) -> Confidence | None:
    try:
        return Confidence(value) if value else None
    except ValueError:
        return None


class _Ceiling:
    def __init__(self) -> None:
        self.level = Confidence.HIGH
        self.reasons: list[str] = []

    def cap(self, level: Confidence, reason: str) -> None:
        if level.rank < self.level.rank:
            self.level = level
        self.reasons.append(reason)

    def result(self) -> ConfidenceAssessment:
        return ConfidenceAssessment(self.level, tuple(self.reasons))


def assess_grounded_answer(inputs: GroundedInputs) -> ConfidenceAssessment:
    """Confidence for a strand answered by a grounded model call."""
    ceiling = _Ceiling()

    if inputs.insufficient_evidence:
        ceiling.cap(Confidence.LOW, "The sources did not answer this question.")
        return ceiling.result()

    if inputs.cited_source_count == 0:
        ceiling.cap(Confidence.LOW, "No source was cited.")
    elif inputs.cited_source_count == 1:
        ceiling.cap(Confidence.MEDIUM, "The findings rest on a single source.")

    if inputs.fact_findings == 0:
        ceiling.cap(
            Confidence.MEDIUM,
            "No finding is established directly by a source; all are inference or opinion.",
        )

    if inputs.unknown_findings > 0:
        ceiling.cap(
            Confidence.MEDIUM,
            f"{inputs.unknown_findings} part(s) of the question are unresolved on the sources.",
        )

    if inputs.evidence_status == "missing":
        ceiling.cap(Confidence.LOW, "The record holds no evidence for this element.")
    elif inputs.evidence_status == "partial":
        ceiling.cap(Confidence.MEDIUM, "Evidence for this element is partial or unreviewed.")
    elif inputs.evidence_status == "contested":
        ceiling.cap(
            Confidence.MEDIUM, "The record both supports and contradicts this element."
        )

    model = _parse(inputs.model_confidence)
    if model is not None:
        if model.rank < ceiling.level.rank:
            ceiling.cap(model, f"The model rated its own answer {model.value}.")
        elif model.rank > ceiling.level.rank:
            # Recorded, not applied. The model may lower confidence, never raise it.
            ceiling.reasons.append(
                f"The model rated its own answer {model.value}; the checks above "
                f"hold it at {ceiling.level.value}."
            )

    return ceiling.result()


def assess_notice_timing(
    status: str,
    *,
    assumptions: Sequence[str] = (),
    warnings: Sequence[str] = (),
) -> ConfidenceAssessment:
    """Confidence in a computed notice-compliance result.

    The arithmetic itself is certain. What limits confidence is its inputs: an
    unknown awareness date, a receipt date assumed from a despatch date, a
    notice nobody has confirmed satisfies the provision.
    """
    ceiling = _Ceiling()

    if status == "indeterminate":
        ceiling.cap(
            Confidence.LOW,
            "Timing could not be computed: the date time ran from, or the notice "
            "itself, is not established on the record.",
        )
        return ceiling.result()

    if status == "not_required":
        ceiling.reasons.append("No notice was required.")
        return ceiling.result()

    if status == "not_given":
        ceiling.cap(
            Confidence.MEDIUM,
            "No dated notice was identified. Absence from the recorded "
            "correspondence is not proof that none was given.",
        )
    elif status in ("compliant", "late"):
        ceiling.reasons.append("Computed from recorded dates.")
    else:
        ceiling.cap(Confidence.LOW, f"Unrecognised timing status {status!r}.")
        return ceiling.result()

    if assumptions:
        ceiling.cap(Confidence.MEDIUM, f"Computed on an assumption: {assumptions[0]}")
    if warnings:
        ceiling.cap(
            Confidence.MEDIUM,
            f"Subject to {len(warnings)} caveat(s), including: {warnings[0]}",
        )

    return ceiling.result()


def assess_evidence_record(total_evidence: int, unreviewed: int) -> ConfidenceAssessment:
    """Confidence in a computed evidence-gap report.

    The report is exact about what is recorded against the claim. What limits
    confidence is whether that record is complete and checked.
    """
    ceiling = _Ceiling()
    ceiling.reasons.append(
        "Computed from evidence linked to this claim; evidence held elsewhere is not counted."
    )
    if total_evidence == 0:
        ceiling.cap(
            Confidence.MEDIUM,
            "No evidence is linked to this claim yet, so every element reads as missing.",
        )
    elif unreviewed > 0:
        ceiling.cap(
            Confidence.MEDIUM,
            f"{unreviewed} item(s) of evidence have not been reviewed by a person.",
        )
    return ceiling.result()


def combine(assessments: Sequence[ConfidenceAssessment]) -> ConfidenceAssessment:
    """The least confident of several assessments, with all their reasons."""
    if not assessments:
        raise ValueError("combine() needs at least one assessment")
    reasons: list[str] = []
    for assessment in assessments:
        for reason in assessment.reasons:
            if reason not in reasons:
                reasons.append(reason)
    return ConfidenceAssessment(lowest(a.level for a in assessments), tuple(reasons))
