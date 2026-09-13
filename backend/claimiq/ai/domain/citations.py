"""Citation and grounding validation.

Implements the enforcement half of ADR 0005. The prompt asks the model not to
fabricate; this module checks whether it did. An instruction without a check is
a hope, and the legacy prototype had only the instruction
(`../backend/rag.py:63`).

Four checks, each independent:

1. **Closed-world identifiers.** Sources are presented as ``S1``…``Sn``,
   assigned at assembly time. A citation is a dictionary lookup, not a string to
   be parsed, so ``S47`` against eight sources is caught immediately.
2. **Quotation verification.** Any verbatim quotation must actually occur in the
   chunk it is attributed to, compared after whitespace and typography
   normalisation.
3. **Fact grounding.** A finding asserted as ``FACT`` must carry a citation.
4. **Edition agreement.** Every cited knowledge-base source must match the
   scope's edition (ADR 0004), re-checked here so a dropped filter upstream
   cannot reach the user.

Pure stdlib; runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Container, Iterable, Mapping, Sequence

from claimiq.core.domain.errors import (
    UncitedFactError,
    UnknownCitationError,
    UnsupportedQuotationError,
)


class EpistemicStatus(str, Enum):
    """How a finding relates to the evidence.

    Modelled explicitly because the requirement is that the system distinguish
    fact from inference from opinion, and never convert uncertainty into
    certainty. If the model can only emit prose, that distinction is rhetorical.
    As a typed field it is checkable — and ``UNKNOWN`` gives the model a valid
    way to decline rather than forcing it into a shape that only fits an answer.
    """

    FACT = "fact"
    """Directly supported by cited source text. Citation required."""

    INFERENCE = "inference"
    """Reasoned from cited facts. Citation required for the underlying facts."""

    OPINION = "opinion"
    """Professional judgement. Not presented as established."""

    UNKNOWN = "unknown"
    """The available sources do not establish this. A valid, expected outcome."""


@dataclass(frozen=True)
class SourceChunk:
    """A retrieved chunk offered to the model as citable evidence.

    Attributes:
        ref: Opaque identifier shown to the model, e.g. ``S1``.
        text: Exact source text. Quotations are verified against this.
        document_id: Owning document.
        document_title: Display title used when rendering the citation.
        page_number: Page for citation display and viewer navigation.
        clause_number: Clause, when the chunk belongs to one.
        edition: Knowledge-base edition, or None for project documents.
        is_knowledge_base: True for standard-form text.
    """

    ref: str
    text: str
    document_id: str
    document_title: str
    page_number: int
    clause_number: str | None = None
    edition: str | None = None
    is_knowledge_base: bool = False

    def render_citation(self) -> str:
        """Human-readable citation.

        Knowledge-base citations always name the edition, so a reader can never
        mistake which contract form a provision came from — the display half of
        ADR 0004.
        """
        parts = [self.document_title]
        if self.is_knowledge_base and self.edition:
            parts = [self.edition]
        if self.clause_number:
            parts.append(f"Clause {self.clause_number}")
        parts.append(f"p.{self.page_number}")
        return "[" + " — ".join(parts) + "]"


@dataclass(frozen=True)
class Citation:
    """A reference from generated content back to a source."""

    ref: str
    quotation: str | None = None
    """Verbatim text the model attributes to the source. Verified when present."""


@dataclass(frozen=True)
class Finding:
    """One assertion in a structured AI response."""

    statement: str
    status: EpistemicStatus
    citations: tuple[Citation, ...] = ()
    confidence: float | None = None

    def requires_citation(self) -> bool:
        return self.status in (EpistemicStatus.FACT, EpistemicStatus.INFERENCE)


@dataclass
class GroundingReport:
    """Result of validating a response against its sources."""

    findings_checked: int = 0
    citations_checked: int = 0
    quotations_verified: int = 0
    unknown_refs: list[str] = field(default_factory=list)
    unsupported_quotations: list[tuple[str, str]] = field(default_factory=list)
    uncited_facts: list[str] = field(default_factory=list)
    edition_mismatches: list[tuple[str, str]] = field(default_factory=list)
    unused_sources: list[str] = field(default_factory=list)
    resolved_refs: list[str] = field(default_factory=list)
    """Canonical identifiers of the sources actually cited.

    Distinct from the raw strings the model supplied, which may be decorated
    ("SOURCE ID: S1"). Downstream code must use these — matching a raw ref
    against a source's identifier silently finds nothing.
    """

    @property
    def is_grounded(self) -> bool:
        return not (
            self.unknown_refs
            or self.unsupported_quotations
            or self.uncited_facts
            or self.edition_mismatches
        )

    def summary(self) -> str:
        if self.is_grounded:
            return (
                f"grounded: {self.findings_checked} finding(s), "
                f"{self.citations_checked} citation(s), "
                f"{self.quotations_verified} quotation(s) verified"
            )
        return (
            f"NOT grounded: {len(self.unknown_refs)} unknown ref(s), "
            f"{len(self.unsupported_quotations)} unsupported quotation(s), "
            f"{len(self.uncited_facts)} uncited fact(s), "
            f"{len(self.edition_mismatches)} edition mismatch(es)"
        )


#: Minimum length for a quotation to be worth verifying. Very short fragments
#: ("the Notice") occur everywhere and verifying them proves nothing.
MIN_QUOTATION_CHARS = 12

_WS_RE = re.compile(r"\s+")

#: Typographic variants that differ between a PDF and a model's output while
#: representing the same character.
_CHAR_FOLDING = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "–": "-", "—": "-", "―": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", " ": " ",
    "…": "...",
}


def normalise_for_comparison(text: str) -> str:
    """Fold text for quotation matching.

    Normalises Unicode, folds smart quotes and dashes to ASCII, collapses
    whitespace and lowercases. This is deliberately lenient about *typography*
    and strict about *words*: a model that reproduces a provision with a
    straight apostrophe has not fabricated anything, but a model that changes
    "shall" to "may" has.
    """
    folded = unicodedata.normalize("NFKC", text)
    for source, target in _CHAR_FOLDING.items():
        folded = folded.replace(source, target)
    return _WS_RE.sub(" ", folded).strip().lower()


def verify_quotation(quotation: str, source_text: str) -> bool:
    """True if ``quotation`` occurs in ``source_text`` after normalisation.

    Quotations shorter than :data:`MIN_QUOTATION_CHARS` are accepted without
    verification; they carry too little information for a match to be evidence
    of anything.
    """
    cleaned = quotation.strip().strip("\"'“”")
    if len(cleaned) < MIN_QUOTATION_CHARS:
        return True
    return normalise_for_comparison(cleaned) in normalise_for_comparison(source_text)


#: A source identifier: a short letter prefix and a number, e.g. ``S1``.
_REF_TOKEN_RE = re.compile(r"\b([A-Za-z]{1,3}\d{1,3})\b")


def resolve_ref(raw: str, known: Container[str]) -> str | None:
    """Resolve a model-supplied reference to a known source identifier.

    Models decorate references unpredictably. Two forms observed from
    qwen2.5:3b-instruct against the same prompt, on successive runs:

        "S1 [FIDIC Red Book 2017 - Clause 20.2.1 - p.121]"
        "SOURCE ID: S1"

    Both plainly mean ``S1``. Rejecting them as fabricated citations would be a
    false positive that discards a perfectly good answer.

    Resolution is deliberately **closed-world**: candidate tokens are extracted
    from the string and each is checked against ``known``. A token is returned
    only if it is a source that was actually assembled. So this cannot invent a
    reference — the strongest thing it can do is recognise a real one wearing a
    label. ``S9`` against eight sources still resolves to ``None``, decorated
    or not.

    Ambiguity fails closed. If the string contains two *different* known
    identifiers, the intent is genuinely unclear and ``None`` is returned so
    the citation is reported rather than guessed at.

    Returns:
        The resolved identifier, or ``None`` when it cannot be resolved.
    """
    if not raw:
        return None

    text = raw.strip()
    if text in known:
        return text

    upper = text.upper()
    if upper in known:
        return upper

    matches: list[str] = []
    for token in _REF_TOKEN_RE.findall(text):
        candidate = token.upper()
        if candidate in known and candidate not in matches:
            matches.append(candidate)

    if len(matches) == 1:
        return matches[0]
    return None


def build_source_map(chunks: Sequence[SourceChunk]) -> dict[str, SourceChunk]:
    """Index sources by their opaque reference."""
    mapping: dict[str, SourceChunk] = {}
    for chunk in chunks:
        if chunk.ref in mapping:
            raise ValueError(f"Duplicate source ref: {chunk.ref!r}")
        mapping[chunk.ref] = chunk
    return mapping


def assign_refs(chunks: Iterable[SourceChunk], prefix: str = "S") -> list[SourceChunk]:
    """Re-assign sequential opaque refs (``S1``, ``S2``, …).

    Called at context-assembly time. The model never sees database identifiers,
    which keeps the citation space closed and small enough that an invented
    reference is obvious.
    """
    out: list[SourceChunk] = []
    for index, chunk in enumerate(chunks, start=1):
        out.append(
            SourceChunk(
                ref=f"{prefix}{index}",
                text=chunk.text,
                document_id=chunk.document_id,
                document_title=chunk.document_title,
                page_number=chunk.page_number,
                clause_number=chunk.clause_number,
                edition=chunk.edition,
                is_knowledge_base=chunk.is_knowledge_base,
            )
        )
    return out


def validate_findings(
    findings: Sequence[Finding],
    sources: Mapping[str, SourceChunk],
    *,
    expected_edition: str | None = None,
) -> GroundingReport:
    """Validate findings against the sources that were actually retrieved.

    Collects every problem rather than stopping at the first, so an operator
    sees the full picture in AI observability instead of one issue at a time.

    Args:
        findings: Structured findings from the model.
        sources: Assembled sources, keyed by opaque ref.
        expected_edition: Edition the scope was restricted to. When set, every
            cited knowledge-base source must match it.

    Returns:
        A report. Callers use :func:`enforce_grounding` to turn it into an error.
    """
    report = GroundingReport(findings_checked=len(findings))
    cited_refs: set[str] = set()

    for finding in findings:
        if finding.requires_citation() and not finding.citations:
            report.uncited_facts.append(finding.statement)

        for citation in finding.citations:
            report.citations_checked += 1
            ref = resolve_ref(citation.ref, sources)
            source = sources.get(ref) if ref is not None else None

            if source is None:
                report.unknown_refs.append(citation.ref)
                continue

            cited_refs.add(ref)

            if (
                expected_edition is not None
                and source.is_knowledge_base
                and source.edition
                and source.edition != expected_edition
            ):
                report.edition_mismatches.append((citation.ref, source.edition))

            if citation.quotation:
                if verify_quotation(citation.quotation, source.text):
                    report.quotations_verified += 1
                else:
                    report.unsupported_quotations.append(
                        (citation.ref, citation.quotation)
                    )

    report.resolved_refs = sorted(cited_refs)
    report.unused_sources = [ref for ref in sources if ref not in cited_refs]
    return report


def enforce_grounding(report: GroundingReport) -> None:
    """Raise the appropriate typed error if ``report`` is not grounded.

    Ordered most-severe first: an invented reference is a harder failure than a
    missing citation, and the error surfaced should be the worst one present.
    """
    if report.unknown_refs:
        raise UnknownCitationError(
            "The response cited sources that were not retrieved.",
            details={
                "unknown_refs": sorted(set(report.unknown_refs)),
                "explanation": (
                    "Citations must reference an assembled source identifier. An "
                    "identifier outside that set indicates a fabricated citation."
                ),
            },
        )

    if report.unsupported_quotations:
        raise UnsupportedQuotationError(
            "The response quoted text that does not appear in the cited source.",
            details={
                "quotations": [
                    {"ref": ref, "quotation": quote[:200]}
                    for ref, quote in report.unsupported_quotations
                ],
            },
        )

    if report.edition_mismatches:
        # Imported here to keep the module's import surface honest: this is an
        # edition-scope failure, not a grounding failure.
        from claimiq.core.domain.errors import EditionMixingError

        raise EditionMixingError(
            "The response cited knowledge-base sources from outside the scoped edition.",
            details={
                "mismatches": [
                    {"ref": ref, "edition": edition}
                    for ref, edition in report.edition_mismatches
                ]
            },
        )

    if report.uncited_facts:
        raise UncitedFactError(
            "The response asserted facts without citing a source.",
            details={
                "statements": [s[:200] for s in report.uncited_facts],
                "explanation": (
                    "Findings typed FACT or INFERENCE must cite the source that "
                    "establishes them. Use status UNKNOWN when the sources are "
                    "insufficient."
                ),
            },
        )


def render_sources_block(chunks: Sequence[SourceChunk]) -> str:
    """Render assembled sources for inclusion in a prompt.

    The identifier is put on its own labelled line, separated from the
    human-readable citation. An earlier layout placed them adjacent —
    ``S1 [FIDIC Red Book 2017 — Clause 20.2.1 — p.121]`` — and a real model
    cited the entire string as the identifier, because nothing in that line
    said where the token ended. Labelling each part removes the ambiguity at
    source rather than relying on :func:`resolve_ref` to clean up after it.
    """
    blocks: list[str] = []
    for chunk in chunks:
        blocks.append(
            f"SOURCE ID: {chunk.ref}\n"
            f"Reference: {chunk.render_citation()}\n"
            f"Text:\n{chunk.text.strip()}"
        )
    return "\n\n---\n\n".join(blocks)
