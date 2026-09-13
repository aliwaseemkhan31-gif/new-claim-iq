"""Knowledge-base validation.

The legacy prototype discovered, after the fact, that its 2017 knowledge base
contained 422 chunks of contents pages, front matter, guidance notes and sample
forms. They were removed by hand, and the deleted identifiers were committed as
a text file (`../backend/deleted_2017_guidance_ids.txt`).

That episode is the requirement. Contamination is not a one-off event to be
cleaned manually; it is a property of PDF ingestion that recurs with every
document. So validation is:

- **automated** — a rule set, not a person reading chunks;
- **continuous** — run on every ingest, not once;
- **blocking** — `ERROR` findings prevent a knowledge base going live, rather
  than being noticed months later.

Why contamination is dangerous rather than merely untidy: a contents line reads
as "20.2.1 Notice of Claim 121". Retrieved and passed to a model as context, it
looks like an authoritative statement about Sub-Clause 20.2.1 while carrying no
provision text at all. Guidance notes are worse — they discuss what a clause
*could* say, in language that closely resembles what it *does* say.

Pure stdlib; runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence

from claimiq.ingestion.domain.clause_detection import (
    is_contents_line,
    normalise_clause_number,
)


class Severity(str, Enum):
    ERROR = "error"
    """Blocks publication. The knowledge base is not fit to answer questions."""

    WARNING = "warning"
    """Degrades quality. Publication is allowed but the finding is surfaced."""

    INFO = "info"
    """Observation for the operator. No action implied."""


@dataclass(frozen=True)
class KBChunk:
    """A knowledge-base chunk presented for validation.

    Mirrors the persisted model but is framework-free so the rules stay
    unit-testable.
    """

    chunk_id: str
    text: str
    edition: str
    page_number: int
    clause_number: str | None = None
    source_document: str | None = None

    def normalised_text(self) -> str:
        return re.sub(r"\s+", " ", self.text).strip().lower()

    def content_hash(self) -> str:
        return hashlib.sha256(self.normalised_text().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ValidationIssue:
    rule: str
    severity: Severity
    message: str
    chunk_ids: tuple[str, ...] = ()
    details: dict[str, object] = field(default_factory=dict)


@dataclass
class ValidationReport:
    """Outcome of a validation run."""

    edition: str
    total_chunks: int
    issues: list[ValidationIssue] = field(default_factory=list)
    stats: dict[str, object] = field(default_factory=dict)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]

    @property
    def is_publishable(self) -> bool:
        """True when no ERROR finding is present."""
        return not self.errors

    def contaminated_chunk_ids(self) -> tuple[str, ...]:
        """Chunk ids implicated in any contamination rule.

        The quarantine list. Equivalent to what the prototype produced by hand
        as ``deleted_2017_guidance_ids.txt``, generated automatically.
        """
        contamination_rules = {
            "contents_contamination",
            "guidance_text",
            "running_header_footer",
            "malformed_chunk",
        }
        out: list[str] = []
        for issue in self.issues:
            if issue.rule in contamination_rules:
                for chunk_id in issue.chunk_ids:
                    if chunk_id not in out:
                        out.append(chunk_id)
        return tuple(out)

    def summary(self) -> str:
        return (
            f"{self.edition}: {self.total_chunks} chunks, "
            f"{len(self.errors)} error(s), {len(self.warnings)} warning(s), "
            f"{len(self.contaminated_chunk_ids())} quarantined"
        )


# --------------------------------------------------------------------------
# Rule thresholds
# --------------------------------------------------------------------------

#: Share of a chunk's lines that must look like contents entries to flag it.
CONTENTS_LINE_RATIO = 0.4
CONTENTS_MIN_LINES = 2

#: Repeats of identical short text before it is treated as a running header.
RUNNING_TEXT_MIN_REPEATS = 5
RUNNING_TEXT_MAX_CHARS = 120

#: Minimum characters for a chunk to carry usable meaning.
MIN_CHUNK_CHARS = 60

#: Minimum share of alphabetic characters. Below this a chunk is page furniture.
MIN_ALPHA_RATIO = 0.55

#: Phrases marking commentary about the conditions rather than the conditions.
_GUIDANCE_MARKERS = (
    "guidance for the preparation",
    "notes on the preparation",
    "note:",
    "the following example",
    "example sub-clause",
    "example wording",
    "sample form",
    "specimen",
    "if the employer wishes",
    "the employer may wish to",
    "parties may wish to",
    "it is recommended that",
    "this guidance",
    "for guidance",
    "checklist",
)

#: Front/back-matter section titles.
_FRONT_MATTER_MARKERS = (
    "table of contents",
    "index",
    "foreword",
    "acknowledgements",
    "publication list",
    "forms of securities",
    "letter of tender",
    "appendix to tender",
)


def _line_count(text: str) -> int:
    return len([ln for ln in text.splitlines() if ln.strip()])


def check_contents_contamination(chunks: Sequence[KBChunk]) -> list[ValidationIssue]:
    """Flag chunks that are contents-page entries rather than provision text."""
    flagged: list[str] = []
    for chunk in chunks:
        lines = [ln for ln in chunk.text.splitlines() if ln.strip()]
        if len(lines) < CONTENTS_MIN_LINES:
            # A single contents line still counts if it is unmistakable.
            if lines and is_contents_line(lines[0]) and len(chunk.text) < 200:
                flagged.append(chunk.chunk_id)
            continue
        hits = sum(1 for ln in lines if is_contents_line(ln))
        if hits / len(lines) >= CONTENTS_LINE_RATIO:
            flagged.append(chunk.chunk_id)

    if not flagged:
        return []
    return [
        ValidationIssue(
            rule="contents_contamination",
            severity=Severity.ERROR,
            message=(
                f"{len(flagged)} chunk(s) are table-of-contents entries, not "
                f"provision text. Retrieved as context these resemble authoritative "
                f"statements about a clause while containing none of its text."
            ),
            chunk_ids=tuple(flagged),
            details={"count": len(flagged)},
        )
    ]


def check_guidance_text(chunks: Sequence[KBChunk]) -> list[ValidationIssue]:
    """Flag guidance/commentary that is not the conditions themselves."""
    flagged: list[str] = []
    for chunk in chunks:
        low = chunk.normalised_text()
        if any(marker in low for marker in _GUIDANCE_MARKERS):
            flagged.append(chunk.chunk_id)
            continue
        if any(low.startswith(marker) for marker in _FRONT_MATTER_MARKERS):
            flagged.append(chunk.chunk_id)

    if not flagged:
        return []
    return [
        ValidationIssue(
            rule="guidance_text",
            severity=Severity.ERROR,
            message=(
                f"{len(flagged)} chunk(s) are guidance notes, sample forms or front "
                f"matter. These discuss what a clause could say in language "
                f"resembling what it does say, and must not be retrievable as the "
                f"conditions."
            ),
            chunk_ids=tuple(flagged),
            details={"count": len(flagged)},
        )
    ]


def check_duplicates(chunks: Sequence[KBChunk]) -> list[ValidationIssue]:
    """Flag chunks whose normalised text is identical.

    Duplicates distort retrieval: the same passage occupies several of the top-k
    slots, crowding out other relevant provisions.
    """
    by_hash: dict[str, list[str]] = defaultdict(list)
    for chunk in chunks:
        if len(chunk.normalised_text()) >= MIN_CHUNK_CHARS:
            by_hash[chunk.content_hash()].append(chunk.chunk_id)

    groups = {h: ids for h, ids in by_hash.items() if len(ids) > 1}
    if not groups:
        return []

    duplicate_ids: list[str] = []
    for ids in groups.values():
        duplicate_ids.extend(ids[1:])  # keep the first occurrence

    return [
        ValidationIssue(
            rule="duplicate_content",
            severity=Severity.WARNING,
            message=(
                f"{len(groups)} group(s) of identical chunks found "
                f"({len(duplicate_ids)} redundant). Duplicates consume top-k slots "
                f"and crowd out other relevant provisions."
            ),
            chunk_ids=tuple(duplicate_ids),
            details={"groups": len(groups), "redundant": len(duplicate_ids)},
        )
    ]


def check_running_headers(chunks: Sequence[KBChunk]) -> list[ValidationIssue]:
    """Flag short text repeated across many pages — headers and footers."""
    counter: Counter[str] = Counter()
    owners: dict[str, list[str]] = defaultdict(list)

    for chunk in chunks:
        normalised = chunk.normalised_text()
        if 0 < len(normalised) <= RUNNING_TEXT_MAX_CHARS:
            counter[normalised] += 1
            owners[normalised].append(chunk.chunk_id)

    flagged: list[str] = []
    repeated = 0
    for text, count in counter.items():
        if count >= RUNNING_TEXT_MIN_REPEATS:
            repeated += 1
            flagged.extend(owners[text])

    if not flagged:
        return []
    return [
        ValidationIssue(
            rule="running_header_footer",
            severity=Severity.WARNING,
            message=(
                f"{repeated} distinct short text(s) repeat across {len(flagged)} "
                f"chunks and are probably running headers or footers."
            ),
            chunk_ids=tuple(flagged),
            details={"distinct": repeated},
        )
    ]


def check_malformed(chunks: Sequence[KBChunk]) -> list[ValidationIssue]:
    """Flag chunks too short or too non-textual to carry meaning."""
    flagged: list[str] = []
    for chunk in chunks:
        stripped = chunk.text.strip()
        if len(stripped) < MIN_CHUNK_CHARS:
            flagged.append(chunk.chunk_id)
            continue
        alpha = sum(1 for ch in stripped if ch.isalpha())
        if alpha / len(stripped) < MIN_ALPHA_RATIO:
            flagged.append(chunk.chunk_id)

    if not flagged:
        return []
    return [
        ValidationIssue(
            rule="malformed_chunk",
            severity=Severity.WARNING,
            message=(
                f"{len(flagged)} chunk(s) are too short or contain too little text "
                f"to be useful (page numbers, artefacts, table fragments)."
            ),
            chunk_ids=tuple(flagged),
            details={"count": len(flagged)},
        )
    ]


def check_edition_consistency(
    chunks: Sequence[KBChunk], expected_edition: str
) -> list[ValidationIssue]:
    """Every chunk must carry the expected edition.

    A single stray chunk is enough to produce a wrong contractual answer, so
    this is an ERROR rather than a warning. See ADR 0004.
    """
    offenders = [c.chunk_id for c in chunks if c.edition != expected_edition]
    if not offenders:
        return []
    found = sorted({c.edition for c in chunks if c.edition != expected_edition})
    return [
        ValidationIssue(
            rule="edition_mixing",
            severity=Severity.ERROR,
            message=(
                f"{len(offenders)} chunk(s) are tagged with a different edition than "
                f"{expected_edition!r}. Mixed editions produce confidently wrong "
                f"contractual answers."
            ),
            chunk_ids=tuple(offenders),
            details={"expected": expected_edition, "found": found},
        )
    ]


def check_clause_coverage(
    chunks: Sequence[KBChunk],
    expected_clauses: Iterable[str],
    absent_clauses: Iterable[str] = (),
) -> list[ValidationIssue]:
    """Report expected top-level clauses with no chunk.

    ``absent_clauses`` are numbers known not to exist in the edition — the 1987
    reprint has no Clause 26 — so a genuine gap in the source is not reported as
    a defect in the ingest.
    """
    absent = {normalise_clause_number(c) for c in absent_clauses}
    expected = {normalise_clause_number(c) for c in expected_clauses} - absent

    present: set[str] = set()
    for chunk in chunks:
        if chunk.clause_number:
            number = normalise_clause_number(chunk.clause_number)
            present.add(number)
            present.add(number.split(".")[0])

    missing = sorted(expected - present, key=lambda n: tuple(
        int(p) if p.isdigit() else 0 for p in n.split(".")
    ))
    if not missing:
        return []
    return [
        ValidationIssue(
            rule="missing_clauses",
            severity=Severity.WARNING,
            message=(
                f"{len(missing)} expected clause(s) have no chunk: "
                f"{', '.join(missing[:12])}"
                + (" ..." if len(missing) > 12 else "")
            ),
            details={"missing": missing, "excluded_as_absent": sorted(absent)},
        )
    ]


def check_provenance(chunks: Sequence[KBChunk]) -> list[ValidationIssue]:
    """Every chunk must be traceable to a source document and page.

    Without provenance a citation cannot be resolved, and an unresolvable
    citation is indistinguishable from a fabricated one.
    """
    offenders = [
        c.chunk_id for c in chunks if not c.source_document or c.page_number <= 0
    ]
    if not offenders:
        return []
    return [
        ValidationIssue(
            rule="missing_provenance",
            severity=Severity.ERROR,
            message=(
                f"{len(offenders)} chunk(s) lack a source document or a valid page "
                f"number. Citations to these cannot be resolved and are "
                f"indistinguishable from fabrications."
            ),
            chunk_ids=tuple(offenders),
            details={"count": len(offenders)},
        )
    ]


def validate_knowledge_base(
    chunks: Sequence[KBChunk],
    *,
    edition: str,
    expected_clauses: Iterable[str] = (),
    absent_clauses: Iterable[str] = (),
) -> ValidationReport:
    """Run the full rule set over a knowledge base edition.

    Args:
        chunks: All chunks belonging to the edition.
        edition: Edition code the chunks are expected to carry.
        expected_clauses: Clause numbers the edition should cover, typically
            from the edition's clause skeleton.
        absent_clauses: Numbers known not to exist in this edition.

    Returns:
        A report. ``is_publishable`` is False if any ERROR was raised.
    """
    report = ValidationReport(edition=edition, total_chunks=len(chunks))

    if not chunks:
        report.issues.append(
            ValidationIssue(
                rule="empty_knowledge_base",
                severity=Severity.ERROR,
                message=f"Knowledge base for edition {edition!r} contains no chunks.",
            )
        )
        return report

    report.issues.extend(check_edition_consistency(chunks, edition))
    report.issues.extend(check_provenance(chunks))
    report.issues.extend(check_contents_contamination(chunks))
    report.issues.extend(check_guidance_text(chunks))
    report.issues.extend(check_duplicates(chunks))
    report.issues.extend(check_running_headers(chunks))
    report.issues.extend(check_malformed(chunks))
    if expected_clauses:
        report.issues.extend(
            check_clause_coverage(chunks, expected_clauses, absent_clauses)
        )

    pages = {c.page_number for c in chunks}
    with_clause = sum(1 for c in chunks if c.clause_number)
    report.stats = {
        "pages_covered": len(pages),
        "page_range": (min(pages), max(pages)) if pages else (0, 0),
        "chunks_with_clause": with_clause,
        "chunks_without_clause": len(chunks) - with_clause,
        "mean_chunk_chars": round(
            sum(len(c.text) for c in chunks) / len(chunks), 1
        ),
        "total_lines": sum(_line_count(c.text) for c in chunks),
    }
    return report
