"""Retrieval queries.

Three retrievers over two corpora, all built from a shared base queryset that
applies the scope's filters — including access control.

That sharing is the point. In the prototype, scoping lived above the store and
was reimplemented per call site, so the clause-lookup endpoint simply forgot
the edition filter. Here every query starts from ``_project_base`` or
``_knowledge_base_base``, both of which apply the scope unconditionally. A
retriever cannot opt out of the filter without not using the base at all.

The SQL in this module has not been executed against a database — the build
host has PostgreSQL 17 but no pgvector extension. See `OPERATIONS.md`.
"""
from __future__ import annotations

from typing import Sequence

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import F, Q, QuerySet
from pgvector.django import CosineDistance

from claimiq.core.domain.errors import EditionMixingError, ScopeValidationError
from claimiq.documents.models import DocumentChunk, ProcessingStatus
from claimiq.knowledge.models import KnowledgeBaseChunk, KnowledgeBaseStatus
from claimiq.search.domain.fusion import RetrievalMethod, RetrievedChunk
from claimiq.search.domain.scope import RetrievalScope

#: PostgreSQL text search configuration created by the Postgres init script.
#: Applies `unaccent` before stemming, so "Böhler" matches "Bohler".
SEARCH_CONFIG = "claimiq_english"

#: Cosine distance above which a vector match is discarded outright. Distance,
#: not similarity: 0 is identical, 2 is opposite. Beyond this the match is
#: noise, and passing noise to a model as "context" invites it to use it.
MAX_COSINE_DISTANCE = 0.75


# ---------------------------------------------------------------------------
# Base querysets — every retriever starts here
# ---------------------------------------------------------------------------


def _project_base(scope: RetrievalScope) -> QuerySet[DocumentChunk]:
    """Project-document chunks this scope may read.

    Applies, unconditionally: organization, accessible projects, soft deletion,
    processing completion, superseded versions, and any document or type
    narrowing on the scope.
    """
    if not scope.reads_project_documents:
        return DocumentChunk.objects.none()

    queryset = DocumentChunk.objects.filter(
        version__document__project__organization_id=scope.organization_id,
        version__document__project_id__in=scope.project_filter(),
        # Soft-deleted documents and projects must not surface in retrieval.
        version__document__deleted_at__isnull=True,
        version__document__project__deleted_at__isnull=True,
        version__processing_status=ProcessingStatus.COMPLETED,
    )

    if not scope.include_superseded:
        queryset = queryset.filter(version__is_superseded=False)

    if scope.document_ids:
        queryset = queryset.filter(version__document_id__in=scope.document_ids)

    if scope.document_type_codes:
        queryset = queryset.filter(
            version__document__document_type__in=scope.document_type_codes
        )

    return queryset.select_related("version__document")


def _knowledge_base_base(scope: RetrievalScope) -> QuerySet[KnowledgeBaseChunk]:
    """Knowledge-base chunks this scope may read.

    The edition filter is applied here and nowhere else, so it cannot be
    omitted by a retriever. ``RetrievalScope`` has already guaranteed an
    edition is present; this asserts it again rather than trusting the caller,
    because the cost of being wrong is a confidently wrong contractual answer.
    """
    if not scope.reads_knowledge_base:
        return KnowledgeBaseChunk.objects.none()

    edition = scope.knowledge_base_edition
    if not edition:
        raise ScopeValidationError(
            "Knowledge-base retrieval reached the repository without an edition.",
            details={"scope": scope.describe()},
        )

    return KnowledgeBaseChunk.objects.filter(
        knowledge_base__organization_id=scope.organization_id,
        knowledge_base__status=KnowledgeBaseStatus.PUBLISHED,
        knowledge_base__deleted_at__isnull=True,
        edition_code=edition,
        # Contents pages and guidance notes never reach a model.
        is_quarantined=False,
    ).select_related("knowledge_base")


# ---------------------------------------------------------------------------
# Result mapping
# ---------------------------------------------------------------------------


def _from_document_chunk(
    chunk: DocumentChunk, score: float, method: RetrievalMethod
) -> RetrievedChunk:
    document = chunk.version.document
    return RetrievedChunk(
        chunk_id=str(chunk.id),
        text=chunk.text,
        document_id=str(document.id),
        document_title=document.title,
        page_number=chunk.start_page,
        score=score,
        method=method,
        clause_number=chunk.clause_number or None,
        edition=None,
        is_knowledge_base=False,
        is_complete_clause=chunk.is_complete_clause,
        char_count=chunk.char_count or len(chunk.text),
    )


