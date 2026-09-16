"""Issue categories must reach every element the gap engine looks for.

Evidence is tied to a required element only through the issue it is attached
to. A required element that no issue category maps to can therefore never be
established, and is reported as a gap on every claim of that type no matter
what is on the record.
"""
from __future__ import annotations

from claimiq.claims.domain.evidence_gaps import (
    REQUIRED_ELEMENTS,
    element_code_for_issue_category,
)
from claimiq.claims.models import ClaimIssue


def _reachable_element_codes() -> set[str]:
    return {
        element_code_for_issue_category(value) for value in ClaimIssue.Category.values
    }


def test_every_required_element_is_reachable_from_an_issue_category() -> None:
    required = {
        element.code
        for elements in REQUIRED_ELEMENTS.values()
        for element in elements
    }
    unreachable = required - _reachable_element_codes()
    assert not unreachable, (
        "No issue category maps to these required elements, so evidence can "
        f"never be attached to them: {sorted(unreachable)}"
    )


def test_every_issue_category_addresses_something() -> None:
    """A category that matches no element silently loses the evidence filed under it."""
    known = {
        element.code
        for elements in REQUIRED_ELEMENTS.values()
        for element in elements
    } | {"other"}
    stray = {
        value
        for value in ClaimIssue.Category.values
        if element_code_for_issue_category(value) not in known
    }
    assert not stray, f"Issue categories addressing no element: {sorted(stray)}"
