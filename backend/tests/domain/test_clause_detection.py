"""Tests for clause structure detection.

These target the three specific defects in the legacy prototype's detector
(see the module docstring of ``claimiq.ingestion.domain.clause_detection``):
heading/reference conflation, contents-page contamination, and missing hierarchy.
"""
from __future__ import annotations

import pytest

from claimiq.ingestion.domain.clause_detection import (
    HEADING_MIN_CONFIDENCE,
    ClauseNode,
    PageText,
    build_hierarchy,
    classify_page,
    clause_depth,
    detect,
    detect_citations,
    detect_headings,
    is_contents_line,
    normalise_clause_number,
    parent_of,
)


# ---------------------------------------------------------------------------
# Number normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("20.2.1", "20.2.1"),
        ("20.2.1.", "20.2.1"),
        ("08.02", "8.2"),
        ("  4.12  ", "4.12"),
        ("53", "53"),
        ("20.2)", "20.2"),
    ],
)
def test_normalise_clause_number(raw: str, expected: str) -> None:
    assert normalise_clause_number(raw) == expected


def test_clause_depth_and_parent() -> None:
    assert clause_depth("20") == 1
    assert clause_depth("20.2") == 2
    assert clause_depth("20.2.1") == 3
    assert parent_of("20") is None
    assert parent_of("20.2") == "20"
    assert parent_of("20.2.1") == "20.2"


# ---------------------------------------------------------------------------
# Contents-page detection — the contamination fix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "1.1 Definitions ................................ 12",
        "20.2.1  Notice of Claim ....... 145",
        "Extension of Time for Completion .......... 88",
        "20.2.1  Notice of Claim        145",
    ],
)
def test_contents_lines_are_recognised(line: str) -> None:
    assert is_contents_line(line) is True


@pytest.mark.parametrize(
    "line",
    [
        "20.2.1 Notice of Claim",
        "1.1 Definitions",
        "The Contractor shall give a Notice to the Engineer.",
        "",
    ],
)
def test_body_lines_are_not_contents(line: str) -> None:
    assert is_contents_line(line) is False


def test_contents_page_is_excluded_from_headings() -> None:
    """A whole contents page must not contribute headings.

    This is the exact failure that put 422 contaminated chunks into the legacy
    2017 knowledge base.
    """
    toc = PageText(
        page_number=17,
        text=(
            "Table of Contents\n"
            "1     General Provisions ................... 1\n"
            "1.1   Definitions .......................... 1\n"
            "1.2   Interpretation ....................... 8\n"
            "1.3   Notices and Other Communications ..... 9\n"
            "20    Employer's and Contractor's Claims ... 120\n"
            "20.1  Claims .............................. 120\n"
            "20.2  Claims For Payment and/or EOT ....... 121\n"
        ),
    )
    headings, skipped = detect_headings([toc])
    assert headings == []
    assert skipped == [17]


def test_body_page_is_classified_as_content() -> None:
    body = PageText(
        page_number=120,
        text=(
            "20.2.1 Notice of Claim\n"
            "The claiming Party shall give a Notice to the Engineer, describing "
            "the event or circumstance giving rise to the Claim.\n"
            "The Notice shall be given as soon as practicable, and no later than "
            "28 days after the claiming Party became aware of the event.\n"
        ),
    )
    assert classify_page(body) is True


# ---------------------------------------------------------------------------
# Heading vs cross-reference — the conflation fix
# ---------------------------------------------------------------------------


def test_heading_is_detected_with_title() -> None:
    page = PageText(page_number=121, text="20.2.1 Notice of Claim\nThe claiming Party shall...")
    headings, _ = detect_headings([page])
    assert len(headings) == 1
    heading = headings[0]
    assert heading.number == "20.2.1"
    assert heading.title == "Notice of Claim"
    assert heading.page_number == 121
    assert heading.depth == 3
    assert heading.confidence >= HEADING_MIN_CONFIDENCE


def test_cross_reference_in_prose_is_not_a_heading() -> None:
    """The critical distinction the prototype could not make.

    A sentence mentioning a clause must produce a citation, never a heading —
    otherwise the clause index records the wrong page as the clause's location.
    """
    page = PageText(
        page_number=45,
        text=(
            "If the Contractor fails to give the Notice required by Sub-Clause "
            "20.2.1, the Contractor shall not be entitled to any additional payment."
        ),
    )
    headings, _ = detect_headings([page])
    assert headings == []

    citations = detect_citations([page])
    numbers = {c.number for c in citations}
    assert "20.2.1" in numbers
    assert any(c.is_explicit for c in citations if c.number == "20.2.1")


def test_prose_line_starting_with_number_is_rejected() -> None:
    page = PageText(
        page_number=8,
        text="20.2 The Contractor shall submit a fully detailed Claim within 84 days.",
    )
    headings, _ = detect_headings([page])
    assert headings == [], "A sentence beginning with a clause number is not a heading"


def test_keyword_prefixed_heading_scores_higher() -> None:
    plain = PageText(page_number=1, text="20 Claims")
    keyworded = PageText(page_number=1, text="Clause 20 - Employer's and Contractor's Claims")

    plain_headings, _ = detect_headings([plain])
    keyword_headings, _ = detect_headings([keyworded])

    assert plain_headings and keyword_headings
    assert keyword_headings[0].confidence > plain_headings[0].confidence


