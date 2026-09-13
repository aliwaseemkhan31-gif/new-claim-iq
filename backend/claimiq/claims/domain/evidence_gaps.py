"""Evidence gap analysis.

Answers "what is missing" rather than "what do we have" — which is the harder
and more useful question, and the one a claim consultant is actually paid for.

The approach is deliberately not to ask a model "what evidence is missing?".
That invites it to invent plausible-sounding gaps. Instead the elements a claim
type must establish are declared as data, the evidence on record is matched
against them, and what remains unmatched is the gap. The model's role, if any,
is to explain a gap the arithmetic already found.

Pure stdlib; runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping, Sequence


class ElementStatus(str, Enum):
    ESTABLISHED = "established"
    PARTIAL = "partial"
    """Some supporting evidence, but not enough to carry the element."""

    CONTESTED = "contested"
    """Supported and contradicted. Not a gap — a dispute, and more urgent than
    a gap because it will be argued rather than merely queried."""

    MISSING = "missing"


class Relevance(str, Enum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    NEUTRAL = "neutral"
    UNASSESSED = "unassessed"


@dataclass(frozen=True)
class RequiredElement:
    """Something a claim of a given type must establish."""

    code: str
    label: str
    description: str
    is_essential: bool = True
    """False for elements that strengthen a claim without being necessary."""

    typical_documents: tuple[str, ...] = ()
    """Document-type codes that usually evidence this. Used to say what would
    close the gap, rather than only that one exists."""


@dataclass(frozen=True)
class EvidenceItem:
    """A piece of evidence on the record."""

    evidence_id: str
    title: str
    element_code: str | None
    relevance: Relevance
    weight: str = ""
    document_type: str | None = None
    is_reviewed: bool = False


@dataclass
class ElementAssessment:
    element: RequiredElement
    status: ElementStatus
    supporting: list[EvidenceItem] = field(default_factory=list)
    contradicting: list[EvidenceItem] = field(default_factory=list)
    unreviewed: list[EvidenceItem] = field(default_factory=list)

    @property
    def is_gap(self) -> bool:
        return self.status in (ElementStatus.MISSING, ElementStatus.PARTIAL)

    def suggestion(self) -> str:
        """What would close this gap, where that can be said concretely."""
        if self.status is ElementStatus.ESTABLISHED:
            return ""
        if self.status is ElementStatus.CONTESTED:
            return (
                f"{self.element.label} is both supported and contradicted on the "
                f"record. This is a dispute to resolve, not a document to find."
            )
        if self.element.typical_documents:
            readable = ", ".join(d.replace("_", " ") for d in self.element.typical_documents)
            return f"{self.element.label} is usually evidenced by: {readable}."
        return f"No evidence on record establishes {self.element.label.lower()}."


@dataclass
class GapReport:
    claim_type: str
    assessments: list[ElementAssessment] = field(default_factory=list)
    unmatched_evidence: list[EvidenceItem] = field(default_factory=list)

    @property
    def essential_gaps(self) -> list[ElementAssessment]:
        return [a for a in self.assessments if a.is_gap and a.element.is_essential]

    @property
    def contested(self) -> list[ElementAssessment]:
        return [a for a in self.assessments if a.status is ElementStatus.CONTESTED]

    @property
    def is_complete(self) -> bool:
        """Whether every essential element is established.

        Deliberately not "is the claim good". Completeness of the evidential
        record is a different question from the merits, and conflating them is
        how a well-documented weak claim gets read as a strong one.
        """
        return not self.essential_gaps

    @property
    def completeness_ratio(self) -> float:
        essential = [a for a in self.assessments if a.element.is_essential]
        if not essential:
            return 1.0
        established = sum(1 for a in essential if a.status is ElementStatus.ESTABLISHED)
        return established / len(essential)

    def summary(self) -> str:
        return (
            f"{self.claim_type}: {self.completeness_ratio:.0%} of essential elements "
            f"established, {len(self.essential_gaps)} gap(s), "
            f"{len(self.contested)} contested"
        )


# ---------------------------------------------------------------------------
# Required elements by claim type
#
# Declared as data so the analysis is inspectable and correctable. A user who
# disagrees that a given element is essential can see exactly what the system
# expected and why a gap was reported.
# ---------------------------------------------------------------------------

_EVENT = RequiredElement(
    code="event",
    label="The triggering event",
    description="That the event or circumstance relied on actually occurred.",
    typical_documents=("daily_report", "site_record", "photograph", "meeting_minutes"),
)

_NOTICE = RequiredElement(
    code="notice",
    label="Notice given in time",
    description="That the required Notice was given, to the right recipient, within the period.",
    typical_documents=("notice", "letter", "email"),
)

_CAUSATION = RequiredElement(
    code="causation",
    label="Causation",
    description="That the event caused the effect claimed, not merely that both occurred.",
    typical_documents=("programme", "updated_programme", "progress_report", "daily_report"),
)

_RESPONSIBILITY = RequiredElement(
    code="responsibility",
    label="Responsibility",
    description="That the event is one the respondent bears the risk of under the contract.",
    typical_documents=("conditions_of_contract", "particular_conditions", "engineers_instruction"),
)

_TIME_IMPACT = RequiredElement(
    code="time_impact",
    label="Time impact",
    description="That the event delayed a critical activity, and by how much.",
    typical_documents=("programme", "updated_programme"),
)

_COST_IMPACT = RequiredElement(
    code="cost_impact",
    label="Cost incurred",
    description="That the cost claimed was actually incurred.",
    typical_documents=("invoice", "payment_record", "timesheet", "measurement_sheet"),
)

_QUANTUM = RequiredElement(
    code="quantum",
    label="Quantification",
    description="That the amount claimed is calculated on a supportable basis.",
    typical_documents=("measurement_sheet", "bill_of_quantities", "invoice"),
)

_MITIGATION = RequiredElement(
    code="mitigation",
    label="Mitigation",
    description="That reasonable steps were taken to mitigate the effect.",
    is_essential=False,
    typical_documents=("meeting_minutes", "letter", "progress_report"),
)

_INSTRUCTION = RequiredElement(
    code="instruction",
    label="The instruction",
    description="That a valid instruction was issued by someone with authority.",
    typical_documents=("engineers_instruction", "site_instruction", "letter"),
)

_COMMON = (_EVENT, _NOTICE, _CAUSATION, _RESPONSIBILITY)

REQUIRED_ELEMENTS: Mapping[str, tuple[RequiredElement, ...]] = {
    "eot": _COMMON + (_TIME_IMPACT, _MITIGATION),
    "delay": _COMMON + (_TIME_IMPACT, _MITIGATION),
    "disruption": _COMMON + (_COST_IMPACT, _QUANTUM, _MITIGATION),
    "acceleration": _COMMON + (_INSTRUCTION, _COST_IMPACT, _QUANTUM),
    "variation": (_INSTRUCTION, _NOTICE, _RESPONSIBILITY, _COST_IMPACT, _QUANTUM),
    "cost": _COMMON + (_COST_IMPACT, _QUANTUM, _MITIGATION),
    "payment": (_NOTICE, _QUANTUM, _COST_IMPACT),
    "compensation_event": _COMMON + (_TIME_IMPACT, _COST_IMPACT, _QUANTUM),
    "other": _COMMON,
}


def elements_for(claim_type: str) -> tuple[RequiredElement, ...]:
    """Required elements for a claim type, falling back to the common set."""
    return REQUIRED_ELEMENTS.get(claim_type, REQUIRED_ELEMENTS["other"])


def assess_element(
    element: RequiredElement, evidence: Sequence[EvidenceItem]
) -> ElementAssessment:
    """Assess one element against the evidence attached to it."""
    supporting = [e for e in evidence if e.relevance is Relevance.SUPPORTS]
    contradicting = [e for e in evidence if e.relevance is Relevance.CONTRADICTS]
    unreviewed = [e for e in evidence if e.relevance is Relevance.UNASSESSED]

    if supporting and contradicting:
        status = ElementStatus.CONTESTED
    elif supporting:
        # Reviewed supporting evidence establishes the element. Unreviewed
        # evidence is only a candidate — treating it as established would let
        # an unchecked AI suggestion close a gap on its own.
        reviewed = [e for e in supporting if e.is_reviewed]
        status = ElementStatus.ESTABLISHED if reviewed else ElementStatus.PARTIAL
    elif unreviewed:
        status = ElementStatus.PARTIAL
    else:
        status = ElementStatus.MISSING

    return ElementAssessment(
        element=element,
        status=status,
        supporting=supporting,
        contradicting=contradicting,
        unreviewed=unreviewed,
    )


def analyse_gaps(claim_type: str, evidence: Iterable[EvidenceItem]) -> GapReport:
    """Assess a claim's evidential completeness.

    Args:
        claim_type: The claim's type code.
        evidence: Evidence on record, each optionally tagged with the element
            it addresses.

    Returns:
        A report naming what is established, partial, contested and missing.
        Evidence tagged with no recognised element is reported separately
        rather than discarded — it may be relevant to something the element
        list does not model, and silently dropping it would hide that.
    """
    items = list(evidence)
    by_element: dict[str, list[EvidenceItem]] = {}
    for item in items:
        if item.element_code:
            by_element.setdefault(item.element_code, []).append(item)

    elements = elements_for(claim_type)
    known_codes = {e.code for e in elements}

    assessments = [
        assess_element(element, by_element.get(element.code, [])) for element in elements
    ]

    unmatched = [
        item
        for item in items
        if item.element_code is None or item.element_code not in known_codes
    ]

    return GapReport(
        claim_type=claim_type, assessments=assessments, unmatched_evidence=unmatched
    )
