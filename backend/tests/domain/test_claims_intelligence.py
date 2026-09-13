"""Tests for chronology assembly and evidence gap analysis.

Two properties dominate: the chronology must not read as more certain than its
sources, and gap analysis must not let unreviewed evidence close a gap.
"""
from __future__ import annotations

from datetime import date

import pytest

from claimiq.claims.domain.chronology import (
    DatePrecision,
    EntryKind,
    TimelineEntry,
    build_chronology,
    detect_date_conflicts,
    find_notice_chain,
    gaps_exceeding,
)
from claimiq.claims.domain.evidence_gaps import (
    ElementStatus,
    EvidenceItem,
    Relevance,
    analyse_gaps,
    elements_for,
)


def entry(
    entry_id: str,
    day: date | None = date(2026, 3, 1),
    *,
    kind: EntryKind = EntryKind.EVENT,
    title: str = "Event",
    precision: DatePrecision = DatePrecision.DAY,
    clauses: tuple[str, ...] = (),
    document: str | None = "doc-1",
    ai: bool = False,
    confirmed: bool = False,
) -> TimelineEntry:
    return TimelineEntry(
        entry_id=entry_id,
        kind=kind,
        occurred_on=day,
        title=title,
        precision=precision,
        clause_references=clauses,
        source_document_id=document,
        is_ai_extracted=ai,
        is_confirmed=confirmed,
    )


# ---------------------------------------------------------------------------
# Chronology ordering and precision
# ---------------------------------------------------------------------------


def test_entries_are_ordered_by_date() -> None:
    chronology = build_chronology(
        [
            entry("c", date(2026, 3, 20)),
            entry("a", date(2026, 3, 1)),
            entry("b", date(2026, 3, 10)),
        ]
    )
    assert [e.entry_id for e in chronology.entries] == ["a", "b", "c"]


def test_precise_entries_lead_on_a_shared_date() -> None:
    """The precise entry is better evidence of sequence."""
    chronology = build_chronology(
        [
            entry("vague", date(2026, 3, 1), precision=DatePrecision.MONTH, title="Vague"),
            entry("exact", date(2026, 3, 1), precision=DatePrecision.DAY, title="Exact"),
        ]
    )
    assert [e.entry_id for e in chronology.entries] == ["exact", "vague"]


@pytest.mark.parametrize(
    ("precision", "expected"),
    [
        (DatePrecision.DAY, "2026-03-14"),
        (DatePrecision.MONTH, "March 2026"),
        (DatePrecision.YEAR, "2026"),
    ],
)
def test_dates_render_at_their_actual_precision(precision, expected) -> None:
    """A chronology must not read as more certain than its sources."""
    item = entry("x", date(2026, 3, 14), precision=precision)
    assert item.render_date() == expected


def test_unknown_precision_renders_honestly() -> None:
    item = entry("x", date(2026, 3, 14), precision=DatePrecision.UNKNOWN)
    assert item.render_date() == "date unknown"


def test_undated_entries_are_separated_not_dropped() -> None:
    chronology = build_chronology(
        [entry("dated", date(2026, 3, 1)), entry("undated", None, title="Unknown date")]
    )
    assert [e.entry_id for e in chronology.entries] == ["dated"]
    assert [e.entry_id for e in chronology.undated] == ["undated"]


def test_span_reports_the_window() -> None:
    chronology = build_chronology(
        [entry("a", date(2026, 1, 5)), entry("b", date(2026, 6, 20))]
    )
    assert chronology.span == (date(2026, 1, 5), date(2026, 6, 20))


def test_empty_chronology_has_no_span() -> None:
    assert build_chronology([]).span is None


# ---------------------------------------------------------------------------
# Conflicts are surfaced, not resolved
# ---------------------------------------------------------------------------


def test_conflicting_dates_for_the_same_event_are_flagged() -> None:
    """The disagreement is the finding. Silently picking one hides the dispute."""
    conflicts = detect_date_conflicts(
        [
            entry("a", date(2026, 3, 1), title="Notice of Claim served"),
            entry("b", date(2026, 3, 8), title="Notice of Claim served"),
        ]
    )
    assert len(conflicts) == 1
    assert set(conflicts[0].dates) == {date(2026, 3, 1), date(2026, 3, 8)}
    assert "dated differently" in conflicts[0].describe()


