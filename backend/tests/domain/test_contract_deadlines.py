"""Deadlines as a claim actually faces them.

Three things the notice check got wrong, or could not do, before:

- The 1987 form was assessed against Clause 53 alone, so an extension of time
  claim was checked against the money procedure and never against Clause 44.
- Periods that run from the notice (1987 Sub-Clauses 44.2(b) and 53.3) were
  run from the awareness date instead.
- A project's own Particular Conditions could not change a period.
"""
from __future__ import annotations

from datetime import date

from claimiq.analysis.domain import strands
from claimiq.claims.domain.contract_terms import (
    Passage,
    is_particular_conditions_page,
    periods_in,
    suggest_amendments,
)
from claimiq.claims.domain.notice_compliance import (
    FIDIC_1987_ACCOUNT,
    FIDIC_1987_EOT_NOTICE,
    FIDIC_1987_EOT_PARTICULARS,
    FIDIC_1987_NOTICE_OF_CLAIM,
    MONEY_CLAIM_TYPES,
    TIME_CLAIM_TYPES,
    Amendment,
    AmendmentAction,
    ComplianceStatus,
    DayCount,
    NoticeEvent,
    Obligation,
    applicable_requirements,
    apply_amendments,
    assess_notice,
    assess_requirements,
    requirements_for_edition,
)

EVENT = date(2026, 3, 1)


def _event(doc: str, clause: str, sent: date, obligation: Obligation | None) -> NoticeEvent:
    return NoticeEvent(
        document_id=doc,
        document_title=doc,
        sent_date=sent,
        received_date=sent,
        recipient="the Engineer",
        is_confirmed_notice=True,
        clause_number=clause,
        obligation=obligation,
    )


# ---------------------------------------------------------------------------
# Which procedure governs
# ---------------------------------------------------------------------------


def test_claim_type_sets_match_the_analysis_engine() -> None:
    assert TIME_CLAIM_TYPES == strands.TIME_CLAIM_TYPES
    assert MONEY_CLAIM_TYPES == strands.MONEY_CLAIM_TYPES


def test_an_extension_of_time_claim_under_1987_is_checked_against_clause_44() -> None:
    applied = applicable_requirements(requirements_for_edition("red-book-1987"), claim_type="eot")
    assert applied == (FIDIC_1987_EOT_NOTICE, FIDIC_1987_EOT_PARTICULARS)


def test_a_cost_claim_under_1987_is_checked_against_clause_53() -> None:
    applied = applicable_requirements(requirements_for_edition("red-book-1987"), claim_type="cost")
    assert applied == (FIDIC_1987_NOTICE_OF_CLAIM, FIDIC_1987_ACCOUNT)


def test_an_eot_claim_that_also_claims_money_faces_both_procedures() -> None:
    applied = applicable_requirements(
        requirements_for_edition("red-book-1987"), claim_type="eot", seeks_money=True
    )
    assert {r.clause_number for r in applied} == {"44.2", "53.1", "53.3"}


def test_a_claim_with_no_stated_relief_is_shown_every_deadline() -> None:
    every = requirements_for_edition("red-book-1987")
    assert applicable_requirements(every, claim_type="other") == every


def test_later_suites_apply_to_every_claim() -> None:
    every = requirements_for_edition("red-book-2017")
    assert applicable_requirements(every, claim_type="eot") == every


# ---------------------------------------------------------------------------
# Periods that run from the notice
# ---------------------------------------------------------------------------


def test_particulars_run_from_the_notice_not_the_event() -> None:
    notice = _event("notice", "44.2", date(2026, 3, 20), Obligation.NOTICE_OF_CLAIM)
    particulars = _event("particulars", "44.2", date(2026, 4, 15), Obligation.DETAILED_CLAIM)

    notice_finding, particulars_finding = assess_requirements(
        (FIDIC_1987_EOT_NOTICE, FIDIC_1987_EOT_PARTICULARS),
        awareness_date=EVENT,
        notices=[notice, particulars],
    )

    assert notice_finding.status is ComplianceStatus.COMPLIANT
    # 28 days from 20 March, not from 1 March: 17 April. 15 April is in time,
    # where an event-based period would have expired on 29 March.
    assert particulars_finding.deadline == date(2026, 4, 17)
    assert particulars_finding.status is ComplianceStatus.COMPLIANT
    assert any("runs from the notice" in a for a in particulars_finding.assumptions)


