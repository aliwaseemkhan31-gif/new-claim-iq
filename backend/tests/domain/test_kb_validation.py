"""Tests for knowledge-base validation.

The reference scenario is the legacy 2017 ingest, which admitted 422 chunks of
contents pages, front matter and guidance notes that had to be removed by hand.
These tests assert that the same material is now caught automatically.
"""
from __future__ import annotations

from claimiq.knowledge.domain.validation import (
    KBChunk,
    Severity,
    check_clause_coverage,
    check_contents_contamination,
    check_duplicates,
    check_edition_consistency,
    check_guidance_text,
    check_malformed,
    check_running_headers,
    validate_knowledge_base,
)

EDITION = "red-book-2017"

CLEAN_TEXT = (
    "The claiming Party shall give a Notice to the Engineer, describing the "
    "event or circumstance giving rise to the Claim. The Notice shall be given "
    "as soon as practicable, and no later than 28 days after the claiming Party "
    "became aware, or should have become aware, of the event or circumstance."
)


def chunk(
    chunk_id: str,
    text: str = CLEAN_TEXT,
    *,
    edition: str = EDITION,
    page: int = 121,
    clause: str | None = "20.2.1",
    source: str | None = "fidic_red_book_2017.pdf",
) -> KBChunk:
    return KBChunk(
        chunk_id=chunk_id,
        text=text,
        edition=edition,
        page_number=page,
        clause_number=clause,
        source_document=source,
    )


# ---------------------------------------------------------------------------
# Contents contamination — the 422-chunk scenario
# ---------------------------------------------------------------------------


def test_contents_page_chunk_is_flagged_as_error() -> None:
    toc = chunk(
        "c1",
        "1.1   Definitions .......................... 1\n"
        "1.2   Interpretation ....................... 8\n"
        "1.3   Notices and Other Communications ..... 9\n",
        page=17,
        clause=None,
    )
    issues = check_contents_contamination([toc])
    assert len(issues) == 1
    assert issues[0].severity is Severity.ERROR
    assert "c1" in issues[0].chunk_ids


def test_clean_provision_text_is_not_flagged() -> None:
    assert check_contents_contamination([chunk("c1")]) == []


def test_single_unmistakable_contents_line_is_flagged() -> None:
    issues = check_contents_contamination(
        [chunk("c1", "20.2  Claims For Payment ............. 121", clause=None)]
    )
    assert issues and "c1" in issues[0].chunk_ids


# ---------------------------------------------------------------------------
# Guidance text
# ---------------------------------------------------------------------------


def test_guidance_note_is_flagged_as_error() -> None:
    guidance = chunk(
        "g1",
        "Guidance for the Preparation of Particular Conditions. The Employer may "
        "wish to consider whether the period stated in this Sub-Clause is "
        "appropriate for the particular project.",
        page=8,
        clause=None,
    )
    issues = check_guidance_text([guidance])
    assert len(issues) == 1
    assert issues[0].severity is Severity.ERROR


def test_sample_form_is_flagged() -> None:
    issues = check_guidance_text(
        [chunk("f1", "Sample Form of Performance Security issued by a bank.", clause=None)]
    )
    assert issues and "f1" in issues[0].chunk_ids


def test_conditions_text_is_not_mistaken_for_guidance() -> None:
    assert check_guidance_text([chunk("c1")]) == []


# ---------------------------------------------------------------------------
# Duplicates, headers, malformed
# ---------------------------------------------------------------------------


def test_identical_chunks_are_reported_once_and_first_is_kept() -> None:
    issues = check_duplicates([chunk("a"), chunk("b"), chunk("c")])
    assert len(issues) == 1
    assert issues[0].severity is Severity.WARNING
    assert set(issues[0].chunk_ids) == {"b", "c"}, "the first occurrence is retained"


def test_whitespace_differences_still_count_as_duplicates() -> None:
    issues = check_duplicates(
        [chunk("a", CLEAN_TEXT), chunk("b", CLEAN_TEXT.replace(" ", "  "))]
    )
    assert issues


def test_running_header_is_detected() -> None:
    header = "FIDIC Conditions of Contract for Construction"
    chunks = [chunk(f"h{i}", header, page=100 + i, clause=None) for i in range(6)]
    issues = check_running_headers(chunks)
    assert len(issues) == 1
    assert len(issues[0].chunk_ids) == 6