def _from_kb_chunk(
    chunk: KnowledgeBaseChunk, score: float, method: RetrievalMethod
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=str(chunk.id),
        text=chunk.text,
        document_id=str(chunk.knowledge_base_id),
        document_title=chunk.knowledge_base.name,
        page_number=chunk.page_number,
        score=score,
        method=method,
        clause_number=chunk.clause_number or None,
        edition=chunk.edition_code,
        is_knowledge_base=True,
        is_complete_clause=chunk.is_complete_clause,
        char_count=chunk.char_count or len(chunk.text),
    )


# ---------------------------------------------------------------------------
# Retriever 1 — exact clause lookup
# ---------------------------------------------------------------------------


def clause_exact(
    scope: RetrievalScope, clause_numbers: Sequence[str], limit: int = 20
) -> list[RetrievedChunk]:
    """Fetch chunks belonging to specific clauses.

    A relational query on an indexed column, not a similarity search. When the
    user asks about Clause 20.2.1, the text of 20.2.1 is returned because it
    *is* 20.2.1. The prototype approximated this by repeating the number in the
    embedding query string, which is unreliable and unexplainable.

    Descendants are included: asking about Clause 20 should return 20.1 and
    20.2.1, since the sub-clauses are where the obligations live.
    """
    if not clause_numbers:
        return []

    clause_filter = Q()
    for number in clause_numbers:
        clause_filter |= Q(clause_number=number) | Q(clause_number__startswith=f"{number}.")

    results: list[RetrievedChunk] = []

    if scope.reads_project_documents:
        for chunk in (
            _project_base(scope)
            .filter(clause_filter)
            .order_by("clause_number", "sequence")[:limit]
        ):
            results.append(_from_document_chunk(chunk, 1.0, RetrievalMethod.CLAUSE_EXACT))

    if scope.reads_knowledge_base:
        for chunk in (
            _knowledge_base_base(scope)
            .filter(clause_filter)
            .order_by("clause_number", "sequence")[:limit]
        ):
            results.append(_from_kb_chunk(chunk, 1.0, RetrievalMethod.CLAUSE_EXACT))

    return results


# ---------------------------------------------------------------------------
# Retriever 2 — lexical
# ---------------------------------------------------------------------------


def build_search_query(query_text: str) -> SearchQuery | None:
    """Build an OR query over the terms in ``query_text``.

    **OR, not AND.** An earlier version used ``search_type="websearch"``, which
    requires every term to be present. Verified against a real corpus, that
    meant a realistic question retrieved nothing: "What notice period does
    Sub-Clause 20.2.1 require?" reduces to the terms
    ``notice period sub-clause require``, and the clause that answers it
    contains "notice" but not "period" or "require", so the AND matched zero
    rows while a direct query with better-chosen words matched fine. Lexical
    retrieval was silently dead for most real questions.

    OR gives recall; ``ts_rank_cd`` supplies the precision by ranking documents
    that match more terms, more closely together, above those that match one.
    That division of labour is the standard information-retrieval arrangement
    and is why the ranking function exists.
    """
    terms = [t for t in query_text.replace("-", " ").split() if len(t) > 1]
    if not terms:
        return None

    query: SearchQuery | None = None
    for term in terms[:24]:  # bounded: a pathological query should not build a huge tree
        clause = SearchQuery(term, config=SEARCH_CONFIG, search_type="plain")
        query = clause if query is None else (query | clause)
    return query


def lexical(scope: RetrievalScope, query_text: str, limit: int = 50) -> list[RetrievedChunk]:
    """Full-text search over the precomputed tsvector columns.

    ``ts_rank_cd`` is cover-density ranking: it rewards query terms appearing
    close together, which suits contract prose where the meaningful match is a
    phrase rather than scattered keywords.

    This is not BM25 — PostgreSQL lacks its length normalisation. For chunks of
    bounded, similar size the difference is small, and staying in one engine
    keeps the permission filter in the same query (ADR 0002).
    """
    if not query_text.strip():
        return []

    search_query = build_search_query(query_text)
    if search_query is None:
        return []
    rank = SearchRank(F("search_vector"), search_query)
    results: list[RetrievedChunk] = []

    if scope.reads_project_documents:
        rows = (
            _project_base(scope)
            .filter(search_vector=search_query)
            .annotate(rank=rank)
            .order_by("-rank")[:limit]
        )
        results.extend(
            _from_document_chunk(chunk, float(chunk.rank), RetrievalMethod.LEXICAL)
            for chunk in rows
        )

    if scope.reads_knowledge_base:
        rows = (
            _knowledge_base_base(scope)
            .filter(search_vector=search_query)
            .annotate(rank=rank)
            .order_by("-rank")[:limit]
        )
        results.extend(
            _from_kb_chunk(chunk, float(chunk.rank), RetrievalMethod.LEXICAL)
            for chunk in rows
        )

    return results


