"""The same file must not be uploaded twice as two documents.

The existing duplicate check compared a file against the versions of the
document it was being uploaded *to*. Uploading the same bytes again under a new
title therefore passed, and one installation accumulated the Silver Book three
times (one of them stuck in processing under the typo "Silver Bool") and the
Yellow Book twice. Each copy was separately chunked, embedded and indexed, so
the same clause came back three times in a result set and could be cited three
times in one answer.

These tests exercise the refusal itself without a database: what matters is
that the duplicate is found and that the refusal carries enough for the UI to
name the existing document and offer to open it.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from claimiq.core.domain.errors import ConflictError
from claimiq.documents.services import upload as upload_service


class _FakeQuerySet:
    """Just enough of the chained queryset the check uses."""

    def __init__(self, result):
        self._result = result
        self.filters = {}

    def filter(self, **kwargs):
        self.filters.update(kwargs)
        return self

    def exclude(self, **kwargs):
        return self

    def select_related(self, *args):
        return self

    def order_by(self, *args):
        return self

    def first(self):
        return self._result


@pytest.fixture
def existing_version():
    document = SimpleNamespace(
        id="d0000000-0000-0000-0000-000000000001",
        title="FIDIC Silver Book 1999",
        document_type="contract",
    )
    return SimpleNamespace(
        document=document,
        document_id=document.id,
        version_number=1,
        created_at=SimpleNamespace(isoformat=lambda: "2026-03-03T09:00:00+00:00"),
    )


def _check(monkeypatch, result):
    monkeypatch.setattr(
        upload_service.DocumentVersion,
        "objects",
        _FakeQuerySet(result),
        raising=False,
    )
    project = SimpleNamespace(pk="p1")
    return lambda: upload_service._refuse_duplicate_of_another_document(
        project, "a" * 64, exclude=SimpleNamespace(pk="d2")
    )


def test_a_file_already_in_the_project_is_refused(monkeypatch, existing_version) -> None:
    with pytest.raises(ConflictError) as caught:
        _check(monkeypatch, existing_version)()

    error = caught.value
    assert "FIDIC Silver Book 1999" in error.message
    assert error.details["reason"] == "duplicate_of_another_document"
    assert error.details["document_title"] == "FIDIC Silver Book 1999"
    # The UI offers to open the existing document, so it needs its id.
    assert error.details["document_id"] == "d0000000-0000-0000-0000-000000000001"


def test_a_file_not_seen_before_passes(monkeypatch) -> None:
    _check(monkeypatch, None)()  # must not raise


def test_the_refusal_says_how_to_proceed(monkeypatch, existing_version) -> None:
    """A refusal with no way forward is a dead end; both copies may be wanted."""
    with pytest.raises(ConflictError) as caught:
        _check(monkeypatch, existing_version)()
    assert "allow_duplicate" in caught.value.details["remedy"]


def test_the_check_only_looks_within_the_project(monkeypatch, existing_version) -> None:
    """Another project's copy is not a duplicate: projects are separate records,
    and the same standard form legitimately appears on many of them."""
    queryset = _FakeQuerySet(existing_version)
    monkeypatch.setattr(upload_service.DocumentVersion, "objects", queryset, raising=False)
    project = SimpleNamespace(pk="p1")
    with pytest.raises(ConflictError):
        upload_service._refuse_duplicate_of_another_document(
            project, "a" * 64, exclude=SimpleNamespace(pk="d2")
        )
    assert queryset.filters["document__project"] is project
    assert queryset.filters["checksum_sha256"] == "a" * 64
    # Deleted documents are not duplicates of anything.
    assert queryset.filters["document__deleted_at__isnull"] is True


def test_allow_duplicate_is_an_explicit_opt_in() -> None:
    """The service only skips the check when the caller asks it to."""
    import inspect

    signature = inspect.signature(upload_service.upload_document)
    assert signature.parameters["allow_duplicate"].default is False


# ---------------------------------------------------------------------------
# The same file registered as two editions
#
# A knowledge base is already limited to one per edition, which stops the same
# edition being uploaded twice. It does not stop the opposite mistake: one file
# registered under two different edition codes. That is how the Silver Book
# came to be held three times, and it is not merely clutter — retrieval is
# edition-scoped so that a 2017 question is never answered from 1999 text
# (ADR 0004), and a file filed under the wrong edition defeats that silently.
# ---------------------------------------------------------------------------

from claimiq.knowledge.services import build as build_service  # noqa: E402


class _FakeKbQuerySet:
    def __init__(self, result):
        self._result = result
        self.filters = {}
        self.excluded = None

    def filter(self, **kwargs):
        self.filters.update(kwargs)
        return self

    def exclude(self, **kwargs):
        self.excluded = kwargs
        return self

    def first(self):
        return self._result


@pytest.fixture
def existing_kb():
    return SimpleNamespace(
        pk="k0000000-0000-0000-0000-000000000009",
        edition_code="silver-book-1999",
        name="FIDIC Silver Book 1999",
    )


def test_the_same_file_cannot_become_a_second_edition(monkeypatch, existing_kb) -> None:
    monkeypatch.setattr(
        build_service.KnowledgeBase, "objects", _FakeKbQuerySet(existing_kb), raising=False
    )
    with pytest.raises(ConflictError) as caught:
        build_service._refuse_same_file_under_another_edition("org1", "b" * 64)

    details = caught.value.details
    assert details["reason"] == "duplicate_source_under_another_edition"
    assert details["edition_code"] == "silver-book-1999"
    # The label, not the code, is what makes the mistake legible.
    assert "Silver Book" in details["edition_label"]
    assert "replace its source" in details["remedy"].lower()


def test_a_new_standard_form_is_accepted(monkeypatch) -> None:
    monkeypatch.setattr(
        build_service.KnowledgeBase, "objects", _FakeKbQuerySet(None), raising=False
    )
    build_service._refuse_same_file_under_another_edition("org1", "c" * 64)  # must not raise


def test_replacing_a_source_does_not_trip_over_itself(monkeypatch, existing_kb) -> None:
    """Re-uploading the same file to the base that already holds it is a
    replacement, not a second edition."""
    queryset = _FakeKbQuerySet(None)
    monkeypatch.setattr(build_service.KnowledgeBase, "objects", queryset, raising=False)
    build_service._refuse_same_file_under_another_edition(
        "org1", "b" * 64, exclude_kb_id=existing_kb.pk
    )
    assert queryset.excluded == {"pk": existing_kb.pk}


def test_a_missing_checksum_is_not_treated_as_a_match(monkeypatch, existing_kb) -> None:
    """An empty checksum would otherwise match every base that has none."""
    monkeypatch.setattr(
        build_service.KnowledgeBase, "objects", _FakeKbQuerySet(existing_kb), raising=False
    )
    build_service._refuse_same_file_under_another_edition("org1", "")  # must not raise
