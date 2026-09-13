"""Tests for retrieval orchestration.

The repository layer is mocked, so these need Django but no database. They
verify the sequencing decisions the service owns: which retrievers run, how
failures degrade, and that edition verification is enforced before anything
reaches a model.
"""
from __future__ import annotations

from typing import Sequence

import pytest

from claimiq.ai.domain.providers import EmbeddingResult, ModelSpec, RerankResult
from claimiq.core.domain.errors import EditionMixingError, ProviderUnavailableError
from claimiq.search.domain.fusion import RetrievalMethod, RetrievedChunk
from claimiq.search.domain.scope import (
    RetrievalScope,
    SourceKind,
    combined_scope,
    project_scope,
)
from claimiq.search.services import retrieval as retrieval_module
from claimiq.search.services.retrieval import RetrievalService


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeEmbeddingProvider:
    """Deterministic embeddings. Never reaches a model runtime."""

    provider_key = "fake"

    def __init__(self, *, fail: bool = False, dimensions: int = 8) -> None:
        self.fail = fail
        self._dimensions = dimensions
        self.calls: list[Sequence[str]] = []

    def is_available(self) -> bool:
        return not self.fail

    def list_models(self) -> Sequence[ModelSpec]:
        return ()

    def embed(self, model: str, texts: Sequence[str]) -> EmbeddingResult:
        if self.fail:
            raise ProviderUnavailableError("embedding runtime down")
        self.calls.append(list(texts))
        vectors = [[float((len(t) + i) % 7) for i in range(self._dimensions)] for t in texts]
        return EmbeddingResult(vectors=vectors, model=model, dimensions=self._dimensions)

    def dimensions(self, model: str) -> int:
        return self._dimensions


class FakeReranker:
    provider_key = "fake"

    def __init__(self, scores: Sequence[float] | None = None, *, fail: bool = False) -> None:
        self.scores = scores
        self.fail = fail

    def is_available(self) -> bool:
        return not self.fail

    def list_models(self) -> Sequence[ModelSpec]:
        return ()

    def rerank(self, model: str, query: str, documents: Sequence[str]) -> RerankResult:
        if self.fail:
            raise ProviderUnavailableError("reranker down")
        scores = list(self.scores) if self.scores is not None else [
            1.0 / (i + 1) for i in range(len(documents))
        ]
        return RerankResult(scores=scores[: len(documents)], model=model)


def chunk(
    chunk_id: str,
    *,
    method: RetrievalMethod = RetrievalMethod.LEXICAL,
    document_id: str = "doc-1",
    kb: bool = False,
    edition: str | None = None,
    clause: str | None = None,
    text: str = "The claiming Party shall give a Notice within 28 days.",
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        text=text,
        document_id=document_id,
        document_title="Contract.pdf" if not kb else "FIDIC Red Book 2017",
        page_number=121,
        score=0.5,
        method=method,
        clause_number=clause,
        edition=edition,
        is_knowledge_base=kb,
    )


@pytest.fixture
def patched_repos(monkeypatch):
    """Replace the repository layer with recorded stubs."""

    state = {
        "clause_exact": [],
        "lexical": [],
        "vector": [],
        "counts": {"project_chunks": 5, "knowledge_base_chunks": 5, "embedded_chunks": 5},
        "calls": [],
    }

    def fake_clause_exact(scope, clause_numbers, limit=20):
        state["calls"].append(("clause_exact", list(clause_numbers)))
        return state["clause_exact"]

    def fake_lexical(scope, query_text, limit=50):
        state["calls"].append(("lexical", query_text))
        return state["lexical"]

    def fake_vector(scope, embedding, limit=50, max_distance=0.75):
        state["calls"].append(("vector", len(embedding)))
        return state["vector"]

    def fake_counts(scope):
        return state["counts"]

    monkeypatch.setattr(retrieval_module.repositories, "clause_exact", fake_clause_exact)
    monkeypatch.setattr(retrieval_module.repositories, "lexical", fake_lexical)
    monkeypatch.setattr(retrieval_module.repositories, "vector", fake_vector)
    monkeypatch.setattr(retrieval_module.repositories, "count_available", fake_counts)
    return state


def kb_scope(organization_id, project_id, edition="red-book-2017") -> RetrievalScope:
    return combined_scope(
        organization_id=organization_id,
        project_id=project_id,
        edition=edition,
        accessible_project_ids={project_id},
    )


# ---------------------------------------------------------------------------
# Retriever selection
# ---------------------------------------------------------------------------


def test_clause_reference_triggers_exact_lookup(patched_repos, organization_id, project_id):
    patched_repos["clause_exact"] = [chunk("c1", method=RetrievalMethod.CLAUSE_EXACT, clause="20.2.1")]
    patched_repos["lexical"] = [chunk("l1")]

    service = RetrievalService()
    result = service.retrieve(
        "What does Sub-Clause 20.2.1 require?", kb_scope(organization_id, project_id)
    )

    methods = [name for name, _ in patched_repos["calls"]]
    assert "clause_exact" in methods
    assert result.trace.clause_references == ["20.2.1"]


