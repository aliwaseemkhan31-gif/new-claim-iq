"""Reading notices and splitting claim bundles.

The model reads; these rules decide what to believe. Each test pins one rule
that keeps a wrong or missing reading from reaching a deadline.
"""
from __future__ import annotations

from datetime import date

from claimiq.claims.domain.intake import (
    DETAILED_PARTICULARS,
    NOTICE_OF_CLAIM,
    PageText,
    build_notice_reading,
    dates_in,
    heuristic_segments,
    merge_segments,
    notice_kind_from_text,
    read_date,
)

NOTICE = """GHAZI BAROTHA CONTRACTORS
Our Ref: E1&2-823/7565                         Date: 12 September 1998
The Engineer
Subject: Notice of delay - Tarmari borrow area not handed over
Dear Sirs,
We hereby give notice under Sub-Clause 44.2 of the Conditions of Contract that
the borrow area required by 2 January 1998 has not been handed over.
Detailed particulars will follow under Sub-Clause 44.2(b).
RECEIVED 14 September 1998"""


# ---------------------------------------------------------------------------
# Notices
# ---------------------------------------------------------------------------


def test_dates_run_together_by_ocr_are_still_read() -> None:
    assert read_date("12September 1998") == (date(1998, 9, 12), "")
    assert dates_in("Date:12September 1998")[0][1] == date(1998, 9, 12)


def test_a_notice_is_read_without_a_model() -> None:
    reading = build_notice_reading({}, text=NOTICE)
    assert reading.value("letter_date") == date(1998, 9, 12)
    assert reading.value("received_date") == date(1998, 9, 14)
    assert reading.value("reference") == "E1&2-823/7565"
    assert "44.2" in reading.value("clauses")
    assert reading.value("notice_kind") == NOTICE_OF_CLAIM


def test_promising_particulars_does_not_make_a_letter_the_particulars() -> None:
    assert notice_kind_from_text(NOTICE) == NOTICE_OF_CLAIM
    assert (
        notice_kind_from_text("We herewith submit the detailed particulars of our claim.")
        == DETAILED_PARTICULARS
    )


def test_a_model_date_the_page_does_not_bear_is_replaced_by_the_pages_own() -> None:
    reading = build_notice_reading(
        {"letter_date": "2 January 1998", "quotes": {"letter_date": "Date: 2 January 1998"}},
        text=NOTICE,
    )
    item = reading.fields["letter_date"]
    # "Date: 2 January 1998" is not on the page; the letter's own date is.
    assert item.value == date(1998, 9, 12)
    assert item.source == "pattern"


def test_a_verified_model_reading_is_kept() -> None:
    reading = build_notice_reading(
        {
            "letter_date": "12 September 1998",
            "reference": "Our Ref: E1&2-823/7565",
            "notice_kind": "notice_of_claim",
            "quotes": {
                "letter_date": "Date: 12 September 1998",
                "reference": "Our Ref: E1&2-823/7565",
            },
        },
        text=NOTICE,
    )
    assert reading.fields["letter_date"].source == "model"
    assert reading.fields["letter_date"].verified
    # The label a model keeps is stripped from the reference.
    assert reading.value("reference") == "E1&2-823/7565"


# ---------------------------------------------------------------------------
# Bundles
# ---------------------------------------------------------------------------

BUNDLE = [
    PageText(1, "Our Ref: E1-901   Date: 15 March 1999\nDear Sirs,\nThis letter constitutes our claim for an extension of time."),
    PageText(2, "2. We request an extension of 420 days.\nYours faithfully"),
    PageText(3, "ANNEX A - Notice of delay\nDate: 10 June 1997\nWe hereby give notice of delay."),
    PageText(4, "ANNEX B - Programme\nActivity ID  Start  Finish\nCritical path"),
    PageText(5, "ANNEX C - Daily Report 22 July 1998\nWeather fine."),
    PageText(6, "Daily Report 3 January 1999\nLabour on site 210."),
    PageText(7, "ANNEX D - Invoice No. 4471   Date: 25 November 1998\nAmount due"),
]


def test_a_bundle_splits_by_its_headings_without_a_model() -> None:
    segments = heuristic_segments(BUNDLE)
    assert [(s.first_page, s.last_page, s.kind) for s in segments] == [
        (1, 2, "claim_letter"),
        (3, 3, "notice"),
        (4, 4, "programme"),
        (5, 6, "site_record"),
        (7, 7, "invoice_cost"),
    ]
    assert segments[0].date_value == date(1999, 3, 15)


def test_a_models_split_always_covers_every_page_once() -> None:
    proposed = [
        {"first_page": 1, "last_page": 1, "kind": "claim_letter"},
        {"first_page": 2, "last_page": 2, "kind": "correspondence"},
        {"first_page": 3, "last_page": 5, "kind": "notice"},
        {"first_page": 4, "last_page": 4, "kind": "programme"},  # overlaps
        {"first_page": 40, "last_page": 50, "kind": "other"},  # off the end
    ]
    segments, notes = merge_segments(proposed, BUNDLE)
    covered = [n for s in segments for n in range(s.first_page, s.last_page + 1)]
    assert covered == list(range(1, 8))
    assert notes  # the uncovered tail is reported


def test_a_continuation_page_is_joined_to_the_letter() -> None:
    proposed = [
        {"first_page": 1, "last_page": 1, "kind": "claim_letter"},
        {"first_page": 2, "last_page": 2, "kind": "correspondence"},
        {"first_page": 3, "last_page": 7, "kind": "notice"},
    ]
    segments, _ = merge_segments(proposed, BUNDLE)
    assert (segments[0].first_page, segments[0].last_page) == (1, 2)


def test_a_title_or_date_the_page_does_not_bear_is_dropped() -> None:
    proposed = [
        {"first_page": 1, "last_page": 7, "kind": "claim_letter", "title": "Invented title", "date": "1 April 2001"},
    ]
    (segment,), _ = merge_segments(proposed, BUNDLE)
    assert segment.title != "Invented title"
    assert segment.date_value == date(1999, 3, 15)
