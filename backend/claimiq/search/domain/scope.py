"""Retrieval scope: the validated description of what a query may read.

Every retrieval entry point in the system takes a :class:`RetrievalScope`. There
is no ``edition=`` keyword argument with a default anywhere in this codebase,
because a default is a silent decision about which contract governs. See
ADR 0004.

The scope carries three concerns that must travel together:

1. **What corpus** — project documents, a knowledge base, or both.
2. **Which slice** — edition, project, document type, specific documents, clauses.
3. **Whose access** — the set of projects the requesting principal may read.

They travel together because separating them is how retrieval paths end up
filtering on two of the three. The legacy prototype tagged every knowledge-base
chunk with an edition and then shipped an endpoint that did not filter on it.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Iterable
from uuid import UUID

from claimiq.core.domain.errors import PermissionDeniedError, ScopeValidationError


class SourceKind(str, Enum):
    """A corpus that retrieval may draw from."""

    PROJECT_DOCUMENT = "project_document"
    """Documents uploaded to a project: contracts, claims, correspondence, evidence."""

    KNOWLEDGE_BASE = "knowledge_base"
    """Standard-form contract text, e.g. the FIDIC Red Book. Edition-specific."""


def _frozen(values: Iterable[Any] | None) -> frozenset[Any]:
    return frozenset(values) if values else frozenset()


@dataclass(frozen=True)
class RetrievalScope:
    """An immutable, validated description of a retrieval request's reach.

    Construction validates. An instance that exists is safe to execute; there is
    no separate ``validate()`` step a caller can forget.

    Attributes:
        organization_id: Tenant boundary. Always required.
        sources: Which corpora to search. Must be non-empty.
        accessible_project_ids: Projects the principal may read. Enforced as a
            SQL predicate by the repository, and checked here so an
            out-of-scope project fails at construction rather than at query time.
        knowledge_base_edition: Edition code, e.g. ``red-book-2017``. Mandatory
            whenever ``sources`` includes ``KNOWLEDGE_BASE``.
        project_id: Restrict project-document retrieval to one project.
        document_ids: Restrict to specific documents.
        document_type_codes: Restrict by document taxonomy code.
        clause_numbers: Restrict to chunks associated with these clauses.
        include_superseded: Include superseded document versions. Defaults to
            False so retrieval reads current text unless a caller is explicitly
            doing historical or comparison work.
    """

    organization_id: UUID
    sources: frozenset[SourceKind]
    accessible_project_ids: frozenset[UUID] = field(default_factory=frozenset)
    knowledge_base_edition: str | None = None
    project_id: UUID | None = None
    document_ids: frozenset[UUID] = field(default_factory=frozenset)
    document_type_codes: frozenset[str] = field(default_factory=frozenset)
    clause_numbers: frozenset[str] = field(default_factory=frozenset)
    include_superseded: bool = False

    def __post_init__(self) -> None:
        if not self.sources:
            raise ScopeValidationError(
                "A retrieval scope must name at least one source.",
                details={"valid_sources": [s.value for s in SourceKind]},
            )

        # ADR 0004: the central invariant. Reaching a knowledge base without
        # naming an edition is refused, never defaulted.
        if SourceKind.KNOWLEDGE_BASE in self.sources and not self.knowledge_base_edition:
            raise ScopeValidationError(
                "Knowledge-base retrieval requires an explicit edition. "
                "Refusing to select one on the caller's behalf: editions differ "
                "materially and the wrong one yields a confidently wrong answer.",
                details={"remedy": "Set knowledge_base_edition, e.g. 'red-book-2017'."},
            )

        # An edition on a scope that never reads the KB is a caller mistake and
        # usually means the KB source was omitted by accident.
        if self.knowledge_base_edition and SourceKind.KNOWLEDGE_BASE not in self.sources:
            raise ScopeValidationError(
                "knowledge_base_edition was set but KNOWLEDGE_BASE is not among "
                "the sources.",
                details={"edition": self.knowledge_base_edition},
            )

        if SourceKind.PROJECT_DOCUMENT in self.sources:
            if self.project_id is None and not self.document_ids:
                raise ScopeValidationError(
                    "Project-document retrieval requires either a project_id or "
                    "an explicit set of document_ids. An unbounded search across "
                    "every project in the organization is not permitted.",
                )
            if self.project_id is not None and self.project_id not in self.accessible_project_ids:
                raise PermissionDeniedError(
                    "The requested project is outside the principal's accessible set.",
                    details={"project_id": str(self.project_id)},
                )

    # ------------------------------------------------------------------
    # Predicates used by repositories and the assembler
    # ------------------------------------------------------------------

    @property
    def reads_knowledge_base(self) -> bool:
        return SourceKind.KNOWLEDGE_BASE in self.sources

    @property
    def reads_project_documents(self) -> bool:
        return SourceKind.PROJECT_DOCUMENT in self.sources

    def project_filter(self) -> frozenset[UUID]:
        """Project ids that project-document retrieval may touch.

        Narrowed to the single scoped project when one is set, otherwise the
        full accessible set. Never wider than what the principal may read.
        """
        if self.project_id is not None:
            return frozenset({self.project_id})
        return self.accessible_project_ids

    def describe(self) -> str:
        """Short human-readable description, for audit logs and AI observability."""
        parts: list[str] = []
        if self.reads_project_documents:
            if self.project_id:
                parts.append(f"project={self.project_id}")
            if self.document_ids:
                parts.append(f"documents={len(self.document_ids)}")
            if self.document_type_codes:
                parts.append(f"types={','.join(sorted(self.document_type_codes))}")
        if self.reads_knowledge_base:
            parts.append(f"kb={self.knowledge_base_edition}")
        if self.clause_numbers:
            parts.append(f"clauses={','.join(sorted(self.clause_numbers))}")
        return " ".join(parts) if parts else "empty"

    # ------------------------------------------------------------------
    # Narrowing
    #
    # Only narrowing is offered. There is no widening helper: widening a scope
    # after a permission check is how a filter gets lost.
    # ------------------------------------------------------------------

    def narrow_to_clauses(self, clause_numbers: Iterable[str]) -> RetrievalScope:
        """Return a copy additionally restricted to ``clause_numbers``."""
        incoming = _frozen(clause_numbers)
        combined = self.clause_numbers | incoming if self.clause_numbers else incoming
        return replace(self, clause_numbers=combined)

    def narrow_to_documents(self, document_ids: Iterable[UUID]) -> RetrievalScope:
        """Return a copy restricted to ``document_ids``.

        If the scope already names documents, the result is the intersection —
        narrowing can only ever remove reach.
        """
        incoming = _frozen(document_ids)
        if self.document_ids:
            incoming = self.document_ids & incoming
        return replace(self, document_ids=incoming)

    def knowledge_base_only(self) -> RetrievalScope:
        """Return a copy that reads only the knowledge base.

        Used by clause-explanation flows that must not pull project text.
        """
        if not self.reads_knowledge_base:
            raise ScopeValidationError(
                "Cannot narrow to knowledge base: this scope does not include it."
            )
        return replace(
            self,
            sources=frozenset({SourceKind.KNOWLEDGE_BASE}),
            project_id=None,
            document_ids=frozenset(),
            document_type_codes=frozenset(),
        )


# --------------------------------------------------------------------------
# Constructors
#
# Named constructors express intent and make the edition requirement obvious at
# every call site.
# --------------------------------------------------------------------------


def project_scope(
    *,
    organization_id: UUID,
    project_id: UUID,
    accessible_project_ids: Iterable[UUID],
    document_type_codes: Iterable[str] | None = None,
) -> RetrievalScope:
    """Search one project's documents only. Does not read any knowledge base."""
    return RetrievalScope(
        organization_id=organization_id,
        sources=frozenset({SourceKind.PROJECT_DOCUMENT}),
        project_id=project_id,
        accessible_project_ids=_frozen(accessible_project_ids),
        document_type_codes=_frozen(document_type_codes),
    )