# ---------------------------------------------------------------------------
# Retriever 3 — dense vector
# ---------------------------------------------------------------------------


def vector(
    scope: RetrievalScope,
    query_embedding: Sequence[float],
    limit: int = 50,
    max_distance: float = MAX_COSINE_DISTANCE,
) -> list[RetrievedChunk]:
    """Approximate nearest-neighbour search over pgvector HNSW indexes.

    Distance is converted to a similarity score so that higher is better,
    matching the other retrievers' convention. Matches beyond ``max_distance``
    are discarded rather than returned with a low score: a weak match passed to
    a model as context is an invitation to use it, and fusion would still rank
    it above nothing.

    Chunks with no embedding are excluded. A deployment without an embedding
    provider therefore returns nothing here and relies on lexical retrieval,
    which is the documented behaviour rather than a silent degradation.
    """
    if not query_embedding:
        return []

    distance = CosineDistance("embedding", list(query_embedding))
    results: list[RetrievedChunk] = []

    if scope.reads_project_documents:
        rows = (
            _project_base(scope)
            .filter(embedding__isnull=False)
            .annotate(distance=distance)
            .filter(distance__lte=max_distance)
            .order_by("distance")[:limit]
        )
        results.extend(
            _from_document_chunk(
                chunk, 1.0 - float(chunk.distance), RetrievalMethod.VECTOR
            )
            for chunk in rows
        )

    if scope.reads_knowledge_base:
        rows = (
            _knowledge_base_base(scope)
            .filter(embedding__isnull=False)
            .annotate(distance=distance)
            .filter(distance__lte=max_distance)
            .order_by("distance")[:limit]
        )
        results.extend(
            _from_kb_chunk(chunk, 1.0 - float(chunk.distance), RetrievalMethod.VECTOR)
            for chunk in rows
        )

    return results


# ---------------------------------------------------------------------------
# Post-retrieval verification
# ---------------------------------------------------------------------------


def assert_edition_consistency(
    results: Sequence[RetrievedChunk], scope: RetrievalScope
) -> None:
    """Verify no knowledge-base result escaped the edition filter.

    Defence in depth (ADR 0004). Unreachable while the base querysets are used
    correctly; if it fires, a filter was dropped by a code change and the
    response must not be produced. Raising is mandatory — degrading to a
    warning would reintroduce exactly the failure this prevents.
    """
    if not scope.reads_knowledge_base:
        offenders = [r.chunk_id for r in results if r.is_knowledge_base]
        if offenders:
            raise EditionMixingError(
                "Knowledge-base chunks were returned for a scope that does not "
                "read the knowledge base.",
                details={"chunk_ids": offenders[:10]},
            )
        return

    expected = scope.knowledge_base_edition
    mismatched = [
        {"chunk_id": r.chunk_id, "edition": r.edition}
        for r in results
        if r.is_knowledge_base and r.edition != expected
    ]
    if mismatched:
        raise EditionMixingError(
            "Retrieval returned knowledge-base chunks from outside the scoped edition.",
            details={"expected": expected, "mismatched": mismatched[:10]},
        )


def count_available(scope: RetrievalScope) -> dict[str, int]:
    """Corpus sizes for the current scope.

    Used to distinguish "no results" from "nothing indexed", which are very
    different answers to give a user.
    """
    counts = {"project_chunks": 0, "knowledge_base_chunks": 0, "embedded_chunks": 0}
    if scope.reads_project_documents:
        base = _project_base(scope)
        counts["project_chunks"] = base.count()
        counts["embedded_chunks"] = base.filter(embedding__isnull=False).count()
    if scope.reads_knowledge_base:
        counts["knowledge_base_chunks"] = _knowledge_base_base(scope).count()
    return counts
