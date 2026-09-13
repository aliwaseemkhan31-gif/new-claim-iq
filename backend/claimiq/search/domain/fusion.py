"""Hybrid retrieval fusion, diversification and context assembly.

The prototype ran a similarity search and passed the top chunks to the model
(`../backend/rag.py:310`). Three problems that this module addresses:

1. **Dense-only retrieval misses exact terms.** Embeddings are poor at defined
   terms, party names, document references and clause numbers — precisely the
   tokens that matter in a contract. Lexical retrieval catches those; dense
   retrieval catches paraphrase. Neither alone is sufficient.

2. **Scores from different retrievers are not comparable.** A cosine similarity
   of 0.82 and a `ts_rank_cd` of 0.41 cannot be averaged into anything
   meaningful. Reciprocal Rank Fusion combines *ranks* rather than scores,
   which sidesteps the normalisation problem entirely.

3. **Top-k is often one document repeated.** A single verbose clause can occupy
   every slot, crowding out the correspondence that contradicts it. The
   assembler enforces per-document diversity and a token budget.

Pure stdlib; runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Iterable, Sequence


class RetrievalMethod(str, Enum):
    LEXICAL = "lexical"
    VECTOR = "vector"
    CLAUSE_EXACT = "clause_exact"
    """Direct relational lookup by clause number. Not a similarity search."""


@dataclass(frozen=True)
class RetrievedChunk:
    """A candidate returned by one retriever."""

    chunk_id: str
    text: str
    document_id: str
    document_title: str
    page_number: int
    score: float
    method: RetrievalMethod
    clause_number: str | None = None
    edition: str | None = None
    is_knowledge_base: bool = False
    is_complete_clause: bool = False
    char_count: int = 0

    def __post_init__(self) -> None:
        if self.char_count == 0 and self.text:
            object.__setattr__(self, "char_count", len(self.text))


@dataclass
class FusedChunk:
    """A candidate after fusion, carrying how it was found."""

    chunk: RetrievedChunk
    fused_score: float
    ranks: dict[RetrievalMethod, int] = field(default_factory=dict)
    methods: frozenset[RetrievalMethod] = frozenset()

    @property
    def chunk_id(self) -> str:
        return self.chunk.chunk_id

    @property
    def found_by_multiple(self) -> bool:
        """True when more than one retriever surfaced this chunk.

        Independent agreement between lexical and dense retrieval is a genuine
        relevance signal, and is rewarded in scoring.
        """
        return len(self.methods) > 1


#: RRF damping constant. 60 is the value from the original Cormack et al.
#: formulation and is a reasonable default: large enough that the top few ranks
#: are not overwhelmingly dominant, small enough that rank still matters.
DEFAULT_RRF_K = 60

#: Multiplier applied when several retrievers agree on a chunk.
AGREEMENT_BONUS = 1.15

#: Multiplier for an exact clause-number match. Deliberately large: when a user
#: asks about Clause 20.2.1, the text of 20.2.1 outranks anything merely similar
#: to it. This is the structural replacement for the prototype's trick of
#: repeating the clause number in the query string to bias the embedding.
CLAUSE_EXACT_BONUS = 2.0

#: Multiplier for a chunk holding a whole clause rather than a fragment. A
#: partial obligation is a misleading citation.
COMPLETE_CLAUSE_BONUS = 1.10


def reciprocal_rank_fusion(
    result_sets: Sequence[Sequence[RetrievedChunk]],
    *,
    k: int = DEFAULT_RRF_K,
    weights: Sequence[float] | None = None,
) -> list[FusedChunk]:
    """Fuse ranked result sets by Reciprocal Rank Fusion.

    Each chunk scores ``weight / (k + rank)`` from each list it appears in, and
    the contributions are summed. Because only ranks are used, retrievers with
    incomparable score scales combine without normalisation.

    Args:
        result_sets: Ranked results, best first, one sequence per retriever.
        k: Damping constant.
        weights: Per-retriever weights. Defaults to equal weighting.

    Returns:
        Fused candidates, best first.
    """
    if weights is None:
        weights = [1.0] * len(result_sets)
    if len(weights) != len(result_sets):
        raise ValueError("weights must have the same length as result_sets")

    accumulated: dict[str, FusedChunk] = {}

    for result_set, weight in zip(result_sets, weights):
        for rank, chunk in enumerate(result_set, start=1):
            contribution = weight / (k + rank)
            existing = accumulated.get(chunk.chunk_id)
            if existing is None:
                accumulated[chunk.chunk_id] = FusedChunk(
                    chunk=chunk,
                    fused_score=contribution,
                    ranks={chunk.method: rank},
                    methods=frozenset({chunk.method}),
                )
            else:
                existing.fused_score += contribution
                existing.ranks[chunk.method] = rank
                existing.methods = existing.methods | {chunk.method}
                # Prefer the representation that carries clause metadata, so a
                # chunk found by exact clause lookup keeps that context.
                if chunk.clause_number and not existing.chunk.clause_number:
                    existing.chunk = chunk

    for fused in accumulated.values():
        if fused.found_by_multiple:
            fused.fused_score *= AGREEMENT_BONUS
        if RetrievalMethod.CLAUSE_EXACT in fused.methods:
            fused.fused_score *= CLAUSE_EXACT_BONUS
        if fused.chunk.is_complete_clause:
            fused.fused_score *= COMPLETE_CLAUSE_BONUS

    return sorted(
        accumulated.values(),
        key=lambda f: (-f.fused_score, f.chunk.document_id, f.chunk.chunk_id),
    )


def diversify(
    candidates: Sequence[FusedChunk],
    *,
    max_per_document: int = 4,
    limit: int | None = None,
) -> list[FusedChunk]:
    """Cap how many chunks any single document contributes.

    Order is otherwise preserved. Without this, one verbose clause can fill the
    entire context window and the correspondence that contradicts it never
    reaches the model — which is exactly the question the product exists to
    answer.
    """
    if max_per_document < 1:
        raise ValueError("max_per_document must be at least 1")

    counts: dict[str, int] = {}
    kept: list[FusedChunk] = []
    overflow: list[FusedChunk] = []

    for candidate in candidates:
        document_id = candidate.chunk.document_id
        if counts.get(document_id, 0) < max_per_document:
            counts[document_id] = counts.get(document_id, 0) + 1
            kept.append(candidate)
        else:
            overflow.append(candidate)

    # If the cap left room, refill from overflow in score order rather than
    # returning fewer chunks than asked for.
    if limit is not None and len(kept) < limit:
        kept.extend(overflow[: limit - len(kept)])
        kept.sort(key=lambda f: -f.fused_score)

    return kept[:limit] if limit is not None else kept


@dataclass
class AssembledContext:
    """The final context handed to a model, with the trace of how it was built."""

    chunks: list[FusedChunk]
    total_chars: int
    dropped_for_budget: int
    dropped_for_diversity: int
    candidates_considered: int

    def chunk_ids(self) -> list[str]:
        return [c.chunk_id for c in self.chunks]

    def document_ids(self) -> list[str]:
        seen: list[str] = []
        for candidate in self.chunks:
            if candidate.chunk.document_id not in seen:
                seen.append(candidate.chunk.document_id)
        return seen

    def editions_present(self) -> set[str]:
        return {
            c.chunk.edition
            for c in self.chunks
            if c.chunk.is_knowledge_base and c.chunk.edition
        }


def assemble_context(
    candidates: Sequence[FusedChunk],
    *,
    max_chunks: int = 12,
    max_chars: int = 24000,
    max_per_document: int = 4,
    expected_edition: str | None = None,
) -> AssembledContext:
    """Select the final chunk set under diversity and budget constraints.

    Args:
        candidates: Fused candidates, best first.
        max_chunks: Hard cap on chunk count.
        max_chars: Character budget for the assembled context.
        max_per_document: Per-document diversity cap.
        expected_edition: When set, knowledge-base chunks from another edition
            are dropped rather than assembled.

    Returns:
        The selected context with counts of what was dropped and why, so the
        selection is inspectable in AI observability rather than opaque.

    Note:
        Dropping a foreign-edition chunk here is a safety net, not the primary
        control. Scope filtering (ADR 0004) should mean none ever arrive; if one
        does, :class:`claimiq.core.domain.errors.EditionMixingError` is raised
        by the citation validator when it is cited.
    """
    considered = len(candidates)

    if expected_edition is not None:
        candidates = [
            c
            for c in candidates
            if not (
                c.chunk.is_knowledge_base
                and c.chunk.edition
                and c.chunk.edition != expected_edition
            )
        ]

    before_diversity = len(candidates)
    diversified = diversify(candidates, max_per_document=max_per_document)
    dropped_for_diversity = before_diversity - len(diversified)

    selected: list[FusedChunk] = []
    total = 0
    dropped_for_budget = 0

    for candidate in diversified:
        if len(selected) >= max_chunks:
            dropped_for_budget += 1
            continue
        size = candidate.chunk.char_count
        if total + size > max_chars and selected:
            dropped_for_budget += 1
            continue
        selected.append(candidate)
        total += size

    return AssembledContext(
        chunks=selected,
        total_chars=total,
        dropped_for_budget=dropped_for_budget,
        dropped_for_diversity=dropped_for_diversity,
        candidates_considered=considered,
    )


def apply_reranker_scores(
    candidates: Sequence[FusedChunk],
    scores: Iterable[float],
) -> list[FusedChunk]:
    """Reorder candidates by cross-encoder relevance scores.

    A reranker sees the query and the passage together, so it judges relevance
    far better than either retriever — but it is too slow to run over the whole
    corpus. It runs over the fused shortlist and *replaces* the fusion ordering,
    since a reranker score is a direct relevance estimate rather than a rank
    heuristic.

    Raises:
        ValueError: if the score count does not match the candidate count.
            Silently zipping to the shorter sequence would drop candidates
            without any indication.
    """
    score_list = list(scores)
    if len(score_list) != len(candidates):
        raise ValueError(
            f"Expected {len(candidates)} reranker scores, got {len(score_list)}"
        )

    rescored = [
        replace(candidate, fused_score=score)
        for candidate, score in zip(candidates, score_list)
    ]
    return sorted(rescored, key=lambda f: -f.fused_score)
