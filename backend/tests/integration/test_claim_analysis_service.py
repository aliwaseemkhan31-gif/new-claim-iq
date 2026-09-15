"""Tests for the claim analysis engine's execution functions.

These need Django imported but no database: the functions under test take plain
inputs. They cover what the run loop depends on — citation resolution, confidence
caps, a failing strand not escaping, and computed notice timing surviving a model
failure.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from claimiq.ai.domain.answers import AnswerConfidence, StructuredAnswer, insufficient_evidence_answer
from claimiq.ai.domain.citations import (
    Citation,
    EpistemicStatus,
    Finding,
    GroundingReport,
    SourceChunk,
)
from claimiq.ai.services.answering import AnswerRecord
from claimiq.analysis.domain.confidence import Confidence
from claimiq.analysis.domain.strands import ClaimFacts, Strand, StrandStatus, plan_analysis
from claimiq.analysis.services.claim_analysis import (
    NOTICE_QUESTION,
    compute_evidence_gaps,
    compute_notice_timing,
    facts_from_dict,
    facts_to_dict,
    is_stalled,
    recipient_label,
    run_model_strand,
    run_notice_strand,
)
from claimiq.claims.domain.evidence_gaps import EvidenceItem, Relevance
from claimiq.claims.domain.notice_compliance import NoticeEvent
from claimiq.core.domain.errors import StructuredOutputError, UnknownCitationError
from claimiq.search.domain.scope import combined_scope

NOTICE_CLAUSES = ["20.2.1", "20.2.4"]


def claim_facts(**overrides) -> ClaimFacts:
    values = dict(
        claim_type="eot",
        title="Extension of time for exceptional rainfall",
        reference="EOT-003",
        contractual_basis=("8.5", "20.2.1"),
        event_date=date(2026, 3, 1),
        awareness_date=date(2026, 3, 1),
        amount_claimed=Decimal("125000.00"),
        currency="USD",
        time_claimed_days=21,
        claimant="Acme Construction",
        respondent="Oversight Engineering",
        edition_code="red-book-2017",
        edition_label="FIDIC Red Book 2017 (2nd Edition)",
    )
    values.update(overrides)
    return ClaimFacts(**values)


def plan(strand: Strand, facts: ClaimFacts | None = None):
    plans = plan_analysis(facts or claim_facts(), notice_clause_numbers=NOTICE_CLAUSES)
    return next(p for p in plans if p.strand is strand)


def source(ref: str, clause: str = "20.2.1") -> SourceChunk:
    return SourceChunk(
        ref=ref,
        text="The claiming Party shall give a Notice to the Engineer no later than 28 days after becoming aware.",
        document_id=f"doc-{ref}",
        document_title="FIDIC Red Book 2017",
        page_number=121,
        clause_number=clause,
        edition="red-book-2017",
        is_knowledge_base=True,
    )


def answer_record(
    findings,
    sources,
    *,
    resolved,
    confidence=AnswerConfidence.HIGH,
    insufficient=False,
    called=True,
) -> AnswerRecord:
    return AnswerRecord(
        answer=StructuredAnswer(
            summary="Summary of the strand.",
            findings=list(findings),
            confidence=confidence,
            insufficient_evidence=insufficient,
        ),
        sources=list(sources),
        grounding=GroundingReport(findings_checked=len(findings), resolved_refs=list(resolved)),
        model="qwen2.5:3b-instruct" if called else "",
        prompt_identifier="entitlement_analysis@1.1.0" if called else "",
        retrieval_trace={"intent": "entitlement"},
        called_model=called,
    )


class FakeAnswering:
    def __init__(self, record: AnswerRecord | None = None, error: Exception | None = None) -> None:
        self.record = record
        self.error = error
        self.calls: list[dict] = []

    def ask(self, question, scope, *, prompt_key="grounded_answer", extra_variables=None):
        self.calls.append(
            {"question": question, "prompt_key": prompt_key, "extra": dict(extra_variables or {})}
        )
        if self.error is not None:
            raise self.error
        return self.record


@pytest.fixture
def scope(organization_id, project_id):
    return combined_scope(
        organization_id=organization_id,
        project_id=project_id,
        edition="red-book-2017",
        accessible_project_ids={project_id},
    )


# ---------------------------------------------------------------------------
# Model strands
# ---------------------------------------------------------------------------


def test_findings_map_with_resolved_citations(scope):
    """A decorated ref from a real model still resolves to its source."""
    record = answer_record(
        [
            Finding("Notice is required within 28 days.", EpistemicStatus.FACT,
                    (Citation("SOURCE ID: S1", quotation="no later than 28 days"),)),
            Finding("The claimant must show awareness.", EpistemicStatus.INFERENCE,
                    (Citation("S2"),)),
        ],
        [source("S1"), source("S2", clause="20.2.4")],
        resolved=["S1", "S2"],
    )
    outcome = run_model_strand(plan(Strand.ENTITLEMENT), claim_facts(), scope=scope,
                               answering=FakeAnswering(record))

    assert outcome.status is StrandStatus.COMPLETED
    first = outcome.findings[0]["citations"][0]
    assert first["ref"] == "S1"
    assert first["document_id"] == "doc-S1"
    assert "Clause 20.2.1" in first["rendered"]
    assert outcome.findings[1]["epistemic_status"] == "inference"
    assert outcome.confidence.level is Confidence.HIGH


def test_strand_sends_its_prompt_claim_summary_and_clause_query(scope):
    record = answer_record([], [], resolved=[], insufficient=True)
    fake = FakeAnswering(record)
    run_model_strand(plan(Strand.CAUSATION), claim_facts(), scope=scope, answering=fake)

    call = fake.calls[0]
    assert call["prompt_key"] == "causation_analysis"
    assert "Amount claimed: USD 125,000.00" in call["extra"]["claim_summary"]
    assert "Clause 20.2.1" in call["question"]


def test_single_cited_source_caps_confidence_whatever_the_model_says(scope):
    record = answer_record(
        [Finding("Notice is required.", EpistemicStatus.FACT, (Citation("S1"),))],
        [source("S1"), source("S2")],
        resolved=["S1"],
        confidence=AnswerConfidence.HIGH,
    )
    outcome = run_model_strand(plan(Strand.ENTITLEMENT), claim_facts(), scope=scope,
                               answering=FakeAnswering(record))
    assert outcome.confidence.level is Confidence.MEDIUM
    assert any("single source" in r for r in outcome.confidence.reasons)


def test_missing_evidence_on_record_caps_confidence_low(scope):
    record = answer_record(
        [Finding("Rain delayed the works.", EpistemicStatus.FACT, (Citation("S1"),)),
         Finding("Programme shows impact.", EpistemicStatus.FACT, (Citation("S2"),))],
        [source("S1"), source("S2")],
        resolved=["S1", "S2"],
    )
    outcome = run_model_strand(plan(Strand.CAUSATION), claim_facts(), scope=scope,
                               answering=FakeAnswering(record), evidence_status="missing")
    assert outcome.confidence.level is Confidence.LOW


def test_grounding_failure_fails_the_strand_without_escaping(scope):
    """One strand failing must not take down the run."""
    fake = FakeAnswering(error=UnknownCitationError("The response cited sources that were not retrieved."))
    outcome = run_model_strand(plan(Strand.QUANTUM), claim_facts(), scope=scope, answering=fake)

    assert outcome.status is StrandStatus.FAILED
    assert outcome.error_code == "ai_unknown_citation"
    assert outcome.findings == []
    assert outcome.confidence is None


def test_no_sources_is_completed_insufficient_and_low(scope):
    record = AnswerRecord(
        answer=insufficient_evidence_answer("No published knowledge base exists."),
        sources=[],
        grounding=GroundingReport(),
        model="",
        prompt_identifier="",
        retrieval_trace={},
        called_model=False,
    )
    outcome = run_model_strand(plan(Strand.TIME_IMPACT), claim_facts(), scope=scope,
                               answering=FakeAnswering(record))
    assert outcome.status is StrandStatus.COMPLETED
    assert outcome.insufficient_evidence is True
    assert outcome.confidence.level is Confidence.LOW
    assert outcome.model_called is False
    assert outcome.findings == [], "a system explanation is not a reviewable AI finding"
    assert "No published knowledge base" in outcome.summary


def test_notice_timing_keeps_its_confidence_when_nothing_is_retrieved(scope):
    record = AnswerRecord(
        answer=insufficient_evidence_answer("No published knowledge base exists."),
        sources=[],
        grounding=GroundingReport(),
        model="",
        prompt_identifier="",
        retrieval_trace={},
        called_model=False,
    )
    outcome = run_notice_strand(plan(Strand.NOTICE_COMPLIANCE), claim_facts(), [notice()],
                                scope=scope, answering=FakeAnswering(record))
    assert outcome.status is StrandStatus.COMPLETED
    assert outcome.confidence.level is Confidence.MEDIUM, "timing's own level, not LOW"
    assert any("No contract sources" in r for r in outcome.confidence.reasons)
    assert "day 19 of 28" in outcome.summary
    assert outcome.findings == []


# ---------------------------------------------------------------------------
# Notice timing — computed, and kept whatever the model does
# ---------------------------------------------------------------------------


def notice(**overrides) -> NoticeEvent:
    values = dict(
        document_id="corr-1",
        document_title="Notice of Claim L-042",
        sent_date=date(2026, 3, 18),
        received_date=date(2026, 3, 20),
        recipient=recipient_label("Oversight Engineering", "engineer"),
        is_confirmed_notice=True,
        clause_number="20.2.1",
    )
    values.update(overrides)
    return NoticeEvent(**values)


def test_notice_counts_only_towards_its_own_provision():
    """Regression: a 20.2.1 Notice of Claim was being accepted as the 20.2.4
    fully detailed claim, reporting a submission that never happened."""
    computation = compute_notice_timing("red-book-2017", date(2026, 3, 1), [notice()])
    by_clause = {f["clause_number"]: f for f in computation.computed["findings"]}

    assert by_clause["20.2.1"]["status"] == "compliant"
    assert by_clause["20.2.1"]["deadline"] == "2026-03-29"
    assert by_clause["20.2.1"]["is_condition_precedent"] is True
    assert by_clause["20.2.4"]["status"] == "not_given"
    assert any("other provisions" in w for w in by_clause["20.2.4"]["warnings"])
    assert "Deadline: 2026-03-29" in computation.prompt_text
    assert computation.confidence.level is Confidence.MEDIUM, "a notice not found caps it"


def test_both_notices_given_is_high_confidence():
    detailed = notice(document_id="corr-2", document_title="Fully detailed claim",
                      sent_date=date(2026, 5, 10), received_date=date(2026, 5, 11),
                      clause_number="20.2.4")
    computation = compute_notice_timing("red-book-2017", date(2026, 3, 1), [notice(), detailed])
    assert {f["status"] for f in computation.computed["findings"]} == {"compliant"}
    assert computation.confidence.level is Confidence.HIGH


def test_correctly_addressed_notice_raises_no_recipient_caveat():
    """A firm name is matched against the requirement's role through its party role."""
    computation = compute_notice_timing("red-book-2017", date(2026, 3, 1), [notice()])
    warnings = computation.computed["findings"][0]["warnings"]
    assert not any("recipient" in w for w in warnings)


