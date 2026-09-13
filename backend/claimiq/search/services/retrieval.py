"""Hybrid retrieval orchestration.

Ties the pieces together:

    query analysis -> three retrievers -> fusion -> rerank -> assembly

Each piece is separately tested; this module owns the sequencing and the
decisions about which retrievers to run.

What it deliberately does not do is degrade quietly. If the knowledge base is
unreachable, or an embedding cannot be produced, that is recorded in the trace
and — where it changes the answer — raised. The prototype returned
``f"FIDIC KB unavailable: {e}"`` as retrieval context, so an infrastructure
failure could be read by the model as contract text.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from django.conf import settings

from claimiq.ai.domain.providers import EmbeddingProvider, RerankerProvider
from claimiq.core.domain.errors import ProviderUnavailableError, RetrievalError
from claimiq.core.logging import get_logger
from claimiq.search import repositories
from claimiq.search.domain import query as query_domain
from claimiq.search.domain.fusion import (
    AssembledContext,
    FusedChunk,
    RetrievedChunk,
    apply_reranker_scores,
    assemble_context,
    reciprocal_rank_fusion,
)
from claimiq.search.domain.scope import RetrievalScope

logger = get_logger("search.retrieval")


@dataclass
class RetrievalTrace:
    """A record of how a result set was produced.

    Stored with the AI call for observability. Without it, "why did the model
    cite that clause" is unanswerable — and on a system making contractual
    assertions, unanswerable is not acceptable.
    """

    query: str
    scope: str
    intent: str
    clause_references: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    timings_ms: dict[str, float] = field(default_factory=dict)
    reranked: bool = False
    vector_search_ran: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "scope": self.scope,
            "intent": self.intent,
            "clause_references": self.clause_references,
            "counts": self.counts,
            "timings_ms": self.timings_ms,
            "reranked": self.reranked,
            "vector_search_ran": self.vector_search_ran,
            "notes": self.notes,
        }


@dataclass
class RetrievalResult:
    context: AssembledContext
    analysis: query_domain.QueryAnalysis
    trace: RetrievalTrace

    @property
    def is_empty(self) -> bool:
        return not self.context.chunks


class RetrievalService:
    """Runs the hybrid retrieval pipeline.

    Providers are injected rather than constructed, so the service is testable
    with fakes and so a future out-of-process embedding service is a
    constructor argument rather than a rewrite (ADR 0003).
    """

    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        embedding_model: str = "",
        reranker_provider: RerankerProvider | None = None,
        reranker_model: str = "",
        config: dict[str, Any] | None = None,
    ) -> None:
        self.embedding_provider = embedding_provider
        self.embedding_model = embedding_model
        self.reranker_provider = reranker_provider
        self.reranker_model = reranker_model
        self.config = config or dict(settings.RETRIEVAL_SETTINGS)

    # ------------------------------------------------------------------

    def retrieve(self, question: str, scope: RetrievalScope) -> RetrievalResult:
        """Run the full pipeline for ``question`` within ``scope``."""
        analysis = query_domain.analyse(question)
        trace = RetrievalTrace(
            query=question,
            scope=scope.describe(),
            intent=analysis.intent.value,
            clause_references=list(analysis.clause_references),
        )

        effective_scope = self._narrow_scope(scope, analysis, trace)

        result_sets: list[Sequence[RetrievedChunk]] = []

        if analysis.should_use_clause_lookup:
            started = time.perf_counter()
            exact = repositories.clause_exact(
                effective_scope, analysis.clause_references, limit=20
            )
            trace.timings_ms["clause_exact"] = _ms(started)
            trace.counts["clause_exact"] = len(exact)
            if exact:
                result_sets.append(exact)

        started = time.perf_counter()
        lexical = repositories.lexical(
            effective_scope,
            analysis.lexical_query or question,
            limit=int(self.config.get("LEXICAL_CANDIDATES", 50)),
        )
        trace.timings_ms["lexical"] = _ms(started)
        trace.counts["lexical"] = len(lexical)
        if lexical:
            result_sets.append(lexical)

        dense = self._vector_search(question, effective_scope, trace)
        if dense:
            result_sets.append(dense)

        if not result_sets:
            trace.notes.append(self._explain_empty(effective_scope))
            return RetrievalResult(
                context=assemble_context([]), analysis=analysis, trace=trace
            )

        started = time.perf_counter()
        fused = reciprocal_rank_fusion(
            result_sets, k=int(self.config.get("RRF_K", 60))
        )
        trace.timings_ms["fusion"] = _ms(started)
        trace.counts["fused"] = len(fused)

        fused = self._rerank(question, fused, trace)

        started = time.perf_counter()
        context = assemble_context(
            fused,
            max_chunks=int(self.config.get("FINAL_CONTEXT_CHUNKS", 12)),
            max_per_document=int(self.config.get("MAX_CHUNKS_PER_DOCUMENT", 4)),
            expected_edition=effective_scope.knowledge_base_edition,
        )
        trace.timings_ms["assembly"] = _ms(started)
        trace.counts["assembled"] = len(context.chunks)
        trace.counts["dropped_for_budget"] = context.dropped_for_budget
        trace.counts["dropped_for_diversity"] = context.dropped_for_diversity

        # Defence in depth: verify nothing escaped the edition filter before
        # any of this reaches a model.
        repositories.assert_edition_consistency(
            [c.chunk for c in context.chunks], effective_scope
        )

        logger.info(
            "search.retrieved",
            extra={
                "intent": analysis.intent.value,
                "assembled": len(context.chunks),
                "total_ms": round(sum(trace.timings_ms.values()), 1),
            },
        )

        return RetrievalResult(context=context, analysis=analysis, trace=trace)

    # ------------------------------------------------------------------

    def _narrow_scope(
        self,
        scope: RetrievalScope,
        analysis: query_domain.QueryAnalysis,
        trace: RetrievalTrace,
    ) -> RetrievalScope:
        """Apply intent-derived narrowing.

        Only narrowing is applied, never widening — widening after a permission
        check is how a filter gets lost. Document-type hints are deliberately
        *not* applied as a hard filter: narrowing to them and finding nothing
        is worse than searching wider, so they inform ranking, not exclusion.
        """
        if scope.reads_knowledge_base and not analysis.should_search_knowledge_base:
            # An evidence or chronology question is about what happened on this
            # project; the standard form has nothing to say about it, and
            # including it spends context budget on irrelevant provisions.
            if scope.reads_project_documents:
                from claimiq.search.domain.scope import SourceKind

                narrowed = RetrievalScope(
                    organization_id=scope.organization_id,
                    sources=frozenset({SourceKind.PROJECT_DOCUMENT}),
                    accessible_project_ids=scope.accessible_project_ids,
                    project_id=scope.project_id,
                    document_ids=scope.document_ids,
                    document_type_codes=scope.document_type_codes,
                    clause_numbers=scope.clause_numbers,
                    include_superseded=scope.include_superseded,
                )
                trace.notes.append(
                    f"Knowledge base excluded: a {analysis.intent.value} question "
                    f"concerns project facts, not the standard form."
                )
                return narrowed
        return scope

    def _vector_search(
        self, question: str, scope: RetrievalScope, trace: RetrievalTrace
    ) -> list[RetrievedChunk]:
        """Embed the question and run ANN search.

        A missing embedding provider is a *recorded* condition, not a silent
        one: lexical retrieval still runs, and the trace says vector search did
        not, so a thin result set is explainable.
        """
        if self.embedding_provider is None or not self.embedding_model:
            trace.notes.append(
                "Vector search skipped: no embedding provider is configured. "
                "Results are lexical and clause-exact only."
            )
            return []

        started = time.perf_counter()
        try:
            embedded = self.embedding_provider.embed(self.embedding_model, [question])
        except ProviderUnavailableError as exc:
            # Degrading to lexical-only is correct — a partial answer with a
            # recorded caveat beats no answer. What is not acceptable is
            # letting the failure text become context.
            trace.notes.append(
                f"Vector search unavailable ({exc.code}); results are lexical only."
            )
            logger.warning("search.embedding_unavailable", extra={"error_code": exc.code})
            return []

        if not embedded.vectors:
            trace.notes.append("Vector search skipped: the query produced no embedding.")
            return []

        results = repositories.vector(
            scope,
            embedded.vectors[0],
            limit=int(self.config.get("VECTOR_CANDIDATES", 50)),
        )
        trace.timings_ms["vector"] = _ms(started)
        trace.counts["vector"] = len(results)
        trace.vector_search_ran = True
        return results

    def _rerank(
        self, question: str, fused: list[FusedChunk], trace: RetrievalTrace
    ) -> list[FusedChunk]:
        """Reorder the shortlist with a cross-encoder, if one is available."""
        if self.reranker_provider is None or not self.reranker_model or not fused:
            return fused

        shortlist = fused[: int(self.config.get("RERANK_CANDIDATES", 30))]
        remainder = fused[len(shortlist) :]

        started = time.perf_counter()
        try:
            scored = self.reranker_provider.rerank(
                self.reranker_model, question, [c.chunk.text for c in shortlist]
            )
        except ProviderUnavailableError as exc:
            trace.notes.append(
                f"Reranking unavailable ({exc.code}); fusion ordering retained."
            )
            return fused

        reranked = apply_reranker_scores(shortlist, scored.scores)
        trace.timings_ms["rerank"] = _ms(started)
        trace.reranked = True
        # Reranked results always outrank the untouched tail: the cross-encoder
        # saw the query and passage together, the tail was never assessed.
        return reranked + remainder

    def _explain_empty(self, scope: RetrievalScope) -> str:
        """Say why nothing was found.

        "No results" and "nothing is indexed" are very different answers, and
        conflating them wastes a user's time looking for a document that was
        never ingested.
        """
        try:
            counts = repositories.count_available(scope)
        except Exception:  # noqa: BLE001 - diagnostics must not mask the result
            return "No matching content was found."

        # Both gaps are reported when both exist. An earlier version returned
        # on the first match, so a clause question against a project with no
        # documents *and* no knowledge base was told only about the documents —
        # hiding the fact more relevant to the question actually asked.
        reasons: list[str] = []

        if scope.reads_project_documents and counts["project_chunks"] == 0:
            reasons.append(
                "No processed documents are available in this scope. Documents "
                "must finish ingestion before they can be searched."
            )
        if scope.reads_knowledge_base and counts["knowledge_base_chunks"] == 0:
            reasons.append(
                f"No published knowledge base exists for edition "
                f"{scope.knowledge_base_edition!r}. It must be ingested and "
                f"validated before it can be searched."
            )

        if reasons:
            return " ".join(reasons)
        return "No content matched the query."


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def build_default_service() -> RetrievalService:
    """Construct the service from current configuration.

    Resolves providers at call time rather than import time, so an
    administrator changing the model does not require a restart.
    """
    ai_settings = settings.AI_SETTINGS
    embedding_model = ai_settings.get("DEFAULT_EMBEDDING_MODEL") or ""

    provider = None
    if embedding_model:
        from claimiq.ai.providers.ollama import OllamaEmbeddingProvider

        provider = OllamaEmbeddingProvider(
            ai_settings["OLLAMA_BASE_URL"], ai_settings["OLLAMA_TIMEOUT_SECONDS"]
        )

    return RetrievalService(
        embedding_provider=provider,
        embedding_model=embedding_model,
        config=dict(settings.RETRIEVAL_SETTINGS),
    )
