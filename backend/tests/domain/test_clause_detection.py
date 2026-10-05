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
    looks_like_table_row,
    normalise_clause_number,
    numeric_profile,
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


# ---------------------------------------------------------------------------
# Printings that number clauses in the margin (FIDIC 1987)
# ---------------------------------------------------------------------------


MARGIN_PAGE = """any further contemporary records as are reasonable and may be material to the
claim of which notice has been given. The Contractor shall permit the Engineer to
inspect all records kept pursuant to this Sub-Clause.
Substantiation 53.3 Within 28 days, or such other reasonable time as may be agreed by the Engineer,
of Claims of giving notice under Sub-Clause 53.1, the Contractor shall send an account."""

CONTENTS_PAGE = """CONTENTS
16.1 Contractor's Employees 8
16.2 Engineer at Liberty to Object 8
17.1 Setting-out 9
18.1 Boreholes and Exploratory Excavation 9
19.1 Safety, Security and Protection of the Environment 9"""


def test_contents_entry_with_a_single_space_before_the_page_number() -> None:
    """Regression: the 1987 printing sets contents without dot leaders, so
    every entry was read as a clause heading and contaminated the structure."""
    assert is_contents_line("16.1 Contractor's Employees 8")
    assert is_contents_line("44.2 Engineer's Determination of Extension 21")


def test_a_provision_line_is_not_a_contents_entry() -> None:
    assert not is_contents_line(
        "53.3 Within 28 days, or such other reasonable time as may be agreed by the Engineer,"
    )
    assert not is_contents_line("The amount certified was 1 250")


def test_a_contents_page_is_excluded_from_body_content() -> None:
    assert classify_page(PageText(page_number=4, text=CONTENTS_PAGE)) is False


def test_clause_number_after_a_marginal_note_is_detected() -> None:
    """Regression: with the number inline after the marginal note, no heading
    was found and passages inherited the previous clause's number — so a
    citation named the wrong provision."""
    headings, _skipped = detect_headings([PageText(page_number=32, text=MARGIN_PAGE)])
    assert [h.number for h in headings] == ["53.3"]
    heading = headings[0]
    assert heading.title == "Substantiation"
    # The provision starts at its number, not at the marginal note.
    assert MARGIN_PAGE[heading.char_offset :].startswith("53.3 Within 28 days")


def test_prose_beginning_with_a_clause_reference_is_not_a_marginal_heading() -> None:
    page = PageText(
        page_number=9,
        text="Sub-Clause 20.2 The Contractor shall be responsible for the care of the Works.",
    )
    assert detect_headings([page])[0] == []


def test_line_leading_headings_are_unaffected() -> None:
    page = PageText(page_number=1, text="20.2.1 Notice of Claim\nThe Contractor shall give a Notice.")
    headings, _skipped = detect_headings([page])
    assert [(h.number, h.title) for h in headings] == [("20.2.1", "Notice of Claim")]


# ---------------------------------------------------------------------------
# Table rows read as clause headings
#
# A priced table row is structurally identical to a clause heading: a
# line-leading number, then text that is short, title-cased and has no full
# stop. Every heuristic in the scorer rates it well, and it scored 0.55 against
# a 0.55 threshold — so the nine-page cost annex of the Jaglot-Skardu claim
# produced 128 "clause headings", and 136 of its 137 chunks were flagged as
# complete clauses. Search and citations were polluted with clauses that do not
# exist.
#
# The rows below are verbatim from that annex, as OCR reads them.
# ---------------------------------------------------------------------------

JAGLOT_BILL_OF_QUANTITIES = """Anx - A
Cost Effect Land Slides and Mudflow Damages - JSR Proj
Sr.No Description A/U Qty Rate (Rs) Amount (Rs) Remarks
1 Excavate surplus Unclassified rock material Cuml 38220.46 814.92 31,146,613
2 AggregateBase Cum 647.72 2602.7 1,685,808
3 Prime Coat Sm 4233.60 116.24 492,114
4 Asphaltic Base CoursePlant mix(Class B) Cum 338.69 19336.03 6,548,881
5 TackCoat Sm 4233.60 54.88 232,340
8 Concrete ClassA1(Shoulders) Cum 18.75 11600.73 217,514
10 RetaingWall StoneMasonary Cum 4801.14 4984.56 23,931,570
14 MetalGuardRail Mtr 1065.00 5661.4 6,029,391
15 SteelPostforGuardRail Nos 550.00 6728.02 3,700,411
"""

