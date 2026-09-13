"""Tests for RetrievalScope — the edition-safety invariant of ADR 0004.

These encode the specific failures observed in the legacy prototype:
a defaulted edition parameter, and an endpoint that omitted the filter.
"""
from __future__ import annotations

import inspect
from uuid import uuid4

import pytest

from claimiq.core.domain.errors import PermissionDeniedError, ScopeValidationError
from claimiq.search.domain import scope as scope_module
from claimiq.search.domain.scope import (
    RetrievalScope,
    SourceKind,
    combined_scope,
    knowledge_base_scope,
    project_scope,
)

ORG = uuid4()


def test_knowledge_base_without_edition_is_refused() -> None:
    """The central invariant. Refused, not defaulted."""
    with pytest.raises(ScopeValidationError) as exc:
        RetrievalScope(
            organization_id=ORG,
            sources=frozenset({SourceKind.KNOWLEDGE_BASE}),
        )
    assert "explicit edition" in str(exc.value)
    assert exc.value.code == "retrieval_scope_invalid"


def test_knowledge_base_with_edition_is_valid() -> None:
    built = knowledge_base_scope(organization_id=ORG, edition="red-book-2017")
    assert built.reads_knowledge_base is True
    assert built.knowledge_base_edition == "red-book-2017"


def test_edition_without_knowledge_base_source_is_refused() -> None:
    """Catches the caller who set an edition but forgot the source."""
    with pytest.raises(ScopeValidationError):
        RetrievalScope(
            organization_id=ORG,
            sources=frozenset({SourceKind.PROJECT_DOCUMENT}),
            project_id=(pid := uuid4()),
            accessible_project_ids=frozenset({pid}),
            knowledge_base_edition="red-book-2017",
        )


def test_empty_sources_are_refused() -> None:
    with pytest.raises(ScopeValidationError):
        RetrievalScope(organization_id=ORG, sources=frozenset())


def test_no_retrieval_entry_point_defaults_an_edition() -> None:
    """Regression guard for the prototype's `edition: str = "2017"`.

    Asserted structurally rather than by review, so a future contributor cannot
    reintroduce a default without a test failing.
    """
    for name, obj in vars(scope_module).items():
        if not inspect.isfunction(obj) or name.startswith("_"):
            continue
        signature = inspect.signature(obj)
        for param_name, param in signature.parameters.items():
            if "edition" in param_name:
                assert param.default is inspect.Parameter.empty, (
                    f"{name}() gives {param_name} a default. An edition default "
                    f"silently decides which contract governs (ADR 0004)."
                )


# ---------------------------------------------------------------------------
# Project scoping and access control
# ---------------------------------------------------------------------------


def test_project_documents_require_a_project_or_documents() -> None:
    with pytest.raises(ScopeValidationError) as exc:
        RetrievalScope(
            organization_id=ORG,
            sources=frozenset({SourceKind.PROJECT_DOCUMENT}),
        )
    assert "project_id" in str(exc.value)


def test_inaccessible_project_is_refused_at_construction() -> None:
    with pytest.raises(PermissionDeniedError):
        RetrievalScope(
            organization_id=ORG,
            sources=frozenset({SourceKind.PROJECT_DOCUMENT}),
            project_id=uuid4(),
            accessible_project_ids=frozenset({uuid4()}),
        )


def test_project_filter_never_exceeds_accessible_set() -> None:
    accessible = {uuid4(), uuid4(), uuid4()}
    target = next(iter(accessible))
    built = project_scope(
        organization_id=ORG,
        project_id=target,
        accessible_project_ids=accessible,
    )
    assert built.project_filter() == frozenset({target})
    assert built.project_filter() <= frozenset(accessible)


def test_document_ids_only_scope_is_allowed() -> None:
    built = RetrievalScope(
        organization_id=ORG,
        sources=frozenset({SourceKind.PROJECT_DOCUMENT}),
        document_ids=frozenset({uuid4()}),
    )
    assert built.reads_project_documents is True


# ---------------------------------------------------------------------------
# Narrowing
# ---------------------------------------------------------------------------


def test_narrow_to_documents_intersects() -> None:
    a, b, c = uuid4(), uuid4(), uuid4()
    built = RetrievalScope(
        organization_id=ORG,
        sources=frozenset({SourceKind.PROJECT_DOCUMENT}),
        document_ids=frozenset({a, b}),
    )
    narrowed = built.narrow_to_documents({b, c})
    assert narrowed.document_ids == frozenset({b}), "narrowing must only remove reach"


def test_narrow_to_clauses_accumulates() -> None:
    built = knowledge_base_scope(organization_id=ORG, edition="red-book-2017")
    narrowed = built.narrow_to_clauses(["20.2.1"]).narrow_to_clauses(["20.2.2"])
    assert narrowed.clause_numbers == frozenset({"20.2.1", "20.2.2"})


def test_narrowing_preserves_edition() -> None:
    pid = uuid4()
    built = combined_scope(
        organization_id=ORG,
        project_id=pid,
        edition="red-book-1987",
        accessible_project_ids={pid},
    )
    assert built.narrow_to_clauses(["53.1"]).knowledge_base_edition == "red-book-1987"


def test_knowledge_base_only_drops_project_reach() -> None:
    pid = uuid4()
    built = combined_scope(
        organization_id=ORG,
        project_id=pid,
        edition="red-book-2017",
        accessible_project_ids={pid},
    )
    narrowed = built.knowledge_base_only()
    assert narrowed.reads_project_documents is False
    assert narrowed.project_id is None
    assert narrowed.knowledge_base_edition == "red-book-2017"


def test_knowledge_base_only_refused_when_absent() -> None:
    pid = uuid4()
    built = project_scope(
        organization_id=ORG, project_id=pid, accessible_project_ids={pid}
    )
    with pytest.raises(ScopeValidationError):
        built.knowledge_base_only()


def test_scope_is_immutable() -> None:
    built = knowledge_base_scope(organization_id=ORG, edition="red-book-2017")
    with pytest.raises(Exception):
        built.knowledge_base_edition = "red-book-1987"  # type: ignore[misc]


def test_describe_names_the_edition() -> None:
    pid = uuid4()
    built = combined_scope(
        organization_id=ORG,
        project_id=pid,
        edition="red-book-1987",
        accessible_project_ids={pid},
    )
    assert "kb=red-book-1987" in built.describe()
