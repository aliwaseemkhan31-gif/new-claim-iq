"""Tests for the claim analysis domain: planning, confidence and review.

Three properties carry the weight here. A strand that does not apply says why
rather than vanishing. Confidence is computed from checkable facts and the
model can only lower it. A reviewer's decision never overwrites what the AI said.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from claimiq.ai.domain.prompts import get_prompt
from claimiq.analysis.domain.confidence import (
    Confidence,
    ConfidenceAssessment,
    GroundedInputs,
    assess_evidence_record,
    assess_grounded_answer,
    assess_notice_timing,
    combine,
    lowest,
)
from claimiq.analysis.domain.review import (
    MIN_REASON_CHARS,
    ReviewAction,
    ReviewEntry,
    ReviewState,
    resolve_review,
    validate_review,
)
from claimiq.analysis.domain.strands import (
    CLAIM_TYPE_LABELS,
    MODEL_STRANDS,
    STRAND_ORDER,
    ClaimFacts,
    RunStatus,
    Strand,
    StrandStatus,
    build_claim_summary,
    plan_analysis,
    progress_percent,
    retrieval_query_for,
    summarise_run,
)
from claimiq.claims.domain.evidence_gaps import (
    ElementStatus,
    EvidenceItem,
    Relevance,
    analyse_gaps,
    element_code_for_issue_category,
)
from claimiq.core.domain.errors import ValidationError
from claimiq.search.domain.query import QueryIntent, analyse


def facts(**overrides) -> ClaimFacts:
    values = dict(
        claim_type="eot",
        title="Extension of time for exceptional rainfall",
        reference="EOT-003",
        contractual_basis=("8.5", "20.2.1"),
        event_date=date(2026, 3, 1),
        awareness_date=date(2026, 3, 1),
        amount_claimed=Decimal("125000"),
        currency="USD",
        time_claimed_days=21,
        claimant="Acme Construction",
        respondent="Oversight Engineering",
        edition_code="red-book-2017",
        edition_label="FIDIC Red Book 2017 (2nd Edition)",
    )
    values.update(overrides)
    return ClaimFacts(**values)


def plan_for(strand: Strand, plans) :
    return next(p for p in plans if p.strand is strand)


# ---------------------------------------------------------------------------
# Strand order and catalogue integrity
# ---------------------------------------------------------------------------


def test_every_strand_is_in_the_execution_order_once() -> None:
    assert len(STRAND_ORDER) == len(set(STRAND_ORDER)) == len(list(Strand))


def test_deterministic_strands_run_before_model_strands() -> None:
    """A later model failure must still leave the computed results in place."""
    first_model = min(STRAND_ORDER.index(s) for s in MODEL_STRANDS if s is not Strand.NOTICE_COMPLIANCE)
    assert STRAND_ORDER.index(Strand.EVIDENCE_GAPS) < first_model
    assert STRAND_ORDER.index(Strand.NOTICE_COMPLIANCE) < first_model


@pytest.mark.parametrize("strand", sorted(MODEL_STRANDS, key=lambda s: s.value))
def test_every_model_strand_has_a_registered_prompt(strand: Strand) -> None:
    plan = plan_for(strand, plan_analysis(facts(), notice_clause_numbers=["20.2.1"]))
    prompt = get_prompt(plan.prompt_key)
    assert "sources" in prompt.required_variables


@pytest.mark.parametrize(
    "key",
    ["entitlement_analysis", "causation_analysis", "time_impact_analysis",
     "quantum_analysis", "counterarguments_analysis"],
)
def test_analysis_prompts_say_the_claim_is_not_evidence(key: str) -> None:
    """Otherwise the model restates the claim as an uncited fact and grounding fails."""
    prompt = get_prompt(key)
    assert "claim_summary" in prompt.required_variables
    assert "It is not evidence" in prompt.template


def test_evidence_gaps_never_calls_a_model() -> None:
    plan = plan_for(Strand.EVIDENCE_GAPS, plan_analysis(facts()))
    assert plan.uses_model is False
    assert plan.prompt_key is None
    assert plan.retrieval_query == ""


# ---------------------------------------------------------------------------
# Applicability
# ---------------------------------------------------------------------------


def test_every_strand_is_planned_even_when_skipped() -> None:
    """A strand that silently vanishes reads as an analysis that forgot to look."""
    plans = plan_analysis(facts(claim_type="payment", time_claimed_days=None))
    assert [p.strand for p in plans] == list(STRAND_ORDER)
    for plan in plans:
        if not plan.applies:
            assert plan.skip_reason, plan.strand


def test_eot_claim_runs_time_impact() -> None:
    assert plan_for(Strand.TIME_IMPACT, plan_analysis(facts())).applies is True


def test_payment_claim_without_time_skips_time_impact() -> None:
    plan = plan_for(
        Strand.TIME_IMPACT,
        plan_analysis(facts(claim_type="payment", time_claimed_days=None)),
    )
    assert plan.applies is False
    assert "not time-related" in plan.skip_reason


def test_time_claimed_on_any_type_runs_time_impact() -> None:
    plan = plan_for(
        Strand.TIME_IMPACT, plan_analysis(facts(claim_type="other", time_claimed_days=10))
    )
    assert plan.applies is True


def test_eot_without_amount_skips_quantum() -> None:
    plan = plan_for(Strand.QUANTUM, plan_analysis(facts(amount_claimed=None)))
    assert plan.applies is False
    assert "No amount" in plan.skip_reason


def test_cost_claim_runs_quantum_even_without_an_amount() -> None:
    plan = plan_for(
        Strand.QUANTUM, plan_analysis(facts(claim_type="cost", amount_claimed=None))
    )
    assert plan.applies is True


def test_notice_is_skipped_without_registered_requirements() -> None:
    """Another edition's notice periods are never substituted."""
    plan = plan_for(Strand.NOTICE_COMPLIANCE, plan_analysis(facts(), notice_clause_numbers=[]))
    assert plan.applies is False
    assert "deliberately not substituted" in plan.skip_reason


