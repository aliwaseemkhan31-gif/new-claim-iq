"""Tests for structured answers and prompt templates.

The property under test: a contractual assessment either parses completely or
fails. There is no partial parse and no regex fallback — the prototype scraped
its verdict from prose with a browser-side regex, and any rephrasing silently
changed the answer shown.
"""
from __future__ import annotations

import json

import pytest

from claimiq.ai.domain.answers import (
    ANSWER_SCHEMA,
    AnswerConfidence,
    insufficient_evidence_answer,
    parse_answer,
)
from claimiq.ai.domain.citations import EpistemicStatus
from claimiq.ai.domain.prompts import (
    ALL_PROMPTS,
    GROUNDED_ANSWER,
    NOTICE_COMPLIANCE_ANALYSIS,
    PromptTemplate,
    get_prompt,
    validate_prompts,
)
from claimiq.core.domain.errors import NotFoundError, StructuredOutputError, ValidationError

VALID = {
    "summary": "A Notice of Claim was required within 28 days.",
    "insufficient_evidence": False,
    "confidence": "high",
    "findings": [
        {
            "statement": "Sub-Clause 20.2.1 requires a Notice within 28 days.",
            "status": "fact",
            "citations": [{"ref": "S1", "quotation": "no later than 28 days"}],
        }
    ],
    "missing_information": [],
    "caveats": [],
}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_valid_response_parses() -> None:
    answer = parse_answer(json.dumps(VALID))
    assert answer.summary.startswith("A Notice of Claim")
    assert answer.confidence is AnswerConfidence.HIGH
    assert len(answer.findings) == 1
    assert answer.findings[0].status is EpistemicStatus.FACT
    assert answer.cited_refs == {"S1"}


def test_mapping_input_is_accepted() -> None:
    assert parse_answer(VALID).summary == VALID["summary"]


def test_empty_response_is_rejected() -> None:
    with pytest.raises(StructuredOutputError):
        parse_answer("")


def test_malformed_json_is_rejected_not_salvaged() -> None:
    """No regex fallback. Recovering "most of" an assessment is the old failure."""
    with pytest.raises(StructuredOutputError) as exc:
        parse_answer('{"summary": "half a resp')
    assert "not valid JSON" in exc.value.message


def test_prose_response_is_rejected() -> None:
    with pytest.raises(StructuredOutputError):
        parse_answer("The claim appears VALID based on Clause 20.2.1.")


def test_missing_summary_is_rejected() -> None:
    payload = {k: v for k, v in VALID.items() if k != "summary"}
    with pytest.raises(StructuredOutputError) as exc:
        parse_answer(payload)
    assert exc.value.details["field"] == "summary"


def test_empty_summary_is_rejected() -> None:
    with pytest.raises(StructuredOutputError):
        parse_answer({**VALID, "summary": "   "})


def test_unknown_status_is_rejected() -> None:
    payload = {**VALID, "findings": [{**VALID["findings"][0], "status": "probably"}]}
    with pytest.raises(StructuredOutputError) as exc:
        parse_answer(payload)
    assert "fact" in exc.value.details["valid"]


def test_unknown_confidence_is_rejected() -> None:
    with pytest.raises(StructuredOutputError):
        parse_answer({**VALID, "confidence": "very high indeed"})


def test_citation_without_a_ref_is_rejected() -> None:
    payload = {
        **VALID,
        "findings": [{**VALID["findings"][0], "citations": [{"quotation": "x"}]}],
    }
    with pytest.raises(StructuredOutputError):
        parse_answer(payload)


def test_finding_without_a_statement_is_rejected() -> None:
    payload = {**VALID, "findings": [{"status": "fact", "citations": []}]}
    with pytest.raises(StructuredOutputError):
        parse_answer(payload)


def test_no_findings_without_insufficient_flag_is_contradictory() -> None:
    """Claiming the sources answered while citing nothing from them."""
    with pytest.raises(StructuredOutputError) as exc:
        parse_answer({**VALID, "findings": [], "insufficient_evidence": False})
    assert "insufficient" in exc.value.message.lower()


# ---------------------------------------------------------------------------
# Insufficient evidence is a first-class outcome
# ---------------------------------------------------------------------------


def test_insufficient_evidence_with_no_findings_is_valid() -> None:
    answer = parse_answer(
        {
            "summary": "The available documents do not establish when the Contractor became aware.",
            "insufficient_evidence": True,
            "confidence": "low",
            "findings": [],
        }
    )
    assert answer.insufficient_evidence is True
    assert answer.findings == []


def test_unknown_findings_are_retrievable() -> None:
    answer = parse_answer(
        {
            **VALID,
            "insufficient_evidence": True,
            "findings": [
                {"statement": "The awareness date is not established.", "status": "unknown",
                 "citations": []}
            ],
        }
    )
    assert len(answer.unknowns()) == 1
    assert answer.facts() == []


def test_helper_builds_an_insufficient_evidence_answer() -> None:
    """Used when retrieval returns nothing, so no model is called at all."""
    answer = insufficient_evidence_answer(
        "No processed documents are available in this scope.",
        missing=["An ingested contract"],
    )
    assert answer.insufficient_evidence is True
    assert answer.confidence is AnswerConfidence.LOW
    assert answer.findings[0].status is EpistemicStatus.UNKNOWN
    assert answer.missing_information == ["An ingested contract"]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_schema_forbids_extra_properties() -> None:
    """A model inventing a field has misunderstood; accepting it hides that."""
    assert ANSWER_SCHEMA["additionalProperties"] is False
    finding_schema = ANSWER_SCHEMA["properties"]["findings"]["items"]
    assert finding_schema["additionalProperties"] is False