def test_particulars_have_no_deadline_until_the_notice_is_recorded() -> None:
    particulars = _event("particulars", "44.2", date(2026, 4, 15), Obligation.DETAILED_CLAIM)
    _, finding = assess_requirements(
        (FIDIC_1987_EOT_NOTICE, FIDIC_1987_EOT_PARTICULARS),
        awareness_date=EVENT,
        notices=[particulars],
    )
    assert finding.status is ComplianceStatus.INDETERMINATE
    assert finding.deadline is None


def test_the_notice_does_not_count_as_its_own_particulars() -> None:
    """One letter recorded without saying which it is cannot be both."""
    untagged = _event("letter", "44.2", date(2026, 3, 20), None)
    _, finding = assess_requirements(
        (FIDIC_1987_EOT_NOTICE, FIDIC_1987_EOT_PARTICULARS),
        awareness_date=EVENT,
        notices=[untagged],
    )
    assert finding.status is ComplianceStatus.NOT_GIVEN


def test_a_notice_tagged_as_the_notice_is_not_the_detailed_claim() -> None:
    detailed = next(
        r for r in requirements_for_edition("red-book-1999") if r.obligation is Obligation.DETAILED_CLAIM
    )
    notice = _event("notice", "20.1", date(2026, 3, 10), Obligation.NOTICE_OF_CLAIM)
    finding = assess_notice(detailed, awareness_date=EVENT, notices=[notice])
    assert finding.status is ComplianceStatus.NOT_GIVEN


def test_late_1987_notice_states_the_contracts_own_consequence() -> None:
    late = _event("notice", "53.1", date(2026, 4, 20), Obligation.NOTICE_OF_CLAIM)
    finding = assess_notice(FIDIC_1987_NOTICE_OF_CLAIM, awareness_date=EVENT, notices=[late])
    assert finding.status is ComplianceStatus.LATE
    assert not finding.is_time_barred
    assert any("53.4" in w for w in finding.warnings)


def test_unknown_awareness_still_reports_what_was_given() -> None:
    notice = _event("notice", "53.1", date(2026, 3, 10), Obligation.NOTICE_OF_CLAIM)
    finding = assess_notice(FIDIC_1987_NOTICE_OF_CLAIM, awareness_date=None, notices=[notice])
    assert finding.status is ComplianceStatus.INDETERMINATE
    assert finding.notice == notice


# ---------------------------------------------------------------------------
# The project's own contract
# ---------------------------------------------------------------------------


def test_an_amendment_replaces_the_standard_period_and_says_where_from() -> None:
    standard = requirements_for_edition("red-book-1987")
    amended = apply_amendments(
        standard,
        [
            Amendment(
                clause_number="53.1",
                obligation=Obligation.NOTICE_OF_CLAIM,
                period_days=14,
                source="Particular Conditions, p. 12",
            )
        ],
        edition="red-book-1987",
    )
    notice = next(r for r in amended if r.clause_number == "53.1")
    assert notice.period_days == 14
    assert notice.source == "Particular Conditions, p. 12"
    assert notice.is_amended
    # Everything it did not change is kept.
    assert notice.recipient == FIDIC_1987_NOTICE_OF_CLAIM.recipient
    assert len(amended) == len(standard)


def test_a_removal_drops_the_standard_requirement() -> None:
    amended = apply_amendments(
        requirements_for_edition("red-book-1987"),
        [Amendment("53.3", Obligation.DETAILED_CLAIM, action=AmendmentAction.REMOVE)],
        edition="red-book-1987",
    )
    assert "53.3" not in {r.clause_number for r in amended}


def test_an_addition_without_a_period_is_not_invented() -> None:
    standard = requirements_for_edition("red-book-1987")
    amended = apply_amendments(
        standard,
        [Amendment("99.1", Obligation.NOTICE_OF_CLAIM, action=AmendmentAction.ADD)],
        edition="red-book-1987",
    )
    assert amended == standard


# ---------------------------------------------------------------------------
# Reading the Particular Conditions
# ---------------------------------------------------------------------------


def _passage(text: str, *, clause: str = "", doc_type: str = "particular_conditions") -> Passage:
    return Passage(
        document_id="pc",
        document_title="Particular Conditions",
        document_type=doc_type,
        text=text,
        clause_number=clause,
        page=12,
    )