def test_notice_is_skipped_without_a_governing_edition() -> None:
    plan = plan_for(
        Strand.NOTICE_COMPLIANCE,
        plan_analysis(facts(edition_code="", edition_label=""), notice_clause_numbers=["20.2.1"]),
    )
    assert plan.applies is False
    assert "governing conditions" in plan.skip_reason


def test_notice_retrieval_includes_the_notice_clause() -> None:
    plan = plan_for(
        Strand.NOTICE_COMPLIANCE,
        plan_analysis(facts(contractual_basis=()), notice_clause_numbers=["20.2.1"]),
    )
    assert "Clause 20.2.1" in plan.retrieval_query


# ---------------------------------------------------------------------------
# Retrieval queries must keep the knowledge base in scope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("claim_type", sorted(CLAIM_TYPE_LABELS))
@pytest.mark.parametrize("strand", sorted(MODEL_STRANDS, key=lambda s: s.value))
def test_no_strand_query_is_classified_as_evidence_or_chronology(claim_type, strand) -> None:
    """Either intent drops the knowledge base from retrieval entirely.

    An entitlement analysis that never sees the conditions of contract is
    worthless, so this is checked for every strand against every claim type.
    """
    query = retrieval_query_for(strand, facts(claim_type=claim_type), ["20.2.1"])
    analysis = analyse(query)
    assert analysis.intent not in (QueryIntent.EVIDENCE_SEARCH, QueryIntent.CHRONOLOGY), query
    assert analysis.should_search_knowledge_base is True, query


def test_queries_trigger_exact_clause_lookup() -> None:
    query = retrieval_query_for(Strand.ENTITLEMENT, facts())
    assert analyse(query).clause_references == ["8.5", "20.2.1"]


