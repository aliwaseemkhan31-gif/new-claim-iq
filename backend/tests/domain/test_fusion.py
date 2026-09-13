"""Tests for hybrid retrieval fusion, diversification and context assembly."""
from __future__ import annotations

import pytest

from claimiq.search.domain.fusion import (
    AGREEMENT_BONUS,
    CLAUSE_EXACT_BONUS,
    RetrievalMethod,
    RetrievedChunk,
    apply_reranker_scores,
    assemble_context,
    diversify,
    reciprocal_rank_fusion,
)


def chunk(
    chunk_id: str,
    method: RetrievalMethod = RetrievalMethod.VECTOR,
    *,
    document_id: str = "doc-1",
    score: float = 0.5,
    clause: str | None = None,
    edition: str | None = None,
    kb: bool = False,
    complete: bool = False,
    text: str = "x" * 500,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        text=text,
        document_id=document_id,
        document_title=f"Document {document_id}",
        page_number=1,
        score=score,
        method=method,
        clause_number=clause,
        edition=edition,
        is_knowledge_base=kb,
        is_complete_clause=complete,
    )


# ---------------------------------------------------------------------------
# Reciprocal rank fusion
# ---------------------------------------------------------------------------


def test_fusion_combines_ranks_not_scores() -> None:
    """Incomparable score scales must not need normalising."""
    lexical = [chunk("a", RetrievalMethod.LEXICAL, score=0.41)]
    vector = [chunk("b", RetrievalMethod.VECTOR, score=0.82)]
    fused = reciprocal_rank_fusion([lexical, vector])
    assert {f.chunk_id for f in fused} == {"a", "b"}
    # Both were rank 1 in their own list, so neither dominates.
    assert fused[0].fused_score == pytest.approx(fused[1].fused_score)


def test_agreement_between_retrievers_is_rewarded() -> None:
    """Independent agreement is a real relevance signal."""
    lexical = [chunk("shared", RetrievalMethod.LEXICAL), chunk("lex-only", RetrievalMethod.LEXICAL)]
    vector = [chunk("shared", RetrievalMethod.VECTOR), chunk("vec-only", RetrievalMethod.VECTOR)]
    fused = reciprocal_rank_fusion([lexical, vector])
    assert fused[0].chunk_id == "shared"
    assert fused[0].found_by_multiple is True
    assert fused[0].methods == {RetrievalMethod.LEXICAL, RetrievalMethod.VECTOR}


def test_exact_clause_match_outranks_similarity() -> None:
    """Asking about Clause 20.2.1 must surface 20.2.1, not something like it."""
    exact = [chunk("exact", RetrievalMethod.CLAUSE_EXACT, clause="20.2.1")]
    vector = [chunk("similar", RetrievalMethod.VECTOR)]
    fused = reciprocal_rank_fusion([vector, exact])
    assert fused[0].chunk_id == "exact"


def test_rank_order_is_preserved_within_a_list() -> None:
    lexical = [chunk(f"c{i}", RetrievalMethod.LEXICAL) for i in range(5)]
    fused = reciprocal_rank_fusion([lexical])
    assert [f.chunk_id for f in fused] == ["c0", "c1", "c2", "c3", "c4"]


def test_weights_shift_the_balance() -> None:
    lexical = [chunk("lex", RetrievalMethod.LEXICAL)]
    vector = [chunk("vec", RetrievalMethod.VECTOR)]
    fused = reciprocal_rank_fusion([lexical, vector], weights=[3.0, 1.0])
    assert fused[0].chunk_id == "lex"


def test_mismatched_weights_are_rejected() -> None:
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([[chunk("a")]], weights=[1.0, 2.0])


def test_empty_input_yields_empty_output() -> None:
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []


def test_clause_metadata_is_preserved_across_merge() -> None:
    """The representation carrying clause context wins the merge."""
    vector = [chunk("shared", RetrievalMethod.VECTOR, clause=None)]
    exact = [chunk("shared", RetrievalMethod.CLAUSE_EXACT, clause="20.2.1")]
    fused = reciprocal_rank_fusion([vector, exact])
    assert fused[0].chunk.clause_number == "20.2.1"


def test_bonuses_are_multiplicative_and_ordered() -> None:
    assert CLAUSE_EXACT_BONUS > AGREEMENT_BONUS > 1.0


def test_fusion_is_deterministic_for_equal_scores() -> None:
    a = [chunk("z", RetrievalMethod.LEXICAL, document_id="d2")]
    b = [chunk("a", RetrievalMethod.VECTOR, document_id="d1")]
    first = [f.chunk_id for f in reciprocal_rank_fusion([a, b])]
    second = [f.chunk_id for f in reciprocal_rank_fusion([a, b])]
    assert first == second


# ---------------------------------------------------------------------------
# Diversification
# ---------------------------------------------------------------------------


def test_one_document_cannot_monopolise_results() -> None:
    """The failure this prevents: a verbose clause filling the whole context."""
    candidates = reciprocal_rank_fusion(
        [[chunk(f"c{i}", document_id="hog") for i in range(10)]]
    )
    kept = diversify(candidates, max_per_document=3)
    assert len(kept) == 3


