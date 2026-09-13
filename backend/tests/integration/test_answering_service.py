"""Tests for grounded answer generation.

The property under test: an answer is either substantiated by its sources or it
is refused. There is no third outcome.
"""
from __future__ import annotations

import json
from typing import Sequence

import pytest

from claimiq.ai.domain.providers import GenerationRequest, GenerationResult, ModelSpec
from claimiq.ai.services.answering import AnsweringService
from claimiq.core.domain.errors import (
    ModelNotConfiguredError,
    StructuredOutputError,
    UncitedFactError,
    UnknownCitationError,
    UnsupportedQuotationError,
)
from claimiq.search.domain.fusion import (
    FusedChunk,
    RetrievalMethod,
    RetrievedChunk,
    assemble_context,
    reciprocal_rank_fusion,
)
from claimiq.search.services.retrieval import RetrievalResult, RetrievalTrace
from claimiq.search.domain import query as query_domain

SOURCE_TEXT = (
    "The claiming Party shall give a Notice to the Engineer, describing the event "
    "or circumstance giving rise to the Claim, no later than 28 days after "
    "becoming aware of it."
)


class FakeLLM:
    """Returns scripted responses. Never reaches a runtime."""

    provider_key = "fake"

    def __init__(self, responses: Sequence[str]) -> None:
        self.responses = list(responses)
        self.requests: list[GenerationRequest] = []

    def is_available(self) -> bool:
        return True

    def list_models(self) -> Sequence[ModelSpec]:
        return ()

    def supports_structured_output(self, model: str) -> bool:
        return True

    def generate(self, model: str, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.responses) - 1)
        return GenerationResult(
            text=self.responses[index],
            model=model,
            prompt_tokens=100,
            completion_tokens=50,
        )


class FakeRetrieval:
    """Returns a fixed context without touching a database."""

    def __init__(self, chunks: Sequence[RetrievedChunk], notes: Sequence[str] = ()) -> None:
        self.chunks = list(chunks)
        self.notes = list(notes)

    def retrieve(self, question: str, scope) -> RetrievalResult:
        fused = reciprocal_rank_fusion([self.chunks]) if self.chunks else []
        context = assemble_context(fused)
        trace = RetrievalTrace(
            query=question, scope=scope.describe(), intent="clause_lookup"
        )
        trace.notes = list(self.notes)
        return RetrievalResult(
            context=context, analysis=query_domain.analyse(question), trace=trace
        )


def source_chunk(chunk_id: str = "c1", *, kb: bool = True) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        text=SOURCE_TEXT,
        document_id="kb-1",
        document_title="FIDIC Red Book 2017",
        page_number=121,
        score=0.9,
        method=RetrievalMethod.CLAUSE_EXACT,
        clause_number="20.2.1",
        edition="red-book-2017" if kb else None,
        is_knowledge_base=kb,
    )


def answer_json(**overrides) -> str:
    payload = {
        "summary": "A Notice is required within 28 days.",
        "insufficient_evidence": False,
        "confidence": "high",
        "findings": [
            {
                "statement": "Sub-Clause 20.2.1 requires a Notice within 28 days.",
                "status": "fact",
                "citations": [{"ref": "S1", "quotation": "no later than 28 days"}],
            }
        ],
    }
    payload.update(overrides)
    return json.dumps(payload)


@pytest.fixture
def scope(organization_id, project_id):
    from claimiq.search.domain.scope import combined_scope

    return combined_scope(
        organization_id=organization_id,
        project_id=project_id,
        edition="red-book-2017",
        accessible_project_ids={project_id},
    )


def service(responses, chunks=None, *, model="qwen2.5:7b-instruct") -> AnsweringService:
    return AnsweringService(
        retrieval_service=FakeRetrieval(chunks if chunks is not None else [source_chunk()]),
        llm_provider=FakeLLM(responses),
        llm_model=model,
    )


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_grounded_answer_is_returned(scope):
    record = service([answer_json()]).ask("What does Sub-Clause 20.2.1 require?", scope)

    assert record.grounding.is_grounded is True
    assert record.answer.summary.startswith("A Notice is required")
    assert record.attempts == 1
    assert record.called_model is True
    assert record.grounding.quotations_verified == 1


def test_sources_are_presented_with_opaque_identifiers(scope):
    svc = service([answer_json()])
    svc.ask("What does 20.2.1 require?", scope)

    prompt = svc.llm_provider.requests[0].prompt
    assert "S1" in prompt
    assert "kb-1" not in prompt, "database identifiers must not reach the model"


def test_schema_is_sent_for_constrained_decoding(scope):
    svc = service([answer_json()])
    svc.ask("What does 20.2.1 require?", scope)
    assert svc.llm_provider.requests[0].json_schema is not None


def test_generation_is_deterministic(scope):
    """Contractual analysis should be reproducible."""
    svc = service([answer_json()])
    svc.ask("q", scope)
    request = svc.llm_provider.requests[0]
    assert request.temperature == 0.0
    assert request.seed is not None


def test_citations_render_with_the_edition(scope):
    record = service([answer_json()]).ask("q", scope)
    rendered = record.citations_rendered()
    assert rendered
    assert "2017" in rendered[0]
    assert "Clause 20.2.1" in rendered[0]


def test_decorated_refs_still_render_citations(scope):
    """Regression: a real model cited "SOURCE ID: S1" and the citation list came
    back empty even though grounding passed, because rendering matched the raw
    string against the canonical identifier."""
    decorated = answer_json(
        findings=[
            {
                "statement": "A Notice is required.",
                "status": "fact",
                "citations": [{"ref": "SOURCE ID: S1"}],
            }
        ]
    )
    record = service([decorated]).ask("q", scope)

    assert record.grounding.is_grounded is True
    assert record.grounding.resolved_refs == ["S1"]
    assert record.citations_rendered(), "a grounded answer must render its citations"