def test_claimant_free_text_is_kept_out_of_retrieval_queries() -> None:
    """One word in a description can reclassify the query and drop the KB."""
    claim = facts(
        title="Supporting records show which documents prove delay",
        description="Find any document supporting this; see the timeline.",
    )
    for strand in MODEL_STRANDS:
        query = retrieval_query_for(strand, claim)
        assert "records" not in query and "timeline" not in query


def test_duplicate_clauses_appear_once() -> None:
    query = retrieval_query_for(Strand.NOTICE_COMPLIANCE, facts(), ["20.2.1", "20.2.1"])
    assert query.count("Clause 20.2.1") == 1


# ---------------------------------------------------------------------------
# Claim summary
# ---------------------------------------------------------------------------


def test_summary_states_missing_values_explicitly() -> None:
    """A model told a value is absent can say so; one not told tends to invent it."""
    summary = build_claim_summary(
        facts(awareness_date=None, notice_date=None, amount_claimed=None,
              claimant="", contractual_basis=())
    )
    assert "Date of awareness: not recorded (commonly disputed)" in summary
    assert "Notice date: not recorded" in summary
    assert "Amount claimed: not recorded" in summary
    assert "Claimant: not recorded" in summary
    assert "Contractual basis relied on: none recorded" in summary


def test_summary_renders_recorded_values() -> None:
    summary = build_claim_summary(facts())
    assert "Amount claimed: USD 125,000.00" in summary
    assert "Time claimed: 21 days" in summary
    assert "Clause 8.5, Clause 20.2.1" in summary
    assert "Type: Extension of Time" in summary
    assert "FIDIC Red Book 2017 (2nd Edition)" in summary


def test_long_description_is_truncated_at_a_word() -> None:
    summary = build_claim_summary(facts(description="word " * 1000))
    line = next(l for l in summary.splitlines() if l.startswith("Description:"))
    assert line.endswith("[...truncated]")
    assert len(line) < 1600


# ---------------------------------------------------------------------------
# Run status
# ---------------------------------------------------------------------------


S = StrandStatus


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], RunStatus.COMPLETED),
        ([S.PENDING, S.PENDING], RunStatus.QUEUED),
        ([S.COMPLETED, S.RUNNING], RunStatus.RUNNING),
        ([S.COMPLETED, S.PENDING], RunStatus.RUNNING),
        ([S.COMPLETED, S.SKIPPED], RunStatus.COMPLETED),
        ([S.SKIPPED, S.SKIPPED], RunStatus.COMPLETED),
        ([S.COMPLETED, S.FAILED], RunStatus.PARTIAL),
        ([S.FAILED, S.SKIPPED], RunStatus.FAILED),
        ([S.FAILED, S.FAILED], RunStatus.FAILED),
    ],
)
def test_run_status(statuses, expected) -> None:
    assert summarise_run(statuses) is expected


def test_partial_run_is_not_reported_as_success() -> None:
    """The completed strands stand; the failures are not hidden."""
    assert summarise_run([S.COMPLETED, S.COMPLETED, S.FAILED]) is RunStatus.PARTIAL


def test_progress_counts_terminal_strands() -> None:
    assert progress_percent([S.COMPLETED, S.SKIPPED, S.FAILED, S.PENDING]) == 75
    assert progress_percent([]) == 100


# ---------------------------------------------------------------------------
# Confidence — computed, and the model can only lower it
# ---------------------------------------------------------------------------


def grounded(**overrides) -> GroundedInputs:
    values = dict(
        insufficient_evidence=False,
        cited_source_count=3,
        fact_findings=2,
        unknown_findings=0,
        model_confidence="high",
        evidence_status="established",
    )
    values.update(overrides)
    return GroundedInputs(**values)


def test_well_supported_answer_is_high() -> None:
    assessment = assess_grounded_answer(grounded())
    assert assessment.level is Confidence.HIGH


def test_insufficient_evidence_is_low() -> None:
    assessment = assess_grounded_answer(grounded(insufficient_evidence=True))
    assert assessment.level is Confidence.LOW
    assert assessment.reasons