def test_short_chunk_is_malformed() -> None:
    issues = check_malformed([chunk("s1", "20.2.1", clause=None)])
    assert issues and "s1" in issues[0].chunk_ids


def test_mostly_numeric_chunk_is_malformed() -> None:
    issues = check_malformed(
        [chunk("n1", "121 122 123 124 125 126 127 128 129 130 131 132", clause=None)]
    )
    assert issues and "n1" in issues[0].chunk_ids


# ---------------------------------------------------------------------------
# Edition consistency — ADR 0004
# ---------------------------------------------------------------------------


def test_foreign_edition_chunk_is_an_error() -> None:
    issues = check_edition_consistency(
        [chunk("a"), chunk("b", edition="red-book-1987")], EDITION
    )
    assert len(issues) == 1
    assert issues[0].severity is Severity.ERROR
    assert issues[0].chunk_ids == ("b",)
    assert issues[0].details["found"] == ["red-book-1987"]


def test_consistent_edition_passes() -> None:
    assert check_edition_consistency([chunk("a"), chunk("b")], EDITION) == []


# ---------------------------------------------------------------------------
# Clause coverage
# ---------------------------------------------------------------------------


def test_missing_clause_is_reported() -> None:
    issues = check_clause_coverage([chunk("a", clause="20.2.1")], ["20", "21"])
    assert issues
    assert "21" in issues[0].details["missing"]


def test_known_absent_clause_is_not_reported_missing() -> None:
    """The 1987 reprint has no Clause 26; that is a source quirk, not a defect."""
    issues = check_clause_coverage(
        [chunk("a", clause="25.1")],
        expected_clauses=["25", "26", "27"],
        absent_clauses=["26"],
    )
    missing = issues[0].details["missing"] if issues else []
    assert "26" not in missing
    assert "27" in missing


def test_parent_clause_satisfied_by_subclause() -> None:
    assert check_clause_coverage([chunk("a", clause="20.2.1")], ["20"]) == []


# ---------------------------------------------------------------------------
# Full report
# ---------------------------------------------------------------------------


def test_clean_knowledge_base_is_publishable() -> None:
    chunks = [
        chunk("a", CLEAN_TEXT, page=121, clause="20.2.1"),
        chunk("b", CLEAN_TEXT.replace("28 days", "42 days"), page=122, clause="20.2.2"),
    ]
    report = validate_knowledge_base(chunks, edition=EDITION)
    assert report.is_publishable is True
    assert report.errors == []
    assert report.stats["pages_covered"] == 2


def test_contaminated_knowledge_base_is_not_publishable() -> None:
    chunks = [
        chunk("good", CLEAN_TEXT),
        chunk("toc", "1.1 Definitions ....... 1\n1.2 Interpretation ....... 8\n", clause=None),
        chunk("guide", "Note: the Employer may wish to amend this Sub-Clause.", clause=None),
    ]
    report = validate_knowledge_base(chunks, edition=EDITION)
    assert report.is_publishable is False
    assert {"toc", "guide"} <= set(report.contaminated_chunk_ids())
    assert "good" not in report.contaminated_chunk_ids()


def test_quarantine_list_reproduces_the_manual_cleanup() -> None:
    """The automated equivalent of the hand-written deleted-ids file."""
    contaminated = [
        chunk(f"toc{i}", f"{i}.1 Something ........ {i}", page=i, clause=None)
        for i in range(1, 6)
    ]
    clean = [chunk(f"body{i}", CLEAN_TEXT, page=120 + i) for i in range(3)]
    report = validate_knowledge_base(contaminated + clean, edition=EDITION)
    quarantined = set(report.contaminated_chunk_ids())
    assert quarantined == {f"toc{i}" for i in range(1, 6)}


def test_missing_provenance_is_an_error() -> None:
    report = validate_knowledge_base([chunk("a", source=None)], edition=EDITION)
    assert report.is_publishable is False
    assert any(i.rule == "missing_provenance" for i in report.errors)


def test_empty_knowledge_base_is_an_error() -> None:
    report = validate_knowledge_base([], edition=EDITION)
    assert report.is_publishable is False
    assert report.errors[0].rule == "empty_knowledge_base"


def test_summary_is_human_readable() -> None:
    report = validate_knowledge_base([chunk("a")], edition=EDITION)
    assert EDITION in report.summary()
