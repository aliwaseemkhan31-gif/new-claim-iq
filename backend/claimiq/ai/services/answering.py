"""Grounded answer generation.

The full path from a question to a validated answer:

    retrieve -> assemble sources -> render prompt -> generate ->
    parse -> validate grounding -> return

Three properties this service is responsible for:

1. **No sources means no model call.** Asking a model to answer from nothing
   while instructing it not to speculate is asking it to do the thing it is
   worst at, and paying for the privilege. Retrieval returning empty produces
   an ``insufficient_evidence`` answer directly.

2. **Grounding failures are retried once, then raised.** A model that invents a
   citation is given one corrective attempt with the specific problem named.
   A second failure is a failure — never a degraded answer.

3. **Everything is recorded.** Model, prompt version, retrieval trace, source
   ids, grounding report, timings. Without it, "why did it say that" is
   unanswerable, which is not acceptable for a system making contractual
   assertions.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from claimiq.ai.domain.answers import (
    ANSWER_SCHEMA,
    StructuredAnswer,
    insufficient_evidence_answer,
    parse_answer,
)
from claimiq.ai.domain.citations import (
    GroundingReport,
    SourceChunk,
    assign_refs,
    build_source_map,
    enforce_grounding,
    render_sources_block,
    validate_findings,
)
from claimiq.ai.domain.prompts import PromptTemplate, get_prompt
from claimiq.ai.domain.providers import GenerationRequest, LLMProvider
from claimiq.core.domain.errors import (
    GroundingError,
    ModelNotConfiguredError,
    StructuredOutputError,
)
from claimiq.core.logging import get_logger
from claimiq.search.domain.fusion import AssembledContext
from claimiq.search.domain.scope import RetrievalScope
from claimiq.search.services.retrieval import RetrievalResult, RetrievalService

logger = get_logger("ai.answering")

#: One corrective attempt. More would mostly burn time on a model that has
#: misunderstood the task; zero would fail on a recoverable slip.
MAX_GROUNDING_RETRIES = 1


@dataclass
class AnswerRecord:
    """An answer plus the complete record of how it was produced."""

    answer: StructuredAnswer
    sources: list[SourceChunk]
    grounding: GroundingReport
    model: str
    prompt_identifier: str
    retrieval_trace: dict[str, Any]
    latency_ms: float = 0.0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    attempts: int = 1
    called_model: bool = True

    def citations_rendered(self) -> list[str]:
        """Human-readable citations for the sources actually cited.

        Uses the grounding report's *resolved* identifiers, not the raw strings
        the model supplied. A model that cites "SOURCE ID: S1" has cited S1,
        but matching that raw string against a source's identifier finds
        nothing — which silently rendered an empty citation list even though
        the answer was properly grounded.
        """
        cited = set(self.grounding.resolved_refs)
        return [s.render_citation() for s in self.sources if s.ref in cited]

    def as_observability_record(self) -> dict[str, Any]:
        """The payload persisted for AI observability.

        Chain-of-thought is deliberately absent. Storing raw reasoning traces
        creates a record that reads as authoritative while being unvalidated,
        which is the confusion this architecture exists to prevent.
        """
        return {
            "model": self.model,
            "prompt": self.prompt_identifier,
            "called_model": self.called_model,
            "attempts": self.attempts,
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "source_refs": [s.ref for s in self.sources],
            "source_documents": sorted({s.document_id for s in self.sources}),
            "cited_refs": list(self.grounding.resolved_refs),
            "cited_refs_raw": sorted(self.answer.cited_refs),
            "unused_sources": self.grounding.unused_sources,
            "confidence": self.answer.confidence.value,
            "insufficient_evidence": self.answer.insufficient_evidence,
            "finding_count": len(self.answer.findings),
            "quotations_verified": self.grounding.quotations_verified,
            "retrieval": self.retrieval_trace,
        }


def context_to_sources(context: AssembledContext) -> list[SourceChunk]:
    """Convert assembled chunks into citable sources with opaque identifiers.

    The model never sees database ids. The citation space is closed and small,
    so an invented reference is immediately detectable (ADR 0005).
    """
    raw = [
        SourceChunk(
            ref="pending",
            text=fused.chunk.text,
            document_id=fused.chunk.document_id,
            document_title=fused.chunk.document_title,
            page_number=fused.chunk.page_number,
            clause_number=fused.chunk.clause_number,
            edition=fused.chunk.edition,
            is_knowledge_base=fused.chunk.is_knowledge_base,
        )
        for fused in context.chunks
    ]
    return assign_refs(raw)


class AnsweringService:
    """Produces grounded answers to questions about a project's documents."""

    def __init__(
        self,
        *,
        retrieval_service: RetrievalService,
        llm_provider: LLMProvider | None = None,
        llm_model: str = "",
    ) -> None:
        self.retrieval = retrieval_service
        self.llm_provider = llm_provider
        self.llm_model = llm_model

    def ask(
        self,
        question: str,
        scope: RetrievalScope,
        *,
        prompt_key: str = "grounded_answer",
        extra_variables: dict[str, Any] | None = None,
    ) -> AnswerRecord:
        """Answer ``question`` within ``scope``.

        Raises:
            ModelNotConfiguredError: no LLM is configured. Deliberately not a
                fallback to an ungrounded answer — there is no such thing here.
            StructuredOutputError: the model could not produce valid output.
            GroundingError: the response could not be substantiated after a
                corrective attempt.
        """
        started = time.perf_counter()
        retrieval = self.retrieval.retrieve(question, scope)

        if retrieval.is_empty:
            return self._no_sources_record(retrieval)

        if self.llm_provider is None or not self.llm_model:
            raise ModelNotConfiguredError(
                "No language model is configured, so a grounded answer cannot "
                "be produced. Retrieval succeeded; the sources are available "
                "for reading directly.",
                details={
                    "sources_found": len(retrieval.context.chunks),
                    "remedy": "Select a language model in the administration interface.",
                },
            )

        sources = context_to_sources(retrieval.context)
        source_map = build_source_map(sources)
        prompt = get_prompt(prompt_key)

        variables: dict[str, Any] = {
            "question": question,
            "sources": render_sources_block(sources),
        }
        variables.update(extra_variables or {})

        record = self._generate_and_validate(
            prompt=prompt,
            variables=variables,
            sources=sources,
            source_map=source_map,
            scope=scope,
            retrieval=retrieval,
        )
        record.latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return record

    # ------------------------------------------------------------------

    def _generate_and_validate(
        self,
        *,
        prompt: PromptTemplate,
        variables: dict[str, Any],
        sources: list[SourceChunk],
        source_map: dict[str, SourceChunk],
        scope: RetrievalScope,
        retrieval: RetrievalResult,
    ) -> AnswerRecord:
        rendered = prompt.render(**variables)
        correction = ""
        last_error: GroundingError | None = None

        for attempt in range(1, MAX_GROUNDING_RETRIES + 2):
            result = self.llm_provider.generate(  # type: ignore[union-attr]
                self.llm_model,
                GenerationRequest(
                    prompt=rendered + correction,
                    system_prompt=prompt.system,
                    temperature=0.0,
                    json_schema=ANSWER_SCHEMA,
                    # Fixed seed: a contractual analysis should be reproducible.
                    seed=1,
                ),
            )

            try:
                answer = parse_answer(result.text)
            except StructuredOutputError:
                if attempt > MAX_GROUNDING_RETRIES:
                    raise
                correction = (
                    "\n\nYour previous response was not valid JSON matching the "
                    "required schema. Respond with the schema exactly."
                )
                continue

            grounding = validate_findings(
                answer.findings,
                source_map,
                expected_edition=scope.knowledge_base_edition,
            )

            if grounding.is_grounded:
                logger.info(
                    "ai.answer_grounded",
                    extra={
                        "model": self.llm_model,
                        "prompt": prompt.identifier,
                        "attempts": attempt,
                        "findings": len(answer.findings),
                        "quotations_verified": grounding.quotations_verified,
                    },
                )
                return AnswerRecord(
                    answer=_humanised(answer, {s.ref for s in sources}),
                    sources=sources,
                    grounding=grounding,
                    model=self.llm_model,
                    prompt_identifier=prompt.identifier,
                    retrieval_trace=retrieval.trace.as_dict(),
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                    attempts=attempt,
                )

            try:
                enforce_grounding(grounding)
            except GroundingError as exc:
                last_error = exc

            if attempt > MAX_GROUNDING_RETRIES:
                break

            correction = self._correction_for(grounding, sources)
            logger.warning(
                "ai.grounding_retry",
                extra={
                    "model": self.llm_model,
                    "attempt": attempt,
                    "summary": grounding.summary(),
                },
            )

        # Out of attempts. Raise the specific failure rather than returning
        # something unsubstantiated.
        if last_error is not None:
            raise last_error
        raise GroundingError("The response could not be substantiated.")

    def _correction_for(
        self, grounding: GroundingReport, sources: list[SourceChunk]
    ) -> str:
        """Build a corrective instruction naming the specific problem.

        Specific beats generic: "you cited S7, which does not exist" is
        actionable in a way that "try again" is not.
        """
        available = ", ".join(s.ref for s in sources)
        parts = ["\n\nYour previous response could not be substantiated."]

        if grounding.unknown_refs:
            parts.append(
                f"You cited {', '.join(sorted(set(grounding.unknown_refs)))}, which "
                f"are not in the source list. The only valid identifiers are: {available}."
            )
        if grounding.unsupported_quotations:
            refs = ", ".join(sorted({ref for ref, _ in grounding.unsupported_quotations}))
            parts.append(
                f"Quotations attributed to {refs} do not appear in those sources. "
                f"Quote exactly, or omit the quotation and cite the source alone."
            )
        if grounding.uncited_facts:
            parts.append(
                "Some findings marked 'fact' or 'inference' carry no citation. "
                "Cite the source, or change the status to 'unknown'."
            )
        if grounding.edition_mismatches:
            parts.append(
                "You cited sources from a different contract edition than the one "
                "in scope. Use only the sources provided."
            )

        parts.append("Correct these and respond again with the schema.")
        return " ".join(parts)

    def _no_sources_record(self, retrieval: RetrievalResult) -> AnswerRecord:
        """Answer directly when retrieval found nothing. No model is called."""
        reason = (
            retrieval.trace.notes[0]
            if retrieval.trace.notes
            else "No relevant content was found in the documents available to you."
        )
        logger.info("ai.no_sources", extra={"reason": reason})
        return AnswerRecord(
            answer=insufficient_evidence_answer(reason),
            sources=[],
            grounding=GroundingReport(),
            model="",
            prompt_identifier="",
            retrieval_trace=retrieval.trace.as_dict(),
            called_model=False,
        )


