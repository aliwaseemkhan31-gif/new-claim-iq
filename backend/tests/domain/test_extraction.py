"""Tests for drafting a claim from the text of a claim document.

The strings below are real: they are what RapidOCR read off photographs of a
bound claim submission for the Indus Highway N-55 project. Two defects in this
module were found by running it against them, and both are guarded here — a
parser that concatenated three columns of a summary table into a single
twenty-four digit amount, and one that read "53" out of
"CLAUSE 53.3OF CONTRACT".

The property that matters throughout: a field the document does not state must
come back absent. An invented value is worse than a blank, because a blank is
obviously missing and an invented one is not.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from claimiq.claims.domain.extraction import (
    CLAIM_TYPES,
    EXTRACTABLE_FIELDS,
    build_draft,
    draft_payload,
    normalise_amount,
    normalise_claim_type,
    normalise_clauses,
    normalise_currency,
    normalise_date,
    normalise_days,
    normalise_text,
    quote_appears,
)

# What OCR actually read off the two photographed pages.
COVER = (
    "CONSTRUCTION OF ACW OF INDUS HIGWAY N55\n"
    "SEHWAN-RATODEROIZOMKMIPRC\n"
    "CLAIM NO1(REVISED\n"
    "ENGINEER'S DECISION UHDER CLAUSE\n"
    "RE-DETERMINATION OF CLAIM MOL COMPENSATON\n"
    "OF OVERHEADS & IDLING OF RESOURCES ON ACCOUNT OF DELAY\n"
    "Dated:July 2,2013"
)
SUMMARY = (
    "CLAIM-1(UNDER CLAUSE 53.3OF CONTRACT)\n"
    "SER SECTION REACH MENPOWER PLANT/EQUIPMENT/ VEHICALS/MT TOTAL REMARKS\n"
    "a Section-1 0+000 to47+000 28,529,843 32,972,199 61,502,043\n"
    "TOTALAMOUNTRS. 39,228,535 45,336,774 84,565,309"
)


# ---------------------------------------------------------------------------
# Amounts
# ---------------------------------------------------------------------------


def test_a_currency_prefix_does_not_become_a_decimal_point() -> None:
    """"Rs. 84,565,309" parsed as 0.84565309 while the dot was being stripped."""
    assert normalise_amount("Rs. 84,565,309") == (Decimal("84565309"), "")
    assert normalise_amount("PKR 381,913,687")[0] == Decimal("381913687")


def test_a_row_of_a_summary_table_is_refused_rather_than_run_together() -> None:
    """This produced 285298433297219961502043 — a number on no page anywhere."""
    value, note = normalise_amount("TOTAL AMOUNT RS. 28,529,843 32,972,199 61,502,043")
    assert value is None
    assert "Several amounts" in note
    for candidate in ("28,529,843", "32,972,199", "61,502,043"):
        assert candidate in note


def test_amounts_written_in_millions_are_read() -> None:
    assert normalise_amount("9.828 Mn")[0] == Decimal("9828000.000")
    assert normalise_amount("Amount in Mn 9.828")[0] == Decimal("9828000.000")


def test_decimals_and_plain_numbers_are_read() -> None:
    assert normalise_amount("9,828,471.08")[0] == Decimal("9828471.08")
    assert normalise_amount(4500000)[0] == Decimal("4500000")


@pytest.mark.parametrize("value", ["nil", "not stated", "-", "N/A", "null", "unknown"])
def test_a_word_meaning_not_stated_is_read_as_not_stated(value: str) -> None:
    """Asked for JSON null, a small model writes the word instead.

    Taken literally this gave a claim a claimant called "null" and a currency
    of "NUL". These are absences, and an absence needs no note.
    """
    amount, note = normalise_amount(value)
    assert amount is None
    assert note == ""
    assert normalise_text(value) is None
    assert normalise_currency(value) is None
    assert normalise_date(value) == (None, "")


@pytest.mark.parametrize("value", ["about two crore", "see annexure C"])
def test_text_that_should_be_an_amount_but_cannot_be_read_is_reported(value: str) -> None:
    """Distinct from an absence: something is written and could not be read."""
    amount, note = normalise_amount(value)
    assert amount is None
    assert note


def test_a_zero_or_negative_amount_is_not_a_claim() -> None:
    assert normalise_amount("0")[0] is None
    assert normalise_amount(Decimal("-5"))[0] is None


# ---------------------------------------------------------------------------
# Clause numbers
# ---------------------------------------------------------------------------


def test_ocr_running_the_next_word_onto_a_clause_number_does_not_truncate_it() -> None:
    """"CLAUSE 53.3OF CONTRACT" yielded "53" under a word-boundary anchor."""
    assert normalise_clauses("CLAIM-1(UNDER CLAUSE 53.3OF CONTRACT)")[0] == ("53.3",)


def test_the_claim_number_is_not_mistaken_for_a_clause() -> None:
    """"CLAIM-1" sits beside the clause on the same line."""
    clauses, _ = normalise_clauses("CLAIM NO 1 (REVISED) DECISION UNDER CLAUSE 67.1")
    assert clauses == ("67.1",)


def test_a_list_sharing_one_keyword_keeps_every_clause() -> None:
    assert normalise_clauses("under Clauses 44.1 and 53.1 of the Conditions")[0] == (
        "44.1",
        "53.1",
    )


def test_a_bare_list_of_numbers_is_read_as_clauses() -> None:
    assert normalise_clauses("44.1, 53.1")[0] == ("44.1", "53.1")
    assert normalise_clauses(["44.1", "53.1"])[0] == ("44.1", "53.1")


def test_a_clause_without_a_sub_number_is_kept() -> None:
    assert normalise_clauses("Clause 8")[0] == ("8",)
    assert normalise_clauses("Sub-Clause 20.2.1")[0] == ("20.2.1",)


def test_text_holding_no_clause_number_is_reported() -> None:
    clauses, note = normalise_clauses("under the Conditions of Contract")
    assert clauses == ()
    assert note


# ---------------------------------------------------------------------------
# Dates, days, currency, type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("July 2, 2013", date(2013, 7, 2)),
        ("2 July 2013", date(2013, 7, 2)),
        ("2013-07-02", date(2013, 7, 2)),
        ("02/07/2013", date(2013, 7, 2)),
        ("2nd July 2013", date(2013, 7, 2)),
    ],
)
def test_dates_are_read_as_a_claim_document_writes_them(text: str, expected: date) -> None:
    assert normalise_date(text) == (expected, "")


def test_an_unreadable_date_is_reported_rather_than_dropped() -> None:
    """Silently dropping it reads as a document that stated no date."""
    value, note = normalise_date("sometime in the monsoon")
    assert value is None
    assert note


def test_a_claim_type_is_accepted_by_code_or_by_label() -> None:
    assert normalise_claim_type("cost") == ("cost", "")
    assert normalise_claim_type("Additional Cost") == ("cost", "")
    assert normalise_claim_type("Extension of Time") == ("eot", "")


def test_an_unrecognised_claim_type_is_dropped_not_coerced() -> None:
    """The type decides which elements the claim must establish."""
    value, note = normalise_claim_type("prolongation and disruption claim")
    assert value is None
    assert note
    assert value not in CLAIM_TYPES


def test_currency_is_recognised_from_how_a_document_writes_it() -> None:
    assert normalise_currency("Rs.") == "PKR"
    assert normalise_currency("rupees") == "PKR"
    assert normalise_currency("USD") == "USD"


def test_days_are_read_and_a_non_positive_period_refused() -> None:
    assert normalise_days("20 days") == (20, "")
    assert normalise_days("0")[0] is None


# ---------------------------------------------------------------------------
# Quote checking
# ---------------------------------------------------------------------------


def test_a_quote_is_matched_through_ocr_spacing() -> None:
    """OCR moves spaces without losing characters."""
    assert quote_appears("CLAIM-1 (UNDER CLAUSE 53.3 OF CONTRACT)", SUMMARY)


def test_a_quote_that_is_not_in_the_document_is_caught() -> None:
    assert not quote_appears("TOTAL AMOUNT RS. 99,999,999", SUMMARY)


def test_a_very_short_quote_is_not_reported_either_way() -> None:
    """It proves nothing, so it is not held against the field."""
    assert quote_appears("RS.", SUMMARY)


# ---------------------------------------------------------------------------
# Building a draft
# ---------------------------------------------------------------------------


def test_every_extractable_field_appears_in_the_draft() -> None:
    """A field that vanishes reads as a document that did not state it."""
    draft = build_draft({}, source_text=COVER)
    assert [f.name for f in draft.fields] == list(EXTRACTABLE_FIELDS)
    assert draft.found_fields == []


def test_a_draft_reads_the_flat_shape_with_a_quotes_object() -> None:
    draft = build_draft(
        {
            "title": "Compensation of overheads and idling of resources",
            "contractual_basis": "UNDER CLAUSE 53.3OF CONTRACT",
            "submission_date": "July 2, 2013",
            "currency": "Rs.",
            "quotes": {"contractual_basis": "CLAIM-1(UNDER CLAUSE 53.3OF CONTRACT)"},
        },
        source_text=COVER + "\n" + SUMMARY,
    )
    assert draft.value("contractual_basis") == ("53.3",)
    assert draft.value("submission_date") == date(2013, 7, 2)
    assert draft.value("currency") == "PKR"
    assert draft.get("contractual_basis").quote_verified


def test_a_draft_reads_the_nested_shape_too() -> None:
    """Small models produce both against the same prompt."""
    draft = build_draft(
        {"title": {"value": "Compensation of overheads", "quote": "COMPENSATON"}},
        source_text=COVER,
    )
    assert draft.value("title") == "Compensation of overheads"


def test_a_quote_not_found_in_the_document_marks_the_field_to_check() -> None:
    draft = build_draft(
        {"title": {"value": "Claim for acceleration", "quote": "CLAIM FOR ACCELERATION"}},
        source_text=COVER,
    )
    field = draft.get("title")
    assert field.found
    assert not field.quote_verified
    assert draft.unverified_fields == [field]


def test_an_unparseable_value_carries_its_reason_into_the_draft() -> None:
    draft = build_draft(
        {"amount_claimed": "TOTAL AMOUNT RS. 28,529,843 32,972,199 61,502,043"},
        source_text=SUMMARY,
    )
    field = draft.get("amount_claimed")
    assert field.value is None
    assert "Several amounts" in field.note


def test_notes_from_the_model_are_carried_through() -> None:
    draft = build_draft({"notes": ["Claimant and respondent are not stated.", ""]})
    assert draft.notes == ["Claimant and respondent are not stated."]


def test_the_payload_renders_values_the_form_can_take() -> None:
    draft = build_draft(
        {
            "submission_date": "July 2, 2013",
            "amount_claimed": "Rs. 84,565,309",
            "contractual_basis": "Clause 53.3",
        },
        source_text=SUMMARY,
    )
    payload = draft_payload(draft)
    by_name = {f["name"]: f for f in payload["fields"]}
    assert by_name["submission_date"]["value"] == "2013-07-02"
    assert by_name["amount_claimed"]["value"] == "84565309"
    assert by_name["contractual_basis"]["value"] == ["53.3"]
    assert payload["found"] == 3
    assert payload["missing"] == len(EXTRACTABLE_FIELDS) - 3


def test_a_draft_of_nothing_is_a_valid_answer() -> None:
    """A document that states nothing must not be padded out with guesses."""
    draft = build_draft({"notes": ["This does not look like a claim."]}, source_text="x")
    payload = draft_payload(draft)
    assert payload["found"] == 0
    assert all(f["value"] in (None, []) for f in payload["fields"])


def test_a_value_with_no_quotation_is_reported_as_unconfirmed() -> None:
    """Found when qwen2.5:3b returned the prompt's worked example as its answer.

    The four fields it quoted were caught by the quote check; the eight it
    filled in without a quotation passed as verified, which is exactly
    backwards — a value offered with no citation is the one most in need of
    checking.
    """
    draft = build_draft(
        {"title": "ABC Constructors claim", "reference": "CL-07"},
        source_text=COVER,
    )
    assert draft.get("title").found
    assert not draft.get("title").quote_verified
    assert not draft.get("reference").quote_verified
    assert len(draft.unverified_fields) == 2


def test_a_field_the_document_does_not_state_is_not_reported_as_unconfirmed() -> None:
    """Absent is a clean answer, not a doubtful one."""
    draft = build_draft({"title": None}, source_text=COVER)
    assert draft.unverified_fields == []


# ---------------------------------------------------------------------------
# Date formats found on real submissions
# ---------------------------------------------------------------------------
#
# Added after a claim drafted from a scanned FIDIC submission produced no date
# at all. The cause turned out to be upstream — the covering letter fell
# outside the page limit — but the formats below appear on this kind of
# correspondence and were not accepted.


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("14/03/24", date(2024, 3, 14)),
        ("14-03-24", date(2024, 3, 14)),
        ("14.03.24", date(2024, 3, 14)),
        ("14 Mar 24", date(2024, 3, 14)),
        ("14Mar2024", date(2024, 3, 14)),
        ("2024/03/14", date(2024, 3, 14)),
        ("2024.03.14", date(2024, 3, 14)),
        ("2024-03-14T09:30:00", date(2024, 3, 14)),
        ("2024-03-14 09:30:00", date(2024, 3, 14)),
    ],
)
def test_dates_written_as_scanned_correspondence_writes_them(raw, expected) -> None:
    parsed, note = normalise_date(raw)
    assert parsed == expected, note
    assert note == ""


def test_a_day_first_reading_is_preferred() -> None:
    """These are FIDIC contracts outside the United States."""
    parsed, _ = normalise_date("03/04/2024")
    assert parsed == date(2024, 4, 3)


def test_a_month_without_a_day_is_refused_rather_than_assumed() -> None:
    """Inventing the 1st could move a notice period across its deadline."""
    parsed, note = normalise_date("March 2024")
    assert parsed is None
    assert "could not be read as a date" in note


def test_an_unreadable_date_says_so_rather_than_vanishing() -> None:
    parsed, note = normalise_date("U1Jan.2UUU")
    assert parsed is None
    assert note, "a dropped date must not look like a document that stated none"


# ---------------------------------------------------------------------------
# A currency is read or reported absent, never invented
# ---------------------------------------------------------------------------
#
# "Total Amount (Mn)" on a real cost summary yielded a currency of "MN", which
# then sat in the claim record beside the figure and made it look checked.


@pytest.mark.parametrize(
    "raw", ["Mn", "MN", "mn", "QTY", "NOS", "Cum", "Sm", "xyz", "Amount"]
)
def test_a_non_currency_is_not_turned_into_one(raw) -> None:
    assert normalise_currency(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PKR", "PKR"), ("pkr", "PKR"), ("Rs.", "PKR"), ("rupees", "PKR"),
        ("USD", "USD"), ("$", "USD"), ("GBP", "GBP"), ("EUR", "EUR"),
        ("JPY", "JPY"), ("AED", "AED"), ("SAR", "SAR"),
    ],
)
def test_a_real_currency_is_read(raw, expected) -> None:
    assert normalise_currency(raw) == expected


def test_the_iso_set_covers_the_currencies_these_projects_use() -> None:
    from claimiq.claims.domain.extraction import ISO_4217_CODES

    for code in ("PKR", "USD", "EUR", "GBP", "AED", "SAR", "CNY", "TRY"):
        assert code in ISO_4217_CODES
    for fake in ("MN", "QTY", "NOS"):
        assert fake not in ISO_4217_CODES