def test_schema_requires_the_insufficient_evidence_branch() -> None:
    assert "insufficient_evidence" in ANSWER_SCHEMA["required"]


def test_schema_enumerates_epistemic_status() -> None:
    statuses = ANSWER_SCHEMA["properties"]["findings"]["items"]["properties"]["status"]["enum"]
    assert set(statuses) == {s.value for s in EpistemicStatus}


def test_schema_is_json_serialisable() -> None:
    json.dumps(ANSWER_SCHEMA)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


def test_prompt_catalogue_is_consistent() -> None:
    """Catches placeholders and declared variables drifting apart."""
    assert validate_prompts() == []


def test_every_prompt_is_versioned() -> None:
    for prompt in ALL_PROMPTS:
        assert prompt.version
        assert "@" in prompt.identifier


def test_rendering_substitutes_variables() -> None:
    rendered = GROUNDED_ANSWER.render(question="What does 20.2.1 require?", sources="S1 ...")
    assert "What does 20.2.1 require?" in rendered
    assert "{question}" not in rendered


def test_missing_variable_fails_loudly() -> None:
    """A literal {sources} reaching the model is worse than an error."""
    with pytest.raises(ValidationError) as exc:
        GROUNDED_ANSWER.render(question="x")
    assert "sources" in exc.value.details["missing"]


def test_unknown_prompt_raises() -> None:
    with pytest.raises(NotFoundError):
        get_prompt("no_such_prompt")


def test_system_prompt_states_the_rules_the_validators_enforce() -> None:
    system = GROUNDED_ANSWER.system.lower()
    assert "only the numbered sources" in system
    assert "never cite an identifier that is not in the source list" in system
    assert "not a legal authority" in system
    assert "edition" in system


def test_notice_prompt_forbids_recalculating_the_arithmetic() -> None:
    """Timing is computed deterministically; the model explains, it does not compute."""
    template = NOTICE_COMPLIANCE_ANALYSIS.template.lower()
    assert "do not recalculate" in template


def test_undeclared_placeholder_is_caught_by_validation() -> None:
    broken = PromptTemplate(
        key="broken",
        version="1.0.0",
        system="",
        template="Hello {name}",
        required_variables=frozenset(),
    )
    import re

    placeholders = set(re.findall(r"\{(\w+)\}", broken.template))
    assert placeholders - broken.required_variables == {"name"}


# ---------------------------------------------------------------------------
# Source labels in prose
#
# Sources reach the model as "SOURCE ID: S1" so citations can be checked
# against a closed set. Observed from qwen2.5:3b-instruct on a real claim:
# "SOURCE ID: S1 establishes the conditions under which the Engineer may
# determine an extension of time" — internal scaffolding presented to a reader.
# ---------------------------------------------------------------------------


def test_humanise_refs_replaces_a_decorated_label_in_prose() -> None:
    from claimiq.ai.domain.citations import humanise_refs

    assert humanise_refs("SOURCE ID: S1 establishes the period.") == (
        "The cited source establishes the period."
    )
    assert humanise_refs("Source S2 outlines the requirement.") == (
        "The cited source outlines the requirement."
    )


def test_humanise_refs_reads_a_list_of_sources_as_plural() -> None:
    from claimiq.ai.domain.citations import humanise_refs

    assert humanise_refs("SOURCE ID: S1, S2 and S3 together set the period.") == (
        "The cited sources together set the period."
    )


def test_humanise_refs_leaves_a_bare_token_alone() -> None:
    """It may be a section, a chainage or a party's shorthand in the contract."""
    from claimiq.ai.domain.citations import humanise_refs

    text = "Chainage S1 was handed over on 10 August."
    assert humanise_refs(text) == text
    assert humanise_refs("") == ""


def test_parsed_findings_carry_no_source_scaffolding() -> None:
    from claimiq.ai.domain.answers import parse_answer

    answer = parse_answer(
        {
            "summary": "The notice period is 28 days.",
            "confidence": "low",
            "findings": [
                {
                    "statement": "SOURCE ID: S1 establishes the notice period.",
                    "status": "fact",
                    "citations": [{"ref": "S1"}],
                }
            ],
        }
    )
    assert answer.findings[0].statement == "The cited source establishes the notice period."


def test_humanise_refs_replaces_bare_identifiers_that_were_assembled() -> None:
    """Observed on the N-55 contract: "S1 governs the Conditions of Particular Application"."""
    from claimiq.ai.domain.citations import humanise_refs

    assert humanise_refs(
        "S1 governs the Particular Conditions, and S2 and S3 govern the SCC.",
        {"S1", "S2", "S3"},
    ) == "The cited source governs the Particular Conditions, and the cited sources govern the SCC."


def test_humanise_refs_keeps_a_bare_token_that_is_not_an_assembled_source() -> None:
    from claimiq.ai.domain.citations import humanise_refs

    assert humanise_refs("Section S9 was handed over.", {"S1", "S2"}) == "Section S9 was handed over."