def build_default_answering_service() -> AnsweringService:
    """Construct the service from current configuration."""
    from claimiq.ai.services.configuration import resolve_ai_settings
    from claimiq.search.services.retrieval import build_default_service

    # Resolved rather than read from settings, so an administrator's choice in
    # Administration takes effect on the next question without a restart.
    ai_settings = resolve_ai_settings()
    llm_model = ai_settings.get("DEFAULT_LLM_MODEL") or ""

    provider = None
    if llm_model:
        from claimiq.ai.providers.ollama import OllamaLLMProvider

        provider = OllamaLLMProvider(
            ai_settings["OLLAMA_BASE_URL"],
            ai_settings["OLLAMA_TIMEOUT_SECONDS"],
            max_output_tokens=ai_settings.get("LLM_MAX_OUTPUT_TOKENS"),
            context_tokens=ai_settings.get("LLM_CONTEXT_TOKENS"),
        )

    return AnsweringService(
        retrieval_service=build_default_service(),
        llm_provider=provider,
        llm_model=llm_model,
    )


def _humanised(answer, known_refs):
    """The answer with the prompt's source identifiers removed from its prose.

    Observed on the N-55 contract: "S1 governs the Conditions of Particular
    Application, and S2 and S3 govern the Supplementary Conditions". Citations
    are untouched — they carry the identifiers the reader's links resolve.
    """
    import dataclasses

    from claimiq.ai.domain.citations import humanise_refs

    return dataclasses.replace(
        answer,
        summary=humanise_refs(answer.summary, known_refs),
        findings=[
            dataclasses.replace(f, statement=humanise_refs(f.statement, known_refs))
            for f in answer.findings
        ],
    )