def test_diversification_keeps_the_best_from_each_document() -> None:
    results = [chunk(f"a{i}", document_id="doc-a") for i in range(5)]
    results += [chunk(f"b{i}", document_id="doc-b") for i in range(5)]
    candidates = reciprocal_rank_fusion([results])
    kept = diversify(candidates, max_per_document=2)
    documents = [c.chunk.document_id for c in kept]
    assert documents.count("doc-a") == 2
    assert documents.count("doc-b") == 2


def test_diversification_refills_from_overflow_to_meet_the_limit() -> None:
    """Returning fewer chunks than requested wastes context budget."""
    candidates = reciprocal_rank_fusion(
        [[chunk(f"c{i}", document_id="only") for i in range(10)]]
    )
    kept = diversify(candidates, max_per_document=2, limit=5)
    assert len(kept) == 5


def test_invalid_cap_is_rejected() -> None:
    with pytest.raises(ValueError):
        diversify([], max_per_document=0)


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------


def test_assembly_respects_the_chunk_cap() -> None:
    candidates = reciprocal_rank_fusion(
        [[chunk(f"c{i}", document_id=f"doc-{i}") for i in range(30)]]
    )
    assembled = assemble_context(candidates, max_chunks=8)
    assert len(assembled.chunks) == 8
    assert assembled.dropped_for_budget == 22


def test_assembly_respects_the_character_budget() -> None:
    candidates = reciprocal_rank_fusion(
        [[chunk(f"c{i}", document_id=f"doc-{i}", text="y" * 1000) for i in range(20)]]
    )
    assembled = assemble_context(candidates, max_chunks=20, max_chars=5000)
    assert assembled.total_chars <= 5000
    assert len(assembled.chunks) == 5


def test_first_chunk_is_kept_even_if_it_exceeds_the_budget() -> None:
    """Returning nothing at all is worse than returning one oversized chunk."""
    candidates = reciprocal_rank_fusion([[chunk("big", text="z" * 50000)]])
    assembled = assemble_context(candidates, max_chars=1000)
    assert len(assembled.chunks) == 1


def test_foreign_edition_chunks_are_dropped_at_assembly() -> None:
    """Safety net behind scope filtering (ADR 0004)."""
    results = [
        chunk("ok", edition="red-book-2017", kb=True),
        chunk("wrong", edition="red-book-1987", kb=True, document_id="doc-2"),
    ]
    candidates = reciprocal_rank_fusion([results])
    assembled = assemble_context(candidates, expected_edition="red-book-2017")
    assert assembled.chunk_ids() == ["ok"]
    assert assembled.editions_present() == {"red-book-2017"}


def test_project_documents_are_not_edition_filtered() -> None:
    candidates = reciprocal_rank_fusion([[chunk("project-doc", kb=False, edition=None)]])
    assembled = assemble_context(candidates, expected_edition="red-book-2017")
    assert assembled.chunk_ids() == ["project-doc"]


def test_assembly_reports_what_it_dropped_and_why() -> None:
    results = [chunk(f"c{i}", document_id="one") for i in range(10)]
    candidates = reciprocal_rank_fusion([results])
    assembled = assemble_context(candidates, max_chunks=2, max_per_document=3)
    assert assembled.candidates_considered == 10
    assert assembled.dropped_for_diversity == 7
    assert assembled.dropped_for_budget == 1


def test_document_ids_are_deduplicated_in_order() -> None:
    results = [
        chunk("a", document_id="doc-1"),
        chunk("b", document_id="doc-2"),
        chunk("c", document_id="doc-1"),
    ]
    assembled = assemble_context(reciprocal_rank_fusion([results]))
    assert assembled.document_ids() == ["doc-1", "doc-2"]


def test_empty_candidates_assemble_to_empty_context() -> None:
    assembled = assemble_context([])
    assert assembled.chunks == []
    assert assembled.total_chars == 0


# ---------------------------------------------------------------------------
# Reranking
# ---------------------------------------------------------------------------


def test_reranker_replaces_the_fusion_ordering() -> None:
    candidates = reciprocal_rank_fusion(
        [[chunk("first"), chunk("second", document_id="d2"), chunk("third", document_id="d3")]]
    )
    reranked = apply_reranker_scores(candidates, [0.1, 0.9, 0.5])
    assert [c.chunk_id for c in reranked] == ["second", "third", "first"]


def test_reranker_score_count_must_match() -> None:
    """Zipping to the shorter sequence would drop candidates invisibly."""
    candidates = reciprocal_rank_fusion([[chunk("a"), chunk("b", document_id="d2")]])
    with pytest.raises(ValueError):
        apply_reranker_scores(candidates, [0.5])


def test_reranking_preserves_chunk_payloads() -> None:
    candidates = reciprocal_rank_fusion([[chunk("a", clause="20.2.1")]])
    reranked = apply_reranker_scores(candidates, [0.99])
    assert reranked[0].chunk.clause_number == "20.2.1"
    assert reranked[0].fused_score == 0.99