def test_unknown_awareness_date_is_indeterminate_and_low():
    computation = compute_notice_timing("red-book-2017", None, [notice()])
    assert all(f["status"] == "indeterminate" for f in computation.computed["findings"])
    assert "cannot be computed" in computation.prompt_text
    assert computation.confidence.level is Confidence.LOW


def test_assumed_receipt_date_lowers_confidence():
    computation = compute_notice_timing(
        "red-book-2017", date(2026, 3, 1), [notice(received_date=None)]
    )
    assert computation.confidence.level is Confidence.MEDIUM
    assert "Assumption:" in computation.prompt_text


def test_notice_strand_sends_computed_timing_and_its_own_question(scope):
    fake = FakeAnswering(answer_record([], [], resolved=[], insufficient=True))
    run_notice_strand(plan(Strand.NOTICE_COMPLIANCE), claim_facts(), [notice()],
                      scope=scope, answering=fake)
    extra = fake.calls[0]["extra"]
    assert fake.calls[0]["prompt_key"] == "notice_compliance"
    assert "Clause 20.2.1" in extra["computed_finding"]
    assert extra["question"] == NOTICE_QUESTION


def test_computed_timing_survives_a_model_failure(scope):
    """The explanation failed; the arithmetic did not, and must stay on record."""
    fake = FakeAnswering(error=StructuredOutputError("The model response is not valid JSON."))
    outcome = run_notice_strand(plan(Strand.NOTICE_COMPLIANCE), claim_facts(), [notice()],
                                scope=scope, answering=fake)

    assert outcome.status is StrandStatus.FAILED
    assert outcome.error_code == "ai_structured_output_invalid"
    assert outcome.computed["findings"][0]["status"] == "compliant"
    assert outcome.confidence.level is Confidence.MEDIUM
    assert "day 19 of 28" in outcome.summary