def test_observability_keeps_both_raw_and_resolved_refs(scope):
    decorated = answer_json(
        findings=[
            {
                "statement": "A Notice is required.",
                "status": "fact",
                "citations": [{"ref": "SOURCE ID: S1"}],
            }
        ]
    )
    payload = service([decorated]).ask("q", scope).as_observability_record()
    assert payload["cited_refs"] == ["S1"]
    assert payload["cited_refs_raw"] == ["SOURCE ID: S1"]


# ---------------------------------------------------------------------------
# No sources — no model call
# ---------------------------------------------------------------------------


def test_empty_retrieval_answers_without_calling_the_model(scope):
    """Asking a model to answer from nothing is the thing it is worst at."""
    svc = AnsweringService(
        retrieval_service=FakeRetrieval([], notes=["No processed documents are available."]),
        llm_provider=FakeLLM([answer_json()]),
        llm_model="qwen2.5:7b-instruct",
    )

    record = svc.ask("What does 20.2.1 require?", scope)

    assert record.called_model is False
    assert svc.llm_provider.requests == []
    assert record.answer.insufficient_evidence is True
    assert "No processed documents" in record.answer.summary


def test_missing_model_raises_rather_than_answering_ungrounded(scope):
    svc = AnsweringService(
        retrieval_service=FakeRetrieval([source_chunk()]), llm_provider=None, llm_model=""
    )
    with pytest.raises(ModelNotConfiguredError) as exc:
        svc.ask("q", scope)
    assert exc.value.details["sources_found"] == 1


# ---------------------------------------------------------------------------
# Grounding enforcement
# ---------------------------------------------------------------------------


def test_invented_citation_is_retried_then_raised(scope):
    bad = answer_json(
        findings=[{"statement": "x", "status": "fact", "citations": [{"ref": "S9"}]}]
    )
    svc = service([bad, bad])

    with pytest.raises(UnknownCitationError):
        svc.ask("q", scope)

    assert len(svc.llm_provider.requests) == 2, "one corrective attempt"


def test_correction_names_the_specific_problem(scope):
    """"You cited S9, which does not exist" is actionable; "try again" is not."""
    bad = answer_json(
        findings=[{"statement": "x", "status": "fact", "citations": [{"ref": "S9"}]}]
    )
    svc = service([bad, bad])

    with pytest.raises(UnknownCitationError):
        svc.ask("q", scope)

    correction = svc.llm_provider.requests[1].prompt
    assert "S9" in correction
    assert "not in the source list" in correction


def test_a_corrected_second_attempt_succeeds(scope):
    bad = answer_json(
        findings=[{"statement": "x", "status": "fact", "citations": [{"ref": "S9"}]}]
    )
    svc = service([bad, answer_json()])

    record = svc.ask("q", scope)

    assert record.grounding.is_grounded is True
    assert record.attempts == 2


def test_fabricated_quotation_is_rejected(scope):
    """Changing 28 days to 42 days is fabrication, not paraphrase."""
    bad = answer_json(
        findings=[
            {
                "statement": "Notice within 42 days.",
                "status": "fact",
                "citations": [{"ref": "S1", "quotation": "no later than 42 days"}],
            }
        ]
    )
    with pytest.raises(UnsupportedQuotationError):
        service([bad, bad]).ask("q", scope)


def test_uncited_fact_is_rejected(scope):
    bad = answer_json(
        findings=[{"statement": "The notice was late.", "status": "fact", "citations": []}]
    )
    with pytest.raises(UncitedFactError):
        service([bad, bad]).ask("q", scope)


def test_unknown_status_needs_no_citation(scope):
    """"Insufficient evidence" is a valid outcome, not a grounding failure."""
    response = answer_json(
        insufficient_evidence=True,
        findings=[
            {
                "statement": "The awareness date is not established by the sources.",
                "status": "unknown",
                "citations": [],
            }
        ],
    )
    record = service([response]).ask("q", scope)
    assert record.grounding.is_grounded is True
    assert record.answer.insufficient_evidence is True


def test_malformed_output_is_retried_then_raised(scope):
    svc = service(["not json at all", "still not json"])
    with pytest.raises(StructuredOutputError):
        svc.ask("q", scope)
    assert len(svc.llm_provider.requests) == 2


def test_malformed_then_valid_succeeds(scope):
    svc = service(["not json", answer_json()])
    record = svc.ask("q", scope)
    assert record.attempts == 2
    assert "not valid JSON" in svc.llm_provider.requests[1].prompt


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


def test_observability_record_captures_provenance(scope):
    record = service([answer_json()]).ask("What does 20.2.1 require?", scope)
    payload = record.as_observability_record()

    assert payload["model"] == "qwen2.5:7b-instruct"
    assert payload["prompt"].startswith("grounded_answer@")
    assert payload["source_refs"] == ["S1"]
    assert payload["cited_refs"] == ["S1"]
    assert payload["quotations_verified"] == 1
    assert payload["retrieval"]["intent"] == "clause_lookup"
    assert payload["called_model"] is True


def test_observability_record_excludes_reasoning_traces(scope):
    """Storing raw reasoning creates an authoritative-looking unvalidated record."""
    payload = service([answer_json()]).ask("q", scope).as_observability_record()
    for forbidden in ("chain_of_thought", "reasoning", "thinking", "raw_response"):
        assert forbidden not in payload


def test_unused_sources_are_reported(scope):
    chunks = [source_chunk("c1"), source_chunk("c2")]
    record = service([answer_json()], chunks=chunks).ask("q", scope)
    assert "S2" in record.grounding.unused_sources
