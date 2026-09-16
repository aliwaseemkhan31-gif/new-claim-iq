"""Tests for running header, footer and watermark detection."""
from __future__ import annotations

from claimiq.knowledge.domain.running_text import (
    find_running_lines,
    normalise_line,
    strip_running_lines,
)

WATERMARK = "This copy is licensed for review only - not for contract use"
WORDS = ["access", "bonds", "claims", "delay", "engineer", "fees", "goods", "hazards", "insurance", "joint"]


def page(n: int, body: str) -> str:
    return f"Conditions of Contract\n{body}\n{WATERMARK}\nGeneral Conditions {n}"


PAGES = [page(n, f"The provision on {WORDS[n - 1]} applies to the works.") for n in range(1, 11)]


def test_footer_with_changing_page_number_is_one_running_line():
    assert normalise_line("General Conditions 95") == normalise_line("General  conditions 96")


def test_long_lines_differing_only_by_clause_number_stay_distinct():
    """Regression: masking digits everywhere made numbered provisions collide."""
    first = "Sub-Clause 20.1 The Contractor shall give notice of any claim within the period."
    second = "Sub-Clause 20.2 The Contractor shall give notice of any claim within the period."
    assert normalise_line(first) != normalise_line(second)
    pages = [page(n, f"Sub-Clause {n}.1 The Contractor shall comply with the requirements here.") for n in range(1, 11)]
    assert not any("the contractor shall comply" in line for line in find_running_lines(pages))


def test_repeated_header_footer_and_watermark_are_found():
    running = find_running_lines(PAGES)
    assert normalise_line(WATERMARK) in running
    assert normalise_line("Conditions of Contract") in running
    assert normalise_line("General Conditions 7") in running
    assert not any("the provision on" in line for line in running)


def test_short_documents_are_left_alone():
    """Five repeats is the floor, so a three-page letter keeps its letterhead."""
    assert find_running_lines(PAGES[:3]) == frozenset()


def test_lines_on_a_minority_of_pages_are_kept():
    pages = [page(n, "text") if n <= 3 else f"Body {n}\nunique {n}" for n in range(1, 21)]
    assert normalise_line(WATERMARK) not in find_running_lines(pages)


def test_stripping_removes_whole_lines_only():
    running = find_running_lines(PAGES)
    chunk = (
        "Conditions of Contract\n"
        "The Contractor shall comply with the Conditions of Contract as amended.\n"
        f"{WATERMARK}\n"
        "General Conditions 42"
    )
    stripped = strip_running_lines(chunk, running)
    assert stripped == "The Contractor shall comply with the Conditions of Contract as amended."


def test_stripping_with_nothing_to_strip_is_identity():
    assert strip_running_lines("Clause text\n\nmore", frozenset()) == "Clause text\n\nmore"
    assert strip_running_lines("", {"x"}) == ""