def test_matching_dates_are_not_a_conflict() -> None:
    assert detect_date_conflicts(
        [
            entry("a", date(2026, 3, 1), title="Notice served"),
            entry("b", date(2026, 3, 1), title="Notice served"),
        ]
    ) == []


def test_a_less_precise_date_does_not_contradict_a_precise_one() -> None:
    """"March" is consistent with "14 March", not in conflict with it."""
    conflicts = detect_date_conflicts(
        [
            entry("a", date(2026, 3, 14), title="Notice served"),
            entry("b", date(2026, 3, 1), title="Notice served", precision=DatePrecision.MONTH),
        ]
    )
    assert conflicts == []


def test_conflicts_appear_on_the_assembled_chronology() -> None:
    chronology = build_chronology(
        [
            entry("a", date(2026, 3, 1), title="Notice served"),
            entry("b", date(2026, 3, 8), title="Notice served"),
        ]
    )
    assert len(chronology.conflicts) == 1
    assert "conflict" in chronology.summary()


# ---------------------------------------------------------------------------
# Provenance and review state
# ---------------------------------------------------------------------------


def test_unreviewed_ai_entries_are_included_but_marked() -> None:
    """Omitting them silently produces holes the reader cannot see."""
    chronology = build_chronology([entry("x", ai=True, confirmed=False)])
    assert len(chronology.entries) == 1
    assert chronology.entries[0].needs_review is True
    assert chronology.unreviewed_count == 1


def test_confirmed_ai_entries_do_not_need_review() -> None:
    chronology = build_chronology([entry("x", ai=True, confirmed=True)])
    assert chronology.unreviewed_count == 0


def test_unreviewed_entries_can_be_excluded_for_a_submission() -> None:
    chronology = build_chronology(
        [entry("human"), entry("ai", ai=True)], include_unreviewed=False
    )
    assert [e.entry_id for e in chronology.entries] == ["human"]


def test_unsourced_entries_are_counted() -> None:
    chronology = build_chronology([entry("a"), entry("b", document=None)])
    assert chronology.unsourced_count == 1
    assert chronology.entries[0].has_provenance is True


# ---------------------------------------------------------------------------
# Filtering and analysis
# ---------------------------------------------------------------------------


def test_notice_chain_follows_one_clause() -> None:
    chronology = build_chronology(
        [
            entry("n", date(2026, 3, 1), title="Notice", clauses=("20.2.1",)),
            entry("r", date(2026, 3, 20), title="Response", clauses=("20.2.2",)),
            entry("x", date(2026, 4, 1), title="Unrelated", clauses=("14.3",)),
        ]
    )
    chain = find_notice_chain(chronology, "20.2.1")
    assert [e.entry_id for e in chain] == ["n"]

    parent_chain = find_notice_chain(chronology, "20.2")
    assert {e.entry_id for e in parent_chain} == {"n", "r"}, "descendants included"


def test_kind_filter_restricts_the_chronology() -> None:
    chronology = build_chronology(
        [entry("e", kind=EntryKind.EVENT), entry("c", kind=EntryKind.CORRESPONDENCE)],
        kinds=[EntryKind.CORRESPONDENCE],
    )
    assert [e.entry_id for e in chronology.entries] == ["c"]


def test_between_selects_a_window_inclusively() -> None:
    chronology = build_chronology(
        [
            entry("a", date(2026, 1, 1)),
            entry("b", date(2026, 3, 1)),
            entry("c", date(2026, 6, 1)),
        ]
    )
    window = chronology.between(date(2026, 1, 1), date(2026, 3, 1))
    assert [e.entry_id for e in window] == ["a", "b"]


def test_long_gaps_are_reported() -> None:
    """An unexplained silence between event and notice is what a respondent points at."""
    chronology = build_chronology(
        [entry("a", date(2026, 1, 1)), entry("b", date(2026, 4, 1))]
    )
    gaps = gaps_exceeding(chronology, 30)
    assert len(gaps) == 1
    assert gaps[0][2] == 90


