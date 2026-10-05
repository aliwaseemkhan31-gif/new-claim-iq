"""A question must name what it is to be answered from.

The ask endpoint answers two different questions through one request shape: one
about a project's documents, and one about a published standard form on its
own. The serializer is what keeps the two apart, and what refuses a request
that names neither — which would otherwise reach the view with nothing to
retrieve from and fail somewhere less explicable.

No database: this is request validation, and it holds or does not hold without
one.
"""
from __future__ import annotations

from claimiq.ai.api.views import AskSerializer

PROJECT = "22222222-2222-2222-2222-222222222222"
EDITION = "red-book-2017"


def _validate(payload: dict):
    serializer = AskSerializer(data=payload)
    return serializer.is_valid(), serializer


def test_a_project_question_is_accepted() -> None:
    valid, serializer = _validate({"question": "Was notice given in time?", "project": PROJECT})
    assert valid, serializer.errors
    assert str(serializer.validated_data["project"]) == PROJECT


def test_a_standard_form_question_needs_no_project() -> None:
    valid, serializer = _validate(
        {"question": "Within what period must notice be given?", "edition": EDITION}
    )
    assert valid, serializer.errors
    assert serializer.validated_data.get("project") is None
    assert serializer.validated_data["edition"] == EDITION


def test_a_question_naming_neither_is_refused() -> None:
    """Otherwise the request reaches retrieval with no corpus to read."""
    valid, serializer = _validate({"question": "Within what period must notice be given?"})
    assert not valid
    assert "project" in serializer.errors


def test_a_blank_edition_does_not_stand_in_for_one() -> None:
    """The edition picker starts empty; an empty choice is not a choice."""
    valid, _ = _validate({"question": "Within what period must notice be given?", "edition": ""})
    assert not valid


def test_both_may_be_given_so_a_project_question_is_unaffected() -> None:
    """The project path is unchanged: it ignores `edition` and reads the project's own."""
    valid, serializer = _validate(
        {"question": "Was notice given in time?", "project": PROJECT, "edition": EDITION}
    )
    assert valid, serializer.errors
    assert str(serializer.validated_data["project"]) == PROJECT


def test_the_question_itself_is_still_required() -> None:
    valid, serializer = _validate({"edition": EDITION})
    assert not valid
    assert "question" in serializer.errors