# ---------------------------------------------------------------------------
# Citation extraction
# ---------------------------------------------------------------------------


def test_bare_dotted_reference_is_detected_as_weak() -> None:
    page = PageText(page_number=3, text="The provisions of 4.12 apply to this event.")
    citations = detect_citations([page])
    match = [c for c in citations if c.number == "4.12"]
    assert match
    assert match[0].is_explicit is False


def test_measurements_are_not_treated_as_clause_references() -> None:
    """"1.5 million" and "2.5 m" must not become clause references."""
    page = PageText(
        page_number=3,
        text="The delay cost is USD 1.5 million and the shaft is 2.5 m in diameter.",
    )
    citations = detect_citations([page])
    numbers = {c.number for c in citations}
    assert "1.5" not in numbers
    assert "2.5" not in numbers


def test_explicit_reference_not_double_counted_as_bare() -> None:
    page = PageText(page_number=3, text="See Sub-Clause 20.2.1 for the notice requirement.")
    citations = [c for c in detect_citations([page]) if c.number == "20.2.1"]
    assert len(citations) == 1
    assert citations[0].is_explicit is True


def test_integers_alone_are_not_references() -> None:
    page = PageText(page_number=3, text="The period is 28 days from the date of the Notice.")
    citations = detect_citations([page])
    assert [c for c in citations if not c.is_explicit] == []


# ---------------------------------------------------------------------------
# Hierarchy — the missing-structure fix
# ---------------------------------------------------------------------------


def _numbers(nodes: list[ClauseNode]) -> list[str]:
    return [n.number for n in nodes]


def test_hierarchy_nests_subclauses() -> None:
    pages = [
        PageText(page_number=120, text="20 Employer's and Contractor's Claims"),
        PageText(page_number=121, text="20.1 Claims"),
        PageText(page_number=122, text="20.2 Claims For Payment and/or EOT"),
        PageText(page_number=123, text="20.2.1 Notice of Claim"),
    ]
    result = detect(pages)

    assert _numbers(result.roots) == ["20"]
    root = result.roots[0]
    assert _numbers(root.children) == ["20.1", "20.2"]

    sub = result.find("20.2")
    assert sub is not None
    assert _numbers(sub.children) == ["20.2.1"]
    assert sub.children[0].parent_number == "20.2"


def test_orphan_subclause_attaches_to_nearest_ancestor() -> None:
    """20.2.1 with no detected 20.2 should attach to 20, not be dropped."""
    pages = [
        PageText(page_number=120, text="20 Employer's and Contractor's Claims"),
        PageText(page_number=123, text="20.2.1 Notice of Claim"),
    ]
    result = detect(pages)
    assert _numbers(result.roots) == ["20"]
    child = result.roots[0].children[0]
    assert child.number == "20.2.1"
    assert child.parent_number == "20"


def test_orphan_with_no_ancestor_becomes_root() -> None:
    pages = [PageText(page_number=5, text="4.12.3 Delay and/or Cost")]
    result = detect(pages)
    assert _numbers(result.roots) == ["4.12.3"]
    assert result.roots[0].parent_number is None


def test_duplicate_headings_keep_highest_confidence() -> None:
    """A running header repeating a clause number must not displace the real one."""
    pages = [
        PageText(page_number=120, text="Clause 20 - Employer's and Contractor's Claims"),
        PageText(page_number=140, text="20 Claims"),
    ]
    roots = build_hierarchy(detect_headings(pages)[0])
    assert len(roots) == 1
    assert roots[0].page_number == 120


def test_hierarchy_sorts_numerically_not_lexically() -> None:
    """20.10 must sort after 20.2, which string ordering gets wrong."""
    pages = [
        PageText(page_number=1, text="20 Claims"),
        PageText(page_number=2, text="20.2 Claims For Payment"),
        PageText(page_number=3, text="20.10 Additional Provisions"),
    ]
    result = detect(pages)
    assert _numbers(result.roots[0].children) == ["20.2", "20.10"]


# ---------------------------------------------------------------------------
# End-to-end
# ---------------------------------------------------------------------------


def test_full_detection_separates_headings_from_citations() -> None:
    pages = [
        PageText(
            page_number=16,
            text=(
                "Contents\n"
                "20    Claims ......................... 120\n"
                "20.1  Claims ......................... 120\n"
                "20.2  Claims For Payment ............. 121\n"
                "21    Disputes ....................... 130\n"
            ),
        ),
        PageText(page_number=120, text="20 Employer's and Contractor's Claims"),
        PageText(
            page_number=121,
            text=(
                "20.2.1 Notice of Claim\n"
                "The claiming Party shall give a Notice under Sub-Clause 20.2.1 "
                "no later than 28 days after becoming aware of the event."
            ),
        ),
    ]
    result = detect(pages)

    assert 16 in result.skipped_pages
    assert set(result.clause_numbers()) == {"20", "20.2.1"}
    assert all(h.page_number != 16 for h in result.headings)
    assert any(c.number == "20.2.1" and c.is_explicit for c in result.citations)