def test_short_gaps_are_not_reported() -> None:
    chronology = build_chronology(
        [entry("a", date(2026, 1, 1)), entry("b", date(2026, 1, 10))]
    )
    assert gaps_exceeding(chronology, 30) == []


# ---------------------------------------------------------------------------
# Evidence gaps
# ---------------------------------------------------------------------------


def evidence(
    code: str | None,
    relevance: Relevance = Relevance.SUPPORTS,
    *,
    reviewed: bool = True,
    item_id: str = "e1",
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=item_id,
        title=f"Evidence for {code}",
        element_code=code,
        relevance=relevance,
        is_reviewed=reviewed,
    )


def test_eot_requires_time_impact() -> None:
    codes = {e.code for e in elements_for("eot")}
    assert "time_impact" in codes
    assert "notice" in codes


def test_variation_requires_an_instruction() -> None:
    assert "instruction" in {e.code for e in elements_for("variation")}


def test_unknown_claim_type_falls_back_to_the_common_set() -> None:
    assert elements_for("no_such_type") == elements_for("other")


def test_missing_elements_are_reported_as_gaps() -> None:
    report = analyse_gaps("eot", [evidence("event")])
    codes = {a.element.code for a in report.essential_gaps}
    assert "notice" in codes
    assert "causation" in codes
    assert report.is_complete is False


def test_reviewed_supporting_evidence_establishes_an_element() -> None:
    report = analyse_gaps("eot", [evidence("event", reviewed=True)])
    event = next(a for a in report.assessments if a.element.code == "event")
    assert event.status is ElementStatus.ESTABLISHED


def test_unreviewed_evidence_cannot_close_a_gap_alone() -> None:
    """An unchecked AI suggestion must not mark an element established."""
    report = analyse_gaps("eot", [evidence("event", reviewed=False)])
    event = next(a for a in report.assessments if a.element.code == "event")
    assert event.status is ElementStatus.PARTIAL
    assert event.is_gap is True


def test_unassessed_relevance_is_only_partial() -> None:
    report = analyse_gaps("eot", [evidence("event", Relevance.UNASSESSED)])
    event = next(a for a in report.assessments if a.element.code == "event")
    assert event.status is ElementStatus.PARTIAL


def test_contradicted_element_is_contested_not_missing() -> None:
    """A dispute is more urgent than a gap; it will be argued, not just queried."""
    report = analyse_gaps(
        "eot",
        [
            evidence("causation", Relevance.SUPPORTS, item_id="a"),
            evidence("causation", Relevance.CONTRADICTS, item_id="b"),
        ],
    )
    causation = next(a for a in report.assessments if a.element.code == "causation")
    assert causation.status is ElementStatus.CONTESTED
    assert causation in report.contested
    assert "dispute to resolve" in causation.suggestion()


def test_non_essential_gap_does_not_block_completeness() -> None:
    supplied = ["event", "notice", "causation", "responsibility", "time_impact"]
    report = analyse_gaps("eot", [evidence(code, item_id=code) for code in supplied])
    assert report.is_complete is True
    mitigation = next(a for a in report.assessments if a.element.code == "mitigation")
    assert mitigation.status is ElementStatus.MISSING
    assert mitigation.element.is_essential is False


def test_completeness_ratio_counts_essential_elements_only() -> None:
    report = analyse_gaps("eot", [evidence("event", item_id="a")])
    essential = [a for a in report.assessments if a.element.is_essential]
    assert report.completeness_ratio == pytest.approx(1 / len(essential))


def test_suggestion_names_documents_that_would_close_the_gap() -> None:
    report = analyse_gaps("eot", [])
    notice = next(a for a in report.assessments if a.element.code == "notice")
    suggestion = notice.suggestion()
    assert "notice" in suggestion.lower()
    assert "usually evidenced by" in suggestion


def test_evidence_for_an_unmodelled_element_is_reported_not_dropped() -> None:
    """It may be relevant to something the element list does not model."""
    report = analyse_gaps("eot", [evidence("something_else", item_id="x"), evidence(None, item_id="y")])
    assert {e.evidence_id for e in report.unmatched_evidence} == {"x", "y"}


def test_summary_is_readable() -> None:
    report = analyse_gaps("eot", [evidence("event")])
    assert "essential elements established" in report.summary()
