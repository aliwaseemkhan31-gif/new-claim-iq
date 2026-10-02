"""A draft must say what the model was not shown.

The failure this guards against is silent truncation. On a real 9-page FIDIC
submission the first eight pages were annexures — cost breakdowns and
measurement sheets — and the covering letter fell outside the page limit. The
draft reported no date, which to a reviewer is indistinguishable from a claim
that states none. Every notice and time-bar check then screened as
unanswerable, and nothing anywhere said why.
"""
from __future__ import annotations

import pytest

from claimiq.claims.services.drafting import (
    MAX_PAGES,
    MAX_PROMPT_CHARS,
    _coverage_notes,
)


def pages(count: int, confidence: float = 0.9) -> list[dict]:
    return [
        {"page_number": i + 1, "characters": 500, "confidence": confidence}
        for i in range(count)
    ]


def test_a_short_document_read_in_full_says_nothing():
    """No note is better than a note nobody needs to read."""
    assert _coverage_notes("short text", pages(2), []) == []


def test_reaching_the_page_limit_is_reported():
    notes = _coverage_notes("text", pages(MAX_PAGES), [])
    assert any("page limit was reached" in n for n in notes)
    assert any("annexures" in n for n in notes)


def test_a_file_left_unopened_is_named():
    notes = _coverage_notes("text", pages(MAX_PAGES), ["claim-part-2.pdf"])
    assert any("claim-part-2.pdf" in n for n in notes)


def test_prompt_truncation_is_reported_with_both_figures():
    text = "x" * (MAX_PROMPT_CHARS + 5_000)
    notes = _coverage_notes(text, pages(3), [])
    note = next(n for n in notes if "characters" in n)
    assert f"{MAX_PROMPT_CHARS:,}" in note
    assert f"{len(text):,}" in note


def test_low_confidence_pages_are_flagged_for_checking():
    notes = _coverage_notes("text", pages(3, confidence=0.3), [])
    assert any("low confidence" in n for n in notes)


def test_confident_pages_are_not_flagged():
    notes = _coverage_notes("text", pages(3, confidence=0.95), [])
    assert not any("low confidence" in n for n in notes)


def test_the_page_limit_is_large_enough_for_an_annexure_led_submission():
    """Six was not: the submission that exposed this runs to nine pages."""
    assert MAX_PAGES >= 9