def test_nothing_cited_is_low() -> None:
    assert assess_grounded_answer(grounded(cited_source_count=0)).level is Confidence.LOW


def test_single_source_is_capped_at_medium() -> None:
    assessment = assess_grounded_answer(grounded(cited_source_count=1))
    assert assessment.level is Confidence.MEDIUM
    assert any("single source" in r for r in assessment.reasons)


def test_no_fact_findings_is_capped_at_medium() -> None:
    assessment = assess_grounded_answer(grounded(fact_findings=0))
    assert assessment.level is Confidence.MEDIUM


def test_unresolved_parts_cap_at_medium() -> None:
    assert assess_grounded_answer(grounded(unknown_findings=1)).level is Confidence.MEDIUM


@pytest.mark.parametrize(
    ("status", "expected"),
    [("missing", Confidence.LOW), ("partial", Confidence.MEDIUM),
     ("contested", Confidence.MEDIUM), ("established", Confidence.HIGH), (None, Confidence.HIGH)],
)
def test_evidence_record_caps_confidence(status, expected) -> None:
    assert assess_grounded_answer(grounded(evidence_status=status)).level is expected


def test_model_can_lower_confidence() -> None:
    assessment = assess_grounded_answer(grounded(model_confidence="low"))
    assert assessment.level is Confidence.LOW
    assert any("rated its own answer low" in r for r in assessment.reasons)


def test_model_cannot_raise_confidence() -> None:
    """Models are reliably overconfident. Their rating is a ceiling, not a floor."""
    assessment = assess_grounded_answer(grounded(cited_source_count=1, model_confidence="high"))
    assert assessment.level is Confidence.MEDIUM
    assert any("hold it at medium" in r for r in assessment.reasons)


def test_unrecognised_model_rating_is_ignored() -> None:
    assert assess_grounded_answer(grounded(model_confidence="certain")).level is Confidence.HIGH


def test_indeterminate_timing_is_low() -> None:
    assert assess_notice_timing("indeterminate").level is Confidence.LOW


def test_clean_computed_timing_is_high() -> None:
    assessment = assess_notice_timing("compliant")
    assert assessment.level is Confidence.HIGH
    assert "Computed from recorded dates." in assessment.reasons


def test_timing_resting_on_an_assumption_is_medium() -> None:
    assessment = assess_notice_timing(
        "late", assumptions=["No receipt date is recorded; the sent date has been used."]
    )
    assert assessment.level is Confidence.MEDIUM
    assert "assumption" in assessment.reasons[-1]


def test_no_notice_found_is_medium_not_high() -> None:
    """Absence from the record is not proof of absence."""
    assert assess_notice_timing("not_given").level is Confidence.MEDIUM


def test_unknown_timing_status_is_low() -> None:
    assert assess_notice_timing("mystery").level is Confidence.LOW


def test_evidence_record_confidence() -> None:
    assert assess_evidence_record(0, 0).level is Confidence.MEDIUM
    assert assess_evidence_record(4, 1).level is Confidence.MEDIUM
    assert assess_evidence_record(4, 0).level is Confidence.HIGH


def test_combine_takes_the_lowest_and_keeps_every_reason() -> None:
    combined = combine(
        [
            ConfidenceAssessment(Confidence.HIGH, ("a",)),
            ConfidenceAssessment(Confidence.MEDIUM, ("b", "a")),
        ]
    )
    assert combined.level is Confidence.MEDIUM
    assert combined.reasons == ("a", "b")


def test_lowest_rejects_an_empty_input() -> None:
    with pytest.raises(ValueError):
        lowest([])


# ---------------------------------------------------------------------------
# Review — the AI's statement is never overwritten
# ---------------------------------------------------------------------------

ORIGINAL = "Sub-Clause 20.2.1 requires a Notice within 28 days."
T0 = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


def entry(action: ReviewAction, at: datetime = T0, **kwargs) -> ReviewEntry:
    return ReviewEntry(action=action, reviewer_id="u1", reviewed_at=at, **kwargs)