def test_notice_confidence_combines_timing_and_explanation(scope):
    record = answer_record(
        [Finding("Notice was given within the period.", EpistemicStatus.FACT, (Citation("S1"),))],
        [source("S1")],
        resolved=["S1"],
    )
    outcome = run_notice_strand(plan(Strand.NOTICE_COMPLIANCE), claim_facts(), [notice()],
                                scope=scope, answering=FakeAnswering(record))
    assert outcome.status is StrandStatus.COMPLETED
    assert outcome.confidence.level is Confidence.MEDIUM, "single cited source caps the explanation"
    assert "Computed from recorded dates." in outcome.confidence.reasons


@pytest.mark.parametrize(
    ("name", "role", "expected"),
    [
        ("Oversight Engineering", "engineer", "Oversight Engineering (the Engineer)"),
        ("Acme Construction", "contractor", "Acme Construction (the Contractor)"),
        ("Supplier Ltd", "supplier", "Supplier Ltd"),
        ("", "engineer", "the Engineer"),
        ("", None, ""),
    ],
)
def test_recipient_label(name, role, expected):
    assert recipient_label(name, role) == expected


# ---------------------------------------------------------------------------
# Evidence gaps
# ---------------------------------------------------------------------------


def test_evidence_gaps_payload_and_confidence():
    items = [
        EvidenceItem("e1", "Met Office record", "causation", Relevance.SUPPORTS, is_reviewed=True),
        EvidenceItem("e2", "Notice L-042", "notice", Relevance.SUPPORTS, is_reviewed=True),
    ]
    outcome, report = compute_evidence_gaps("eot", items)
    elements = {e["code"]: e for e in outcome.computed["elements"]}

    assert outcome.status is StrandStatus.COMPLETED
    assert elements["causation"]["status"] == "established"
    assert elements["time_impact"]["is_gap"] is True
    assert outcome.confidence.level is Confidence.HIGH
    assert report.is_complete is False


