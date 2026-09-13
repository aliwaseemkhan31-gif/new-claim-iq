"""Chronology assembly.

Merges events from several sources — claim events, correspondence, notices,
deadlines — into one ordered account of what happened.

Three things this does that a naive sort does not:

1. **Preserves date precision.** A source saying "in March" and one saying
   "14 March" are not the same claim about the world. Rendering both as
   ``2026-03-14`` overstates the record. Precision travels with the entry and
   imprecise entries sort after precise ones on the same date, because the
   precise one is the better evidence of sequence.

2. **Flags conflicts rather than resolving them.** When two sources date the
   same event differently, that disagreement *is* the finding. Silently
   preferring one produces a tidy chronology that hides the dispute.

3. **Keeps provenance on every entry.** An undated, unsourced timeline entry is
   an assertion. Entries without a source are marked as such so a reader can
   tell what is evidenced from what is inferred.

Pure stdlib; runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Iterable, Sequence


class DatePrecision(str, Enum):
    """How precisely an event's date is known."""

    DAY = "day"
    MONTH = "month"
    YEAR = "year"
    UNKNOWN = "unknown"

    @property
    def rank(self) -> int:
        """Lower sorts first. Precise entries lead on a shared date."""
        return {"day": 0, "month": 1, "year": 2, "unknown": 3}[self.value]


class EntryKind(str, Enum):
    EVENT = "event"
    CORRESPONDENCE = "correspondence"
    NOTICE = "notice"
    DEADLINE = "deadline"
    CLAIM_MILESTONE = "claim_milestone"


@dataclass(frozen=True)
class TimelineEntry:
    """One dated item in a chronology."""

    entry_id: str
    kind: EntryKind
    occurred_on: date | None
    title: str
    description: str = ""
    precision: DatePrecision = DatePrecision.DAY
    party: str | None = None
    clause_references: tuple[str, ...] = ()
    source_document_id: str | None = None
    source_document_title: str | None = None
    source_page: int | None = None
    claim_id: str | None = None
    is_ai_extracted: bool = False
    is_confirmed: bool = False
    confidence: float | None = None

    @property
    def has_provenance(self) -> bool:
        """Whether this entry can be traced to a document."""
        return self.source_document_id is not None

    @property
    def is_dated(self) -> bool:
        return self.occurred_on is not None and self.precision is not DatePrecision.UNKNOWN

    @property
    def needs_review(self) -> bool:
        """AI-extracted and not yet confirmed by a person.

        Rendered distinctly rather than mixed in as established fact.
        """
        return self.is_ai_extracted and not self.is_confirmed

    def render_date(self) -> str:
        """Format the date at its actual precision.

        "March 2026" rather than "2026-03-14" when the day is not known — the
        chronology should not read as more certain than its sources.
        """
        if self.occurred_on is None or self.precision is DatePrecision.UNKNOWN:
            return "date unknown"
        if self.precision is DatePrecision.YEAR:
            return self.occurred_on.strftime("%Y")
        if self.precision is DatePrecision.MONTH:
            return self.occurred_on.strftime("%B %Y")
        return self.occurred_on.isoformat()


@dataclass(frozen=True)
class DateConflict:
    """Two sources dating the same event differently."""

    title: str
    entries: tuple[TimelineEntry, ...]
    dates: tuple[date, ...]

    def describe(self) -> str:
        rendered = ", ".join(sorted({d.isoformat() for d in self.dates}))
        return f"{self.title!r} is dated differently by {len(self.entries)} sources: {rendered}"


@dataclass
class Chronology:
    """An assembled timeline with its unresolved problems attached."""

    entries: list[TimelineEntry] = field(default_factory=list)
    undated: list[TimelineEntry] = field(default_factory=list)
    conflicts: list[DateConflict] = field(default_factory=list)

    @property
    def span(self) -> tuple[date, date] | None:
        dated = [e.occurred_on for e in self.entries if e.occurred_on is not None]
        return (min(dated), max(dated)) if dated else None

    @property
    def unreviewed_count(self) -> int:
        return sum(1 for e in self.entries if e.needs_review)

    @property
    def unsourced_count(self) -> int:
        return sum(1 for e in self.entries if not e.has_provenance)

    def of_kind(self, kind: EntryKind) -> list[TimelineEntry]:
        return [e for e in self.entries if e.kind is kind]

    def between(self, start: date, end: date) -> list[TimelineEntry]:
        """Entries in ``[start, end]`` inclusive."""
        return [
            e for e in self.entries if e.occurred_on is not None and start <= e.occurred_on <= end
        ]

    def summary(self) -> str:
        span = self.span
        window = f"{span[0].isoformat()} to {span[1].isoformat()}" if span else "no dated entries"
        return (
            f"{len(self.entries)} entries ({window}), {len(self.undated)} undated, "
            f"{len(self.conflicts)} date conflict(s), {self.unreviewed_count} unreviewed"
        )