def test_accept_needs_no_reason() -> None:
    assert validate_review("accept") is ReviewAction.ACCEPT


def test_reject_needs_a_real_reason() -> None:
    with pytest.raises(ValidationError) as exc:
        validate_review("reject", reason="no")
    assert exc.value.details["field"] == "reason"
    assert validate_review("reject", reason="x" * MIN_REASON_CHARS) is ReviewAction.REJECT


def test_amend_needs_a_statement_and_a_reason() -> None:
    with pytest.raises(ValidationError) as exc:
        validate_review("amend", reason="Period is 28 days, not 42.")
    assert exc.value.details["field"] == "amended_statement"


def test_amendment_identical_to_the_original_is_refused() -> None:
    """That is an acceptance recorded under the wrong name."""
    with pytest.raises(ValidationError):
        validate_review(
            "amend",
            reason="Tidying the wording only.",
            amended_statement="  Sub-Clause 20.2.1 requires a Notice   within 28 days. ",
            original_statement=ORIGINAL,
        )


def test_unknown_action_is_refused() -> None:
    with pytest.raises(ValidationError) as exc:
        validate_review("overrule")
    assert "accept" in exc.value.details["valid"]


def test_unreviewed_finding() -> None:
    outcome = resolve_review(ORIGINAL, [])
    assert outcome.state is ReviewState.UNREVIEWED
    assert outcome.is_reviewed is False
    assert outcome.effective_statement == ORIGINAL


def test_amendment_changes_the_effective_statement_not_the_original() -> None:
    outcome = resolve_review(
        ORIGINAL,
        [entry(ReviewAction.AMEND, reason="Clarify recipient.",
               amended_statement="A Notice must be given to the Engineer within 28 days.")],
    )
    assert outcome.state is ReviewState.AMENDED
    assert outcome.effective_statement.startswith("A Notice must be given to the Engineer")
    assert outcome.original_statement == ORIGINAL


def test_rejected_finding_keeps_its_original_text() -> None:
    """So it can be shown as rejected rather than disappearing."""
    outcome = resolve_review(ORIGINAL, [entry(ReviewAction.REJECT, reason="Wrong edition cited.")])
    assert outcome.state is ReviewState.REJECTED
    assert outcome.effective_statement == ORIGINAL


def test_latest_review_wins_and_history_is_kept() -> None:
    outcome = resolve_review(
        ORIGINAL,
        [
            entry(ReviewAction.REJECT, at=T1, reason="Reconsidered after reading."),
            entry(ReviewAction.ACCEPT, at=T0),
        ],
    )
    assert outcome.state is ReviewState.REJECTED
    assert outcome.history_length == 2


def test_same_timestamp_resolves_to_the_later_entry() -> None:
    outcome = resolve_review(
        ORIGINAL, [entry(ReviewAction.ACCEPT), entry(ReviewAction.REJECT, reason="Changed my mind here.")]
    )
    assert outcome.state is ReviewState.REJECTED


# ---------------------------------------------------------------------------
# Evidence tagged by issue category reaches the right element
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("category", "element"),
    [("notice_compliance", "notice"), ("entitlement", "responsibility"),
     ("causation", "causation"), ("quantum", "quantum"), (None, None), ("", None)],
)
def test_issue_category_maps_to_element(category, element) -> None:
    assert element_code_for_issue_category(category) == element


def test_notice_evidence_filed_under_its_issue_closes_the_notice_gap() -> None:
    """Regression: 'notice_compliance' evidence used to match no element at all."""
    item = EvidenceItem(
        evidence_id="e1",
        title="Notice of Claim L-042",
        element_code=element_code_for_issue_category("notice_compliance"),
        relevance=Relevance.SUPPORTS,
        is_reviewed=True,
    )
    report = analyse_gaps("eot", [item])
    notice = next(a for a in report.assessments if a.element.code == "notice")
    assert notice.status is ElementStatus.ESTABLISHED
    assert report.unmatched_evidence == []