def test_periods_are_read_in_digits_and_words() -> None:
    days = [p.days for p in periods_in("within fourteen (14) days, then 21 (twenty-one) days")]
    assert days == [14, 21]


def test_working_days_are_recognised() -> None:
    (period,) = periods_in("notice within 10 working days of the event")
    assert period.day_count is DayCount.WORKING


def test_a_restated_standard_form_proposes_nothing() -> None:
    """Most contract sets reprint the general conditions in full."""
    text = (
        "53.1 ... he shall give notice of his intention to the Engineer, with a "
        "copy to the Employer, within 28 days after the event giving rise to the "
        "claim has first arisen."
    )
    passages = [_passage(text, clause="53.1", doc_type="conditions_of_contract")]
    assert suggest_amendments(requirements_for_edition("red-book-1987"), passages) == []


def test_a_substituted_period_is_proposed_against_the_right_requirement() -> None:
    text = 'Sub-Clause 53.1: delete "28 days" and substitute "14 days" for the notice of claim.'
    (suggestion,) = suggest_amendments(requirements_for_edition("red-book-1987"), [_passage(text)])
    assert suggestion.clause_number == "53.1"
    assert suggestion.obligation is Obligation.NOTICE_OF_CLAIM
    assert suggestion.period_days == 14
    assert suggestion.standard_period_days == 28
    assert suggestion.page == 12
    assert "14 days" in suggestion.excerpt


def test_the_1999_detailed_claim_period_is_matched_by_the_period_it_replaces() -> None:
    text = (
        "Sub-Clause 20.1 Contractor's Claims: in the fourth paragraph delete "
        "42 days and substitute 60 days for the fully detailed claim."
    )
    (suggestion,) = suggest_amendments(requirements_for_edition("red-book-1999"), [_passage(text)])
    assert suggestion.obligation is Obligation.DETAILED_CLAIM
    assert suggestion.period_days == 60


def test_a_passage_about_something_else_is_not_read() -> None:
    """A specification item numbered 20.1 is not Sub-Clause 20.1."""
    text = "20.1 The Contractor shall hire houses for the Engineer's staff within thirty (30) days."
    passages = [_passage(text, clause="20.1", doc_type="conditions_of_contract")]
    assert suggest_amendments(requirements_for_edition("red-book-1999"), passages) == []


def test_a_general_conditions_mention_is_not_an_amendment() -> None:
    text = "52.2 ... notice of a claim under Sub-Clause 53.1 within 14 days ..."
    passages = [_passage(text, clause="52.2", doc_type="conditions_of_contract")]
    assert suggest_amendments(requirements_for_edition("red-book-1987"), passages) == []


def test_a_deleted_consequence_clause_is_proposed_against_both_requirements() -> None:
    """Observed on a real 1987 contract: Part II deletes Sub-Clause 53.4."""
    text = (
        "COPA - Part-II: Conditions of Particular Application ... the Contract "
        "price. 53.4 Failure to Comply This Sub-Clause is deleted in its "
        "entirety. 54.5 Conditions of Hire of Contractor's Equipment ..."
    )
    passage = Passage(
        document_id="contract",
        document_title="Contract document",
        document_type="conditions_of_contract",
        text=text,
        page=93,
        amending=is_particular_conditions_page(text),
    )
    found = suggest_amendments(requirements_for_edition("red-book-1987"), [passage])
    assert {(s.clause_number, s.period_days) for s in found} == {("53.1", None), ("53.3", None)}
    assert all("53.4 is deleted" in (s.late_consequence or "") for s in found)


def test_a_consequence_deleted_outside_the_particular_conditions_is_ignored() -> None:
    text = "53.4 Failure to Comply. If ... (whether or not deleted from the records) ..."
    passage = Passage("gc", "General Conditions", "conditions_of_contract", text, page=24)
    assert suggest_amendments(requirements_for_edition("red-book-1987"), [passage]) == []


def test_a_confirmed_deletion_replaces_the_stated_consequence() -> None:
    amended = apply_amendments(
        requirements_for_edition("red-book-1987"),
        [
            Amendment(
                "53.1",
                Obligation.NOTICE_OF_CLAIM,
                late_consequence="Sub-Clause 53.4 is deleted by this contract.",
            )
        ],
        edition="red-book-1987",
    )
    notice = next(r for r in amended if r.clause_number == "53.1")
    assert notice.period_days == 28
    assert notice.late_consequence == "Sub-Clause 53.4 is deleted by this contract."