def _sort_key(entry: TimelineEntry) -> tuple:
    return (
        entry.occurred_on or date.max,
        entry.precision.rank,
        entry.title.lower(),
        entry.entry_id,
    )


def _normalise_title(title: str) -> str:
    return " ".join(title.lower().split())


def detect_date_conflicts(entries: Sequence[TimelineEntry]) -> list[DateConflict]:
    """Find events described identically but dated differently.

    Only day-precision entries are compared. A source saying "March" does not
    contradict one saying "14 March" — it is consistent with it and less
    precise, and reporting that as a conflict would bury the real ones.
    """
    grouped: dict[str, list[TimelineEntry]] = {}
    for entry in entries:
        if entry.occurred_on is None or entry.precision is not DatePrecision.DAY:
            continue
        grouped.setdefault(_normalise_title(entry.title), []).append(entry)

    conflicts: list[DateConflict] = []
    for candidates in grouped.values():
        distinct = {e.occurred_on for e in candidates}
        if len(distinct) > 1:
            conflicts.append(
                DateConflict(
                    title=candidates[0].title,
                    entries=tuple(sorted(candidates, key=_sort_key)),
                    dates=tuple(sorted(d for d in distinct if d is not None)),
                )
            )
    return conflicts


def build_chronology(
    entries: Iterable[TimelineEntry],
    *,
    include_unreviewed: bool = True,
    kinds: Iterable[EntryKind] | None = None,
) -> Chronology:
    """Assemble a chronology from mixed entries.

    Args:
        entries: Entries from any source.
        include_unreviewed: Whether to include unconfirmed AI-extracted
            entries. They are included by default and marked, because omitting
            them silently produces a chronology with holes the reader cannot
            see. A reviewer preparing a submission can exclude them.
        kinds: Restrict to these kinds.

    Returns:
        A chronology with dated entries in order, undated entries separated,
        and any date conflicts recorded.
    """
    wanted = set(kinds) if kinds is not None else None
    selected: list[TimelineEntry] = []

    for entry in entries:
        if wanted is not None and entry.kind not in wanted:
            continue
        if not include_unreviewed and entry.needs_review:
            continue
        selected.append(entry)

    dated = [e for e in selected if e.is_dated]
    undated = [e for e in selected if not e.is_dated]

    return Chronology(
        entries=sorted(dated, key=_sort_key),
        undated=sorted(undated, key=lambda e: e.title.lower()),
        conflicts=detect_date_conflicts(dated),
    )


def find_notice_chain(
    chronology: Chronology, clause_number: str
) -> list[TimelineEntry]:
    """Entries relating to one clause, in order.

    The Notice -> Response -> Determination sequence for a provision, which is
    what a notice-compliance argument is actually about.
    """
    return [
        entry
        for entry in chronology.entries
        if clause_number in entry.clause_references
        or any(ref.startswith(f"{clause_number}.") for ref in entry.clause_references)
    ]


def gaps_exceeding(chronology: Chronology, days: int) -> list[tuple[TimelineEntry, TimelineEntry, int]]:
    """Consecutive dated entries separated by more than ``days``.

    Long silences matter in a claim narrative: an unexplained gap between an
    event and the notice of it is exactly what a respondent will point at.
    """
    result: list[tuple[TimelineEntry, TimelineEntry, int]] = []
    dated = [e for e in chronology.entries if e.occurred_on is not None]
    for earlier, later in zip(dated, dated[1:]):
        delta = (later.occurred_on - earlier.occurred_on).days  # type: ignore[operator]
        if delta > days:
            result.append((earlier, later, delta))
    return result