def test_no_clause_reference_skips_exact_lookup(patched_repos, organization_id, project_id):
    patched_repos["lexical"] = [chunk("l1")]

    RetrievalService().retrieve(
        "Which documents support the claim?", kb_scope(organization_id, project_id)
    )

    assert "clause_exact" not in [name for name, _ in patched_repos["calls"]]


def test_lexical_always_runs(patched_repos, organization_id, project_id):
    patched_repos["lexical"] = [chunk("l1")]
    RetrievalService().retrieve("anything at all", kb_scope(organization_id, project_id))
    assert "lexical" in [name for name, _ in patched_repos["calls"]]


def test_evidence_question_excludes_the_knowledge_base(
    patched_repos, organization_id, project_id
):
    """A project-facts question should not spend budget on the standard form."""
    patched_repos["lexical"] = [chunk("l1")]

    result = RetrievalService().retrieve(
        "Which documents support the EOT claim?", kb_scope(organization_id, project_id)
    )

    assert any("Knowledge base excluded" in note for note in result.trace.notes)


def test_clause_question_keeps_the_knowledge_base(patched_repos, organization_id, project_id):
    patched_repos["lexical"] = [chunk("l1")]
    result = RetrievalService().retrieve(
        "What does Clause 20.2.1 require?", kb_scope(organization_id, project_id)
    )
    assert not any("Knowledge base excluded" in note for note in result.trace.notes)


# ---------------------------------------------------------------------------
# Vector search and graceful degradation
# ---------------------------------------------------------------------------


def test_vector_search_runs_when_a_provider_is_configured(
    patched_repos, organization_id, project_id
):
    patched_repos["lexical"] = [chunk("l1")]
    patched_repos["vector"] = [chunk("v1", method=RetrievalMethod.VECTOR)]
    provider = FakeEmbeddingProvider()

    service = RetrievalService(embedding_provider=provider, embedding_model="bge-m3")
    result = service.retrieve("delay to the works", kb_scope(organization_id, project_id))

    assert provider.calls == [["delay to the works"]]
    assert result.trace.vector_search_ran is True


def test_missing_embedding_provider_is_recorded_not_hidden(
    patched_repos, organization_id, project_id
):
    """A thin result set must be explainable."""
    patched_repos["lexical"] = [chunk("l1")]

    result = RetrievalService().retrieve("delay", kb_scope(organization_id, project_id))

    assert result.trace.vector_search_ran is False
    assert any("no embedding provider" in n.lower() for n in result.trace.notes)
    assert result.context.chunks, "lexical results still returned"


def test_embedding_failure_degrades_to_lexical_with_a_note(
    patched_repos, organization_id, project_id
):
    """Degrading is correct. Letting the failure text become context is not."""
    patched_repos["lexical"] = [chunk("l1")]
    service = RetrievalService(
        embedding_provider=FakeEmbeddingProvider(fail=True), embedding_model="bge-m3"
    )

    result = service.retrieve("delay", kb_scope(organization_id, project_id))

    assert result.context.chunks
    assert any("unavailable" in n.lower() for n in result.trace.notes)
    for assembled in result.context.chunks:
        assert "unavailable" not in assembled.chunk.text.lower()


# ---------------------------------------------------------------------------
# Reranking
# ---------------------------------------------------------------------------


def test_reranker_reorders_the_shortlist(patched_repos, organization_id, project_id):
    patched_repos["lexical"] = [
        chunk("a", document_id="d1"),
        chunk("b", document_id="d2"),
        chunk("c", document_id="d3"),
    ]
    service = RetrievalService(
        reranker_provider=FakeReranker([0.1, 0.9, 0.5]), reranker_model="bge-reranker"
    )

    result = service.retrieve("delay", kb_scope(organization_id, project_id))

    assert result.trace.reranked is True
    assert result.context.chunk_ids()[0] == "b"


def test_reranker_failure_retains_fusion_order(patched_repos, organization_id, project_id):
    patched_repos["lexical"] = [chunk("a", document_id="d1"), chunk("b", document_id="d2")]
    service = RetrievalService(
        reranker_provider=FakeReranker(fail=True), reranker_model="bge-reranker"
    )

    result = service.retrieve("delay", kb_scope(organization_id, project_id))

    assert result.trace.reranked is False
    assert any("Reranking unavailable" in n for n in result.trace.notes)
    assert result.context.chunks


# ---------------------------------------------------------------------------
# Edition safety — ADR 0004 enforced at the last possible moment
# ---------------------------------------------------------------------------