def knowledge_base_scope(
    *,
    organization_id: UUID,
    edition: str,
    accessible_project_ids: Iterable[UUID] | None = None,
    clause_numbers: Iterable[str] | None = None,
) -> RetrievalScope:
    """Search a knowledge base edition only.

    ``edition`` is positional-by-keyword and has no default, so this cannot be
    called without deciding which contract form governs.
    """
    return RetrievalScope(
        organization_id=organization_id,
        sources=frozenset({SourceKind.KNOWLEDGE_BASE}),
        knowledge_base_edition=edition,
        accessible_project_ids=_frozen(accessible_project_ids),
        clause_numbers=_frozen(clause_numbers),
    )


def combined_scope(
    *,
    organization_id: UUID,
    project_id: UUID,
    edition: str,
    accessible_project_ids: Iterable[UUID],
    document_type_codes: Iterable[str] | None = None,
    clause_numbers: Iterable[str] | None = None,
) -> RetrievalScope:
    """Search a project's documents together with a knowledge base edition.

    The common scope for claim analysis: the project's own contract and
    correspondence, read alongside the standard form that governs it.
    """
    return RetrievalScope(
        organization_id=organization_id,
        sources=frozenset({SourceKind.PROJECT_DOCUMENT, SourceKind.KNOWLEDGE_BASE}),
        project_id=project_id,
        knowledge_base_edition=edition,
        accessible_project_ids=_frozen(accessible_project_ids),
        document_type_codes=_frozen(document_type_codes),
        clause_numbers=_frozen(clause_numbers),
    )