def test_unreviewed_evidence_lowers_gap_report_confidence():
    items = [EvidenceItem("e1", "Suggested record", "causation", Relevance.SUPPORTS, is_reviewed=False)]
    outcome, _ = compute_evidence_gaps("eot", items)
    assert outcome.confidence.level is Confidence.MEDIUM


# ---------------------------------------------------------------------------
# Snapshot and staleness
# ---------------------------------------------------------------------------


def test_claim_snapshot_round_trips():
    """The run analyses the claim as it was, so the snapshot must be lossless."""
    original = claim_facts(notice_date=None, description="Rainfall")
    restored = facts_from_dict(facts_to_dict(original))
    assert restored == original


def test_snapshot_tolerates_missing_and_bad_values():
    restored = facts_from_dict({"title": "T", "amount_claimed": "not-a-number"})
    assert restored.claim_type == "other"
    assert restored.amount_claimed is None
    assert restored.awareness_date is None


def test_stalled_run_detection():
    now = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    fresh = SimpleNamespace(is_terminal=False, updated_at=now - timedelta(minutes=5))
    stale = SimpleNamespace(is_terminal=False, updated_at=now - timedelta(hours=2))
    finished = SimpleNamespace(is_terminal=True, updated_at=now - timedelta(days=3))
    assert is_stalled(fresh, now) is False
    assert is_stalled(stale, now) is True
    assert is_stalled(finished, now) is False