def test_foreign_edition_chunk_is_dropped_before_assembly(
    patched_repos, organization_id, project_id
):
    patched_repos["lexical"] = [
        chunk("ok", kb=True, edition="red-book-2017", document_id="kb1"),
        chunk("wrong", kb=True, edition="red-book-1987", document_id="kb2"),
    ]

    result = RetrievalService().retrieve(
        "What does Clause 20 require?", kb_scope(organization_id, project_id)
    )

    assert result.context.chunk_ids() == ["ok"]
    assert result.context.editions_present() == {"red-book-2017"}


def test_edition_verification_raises_if_assembly_lets_one_through(
    patched_repos, monkeypatch, organization_id, project_id
):
    """The last line of defence must fire, not be shadowed by the earlier one.

    Assembly already drops foreign-edition chunks. This bypasses that filter to
    simulate a future change that loses it, and asserts the post-retrieval
    verification still refuses to produce a response.
    """
    from claimiq.search.domain import fusion as fusion_domain

    patched_repos["lexical"] = [
        chunk("wrong", kb=True, edition="red-book-1987", document_id="kb2")
    ]

    def assemble_without_edition_filter(candidates, **kwargs):
        kwargs["expected_edition"] = None
        return fusion_domain.assemble_context(candidates, **kwargs)

    monkeypatch.setattr(
        retrieval_module, "assemble_context", assemble_without_edition_filter
    )

    with pytest.raises(EditionMixingError) as exc:
        RetrievalService().retrieve(
            "What does Clause 20 require?", kb_scope(organization_id, project_id)
        )
    assert exc.value.details["expected"] == "red-book-2017"


def test_edition_verification_passes_for_a_matching_edition(
    patched_repos, organization_id, project_id
):
    """Guard against the previous test passing for the wrong reason."""
    patched_repos["lexical"] = [
        chunk("right", kb=True, edition="red-book-2017", document_id="kb1")
    ]

    result = RetrievalService().retrieve(
        "What does Clause 20 require?", kb_scope(organization_id, project_id)
    )
    assert result.context.chunk_ids() == ["right"]


# ---------------------------------------------------------------------------
# Empty results
# ---------------------------------------------------------------------------


def test_empty_corpus_is_distinguished_from_no_match(
    patched_repos, organization_id, project_id
):
    """"No results" and "nothing indexed" are very different answers."""
    patched_repos["counts"] = {
        "project_chunks": 0,
        "knowledge_base_chunks": 0,
        "embedded_chunks": 0,
    }

    result = RetrievalService().retrieve("delay", kb_scope(organization_id, project_id))

    assert result.is_empty
    assert any("finish ingestion" in n for n in result.trace.notes)


def test_both_empty_corpora_are_reported(patched_repos, organization_id, project_id):
    """Regression: returning on the first gap told a clause question only about
    missing documents, hiding the missing knowledge base — the fact more
    relevant to what was actually asked."""
    patched_repos["counts"] = {
        "project_chunks": 0,
        "knowledge_base_chunks": 0,
        "embedded_chunks": 0,
    }

    result = RetrievalService().retrieve(
        "What does Clause 20 require?", kb_scope(organization_id, project_id)
    )
    note = " ".join(result.trace.notes)
    assert "finish ingestion" in note
    assert "red-book-2017" in note


def test_missing_knowledge_base_is_named(patched_repos, organization_id, project_id):
    patched_repos["counts"] = {
        "project_chunks": 10,
        "knowledge_base_chunks": 0,
        "embedded_chunks": 10,
    }

    result = RetrievalService().retrieve(
        "What does Clause 20 require?", kb_scope(organization_id, project_id)
    )

    assert result.is_empty
    assert any("red-book-2017" in n for n in result.trace.notes)


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------


def test_trace_records_how_the_result_was_produced(
    patched_repos, organization_id, project_id
):
    """Without this, "why did the model cite that clause" is unanswerable."""
    patched_repos["clause_exact"] = [chunk("c1", method=RetrievalMethod.CLAUSE_EXACT)]
    patched_repos["lexical"] = [chunk("l1", document_id="d2")]

    result = RetrievalService().retrieve(
        "What does Sub-Clause 20.2.1 require?", kb_scope(organization_id, project_id)
    )

    payload = result.trace.as_dict()
    assert payload["intent"] == "clause_lookup"
    assert payload["clause_references"] == ["20.2.1"]
    assert payload["counts"]["clause_exact"] == 1
    assert payload["counts"]["lexical"] == 1
    assert "fusion" in payload["timings_ms"]
    assert "red-book-2017" in payload["scope"]


def test_project_only_scope_never_touches_the_knowledge_base(
    patched_repos, organization_id, project_id
):
    patched_repos["lexical"] = [chunk("l1")]
    scope = project_scope(
        organization_id=organization_id,
        project_id=project_id,
        accessible_project_ids={project_id},
    )

    result = RetrievalService().retrieve("delay", scope)

    assert SourceKind.KNOWLEDGE_BASE not in scope.sources
    assert result.context.editions_present() == set()