JAGLOT_MEASUREMENT_SHEET = """Anx-B
Detail of Landslides and Mudflow Damages JSR Proj
MeasurementSheet
Ser Item From RD To Length (Mtr) Width Avg Hight Avg (Cum) Qty Remarks
1 Landslides 010+500 010+650 150.00 4.00 0.80 480.00
2 Mudflow 017+390 017+425 35.00 3.65 0.50 63.88
12 Mudflow+Boulders 042+985 043+015 30.00 10.50 1.75 551.25
19 Mudflow 047+615 047+785 170.00 13.00 7.00 15470.00
24 Landslides 079+525 079+675 150.00 10.00 7.00 10500.00
"""


def test_a_bill_of_quantities_yields_no_clauses() -> None:
    """The defect, in one assertion."""
    headings, _skipped = detect_headings(
        [PageText(page_number=1, text=JAGLOT_BILL_OF_QUANTITIES)]
    )
    assert [h.title for h in headings] == []


def test_a_measurement_sheet_yields_no_clauses() -> None:
    headings, _skipped = detect_headings(
        [PageText(page_number=2, text=JAGLOT_MEASUREMENT_SHEET)]
    )
    assert [h.title for h in headings] == []


@pytest.mark.parametrize(
    "row",
    [
        "2 AggregateBase Cum 647.72 2602.7 1,685,808",
        "3 Prime Coat Sm 4233.60 116.24 492,114",
        "15 SteelPostforGuardRail Nos 550.00 6728.02 3,700,411",
        "1 Landslides 010+500 010+650 150.00 4.00 0.80 480.00",
        "19 Mudflow 047+615 047+785 170.00 13.00 7.00 15470.00",
    ],
)
def test_individual_table_rows_are_rejected(row: str) -> None:
    headings, _skipped = detect_headings([PageText(page_number=1, text=row)])
    assert headings == []


@pytest.mark.parametrize(
    "line,number,title",
    [
        ("20.2.1 Notice of Claim", "20.2.1", "Notice of Claim"),
        ("14.3 Application for Interim Payment", "14.3", "Application for Interim Payment"),
        ("8.5 Extension of Time for Completion", "8.5", "Extension of Time for Completion"),
        ("53.1 Notice of Claims", "53.1", "Notice of Claims"),
        ("4.1 Impartiality", "4.1", "Impartiality"),
    ],
)
def test_real_clause_headings_survive(line: str, number: str, title: str) -> None:
    """The rejection must cost nothing in the standard forms themselves.

    Measured on the published books: the 1987 Red Book yields the same 107
    headings before and after, and the only lines the 2017 Red Book, Yellow
    Book and Silver Book lose are flowchart cross-reference lists — which were
    never headings.
    """
    headings, _skipped = detect_headings([PageText(page_number=1, text=line)])
    assert [(h.number, h.title) for h in headings] == [(number, title)]


def test_a_heading_may_still_state_one_figure() -> None:
    """A clause heading naming a period or a percentage is not a table row."""
    page = PageText(page_number=1, text="14.2 Advance Payment of 10 per cent")
    headings, _skipped = detect_headings([page])
    assert [h.title for h in headings] == ["Advance Payment of 10 per cent"]


def test_a_table_row_is_recognised_on_its_own() -> None:
    """The predicate is public so a disputed extraction can be explained."""
    assert looks_like_table_row("AggregateBase Cum 647.72 2602.7 1,685,808")
    assert looks_like_table_row("Mudflow 017+390 017+425 35.00 3.65 0.50 63.88")
    assert not looks_like_table_row("Claims for Payment and/or EOT")
    assert not looks_like_table_row("Notice of Claim")


def test_quantities_are_counted_but_words_are_not() -> None:
    count, ratio = numeric_profile("AggregateBase Cum 647.72 2602.7 1,685,808")
    assert count == 3
    assert ratio == pytest.approx(3 / 5)
    assert numeric_profile("Agreement or Determination") == (0, 0.0)


def test_a_bill_of_quantities_page_is_still_content() -> None:
    """Its rows are not clauses, but its figures are the claim's quantum.

    Classification drives chunking as well as heading detection, so excluding
    the page here would drop the costs being claimed out of retrieval
    altogether — a worse failure than the one being fixed.
    """
    assert classify_page(PageText(page_number=1, text=JAGLOT_BILL_OF_QUANTITIES)) is True
