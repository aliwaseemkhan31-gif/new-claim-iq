"""The two obligations the standard forms impose, and the gap between them.

A Notice of Claim and a fully detailed Claim are different things. FIDIC 2017
requires the first within 28 days of awareness (Sub-Clause 20.2.1) and the
second within 84 (Sub-Clause 20.2.4); the 1999 forms require 28 and 42 at
Sub-Clause 20.1. Screening used to run every notice check over both, so a
missing fully detailed Claim was reported as a notice failure — which sends a
reader to the wrong part of the file, and is the error the client's own
hierarchy warns about under "NOTICE != CLAIM".

The second obligation also fails differently. It is not a condition precedent,
so it never bars a claim. But FIDIC 2017 provides that where the statement of
contractual basis is not submitted within the period, the Notice of Claim is
deemed to have lapsed — a distinct mechanism, and a reversible one, so it is
reported as LAPSED rather than BARRED.
"""
from __future__ import annotations

from datetime import date

from claimiq.claims.domain.notice_compliance import (
    ComplianceStatus,
    NoticeRequirement,
    Obligation,
)
from claimiq.claims.domain.screening import (
    CHECKS,
    OUTCOME_PRECEDENCE,
    Area,
    CheckStatus,
    Outcome,
    Stage,
    screen_claim,
    stage_of,
)

from tests.domain.test_screening import (
    CONDITION_PRECEDENT,
    REQUIREMENT,
    complete_input,
    finding,
    notice,
)

DETAILED_2017 = NoticeRequirement(
    clause_number="20.2.4",
    edition="red-book-2017",
    period_days=84,
    is_condition_precedent=False,
    recipient="the Engineer",
    description="fully detailed Claim",
    obligation=Obligation.DETAILED_CLAIM,
    lapse_consequence=(
        "Failure to submit the statement of contractual basis within the "
        "period makes the Notice of Claim lapse."
    ),
)

DETAILED_1999 = NoticeRequirement(
    clause_number="20.1",
    edition="red-book-1999",
    period_days=42,
    is_condition_precedent=False,
    recipient="the Engineer",
    description="fully detailed claim",
    obligation=Obligation.DETAILED_CLAIM,
)


def detailed_finding(**kwargs):
    """A detailed-Claim finding: none on record, period still running."""
    defaults = dict(
        requirement=DETAILED_2017,
        status=ComplianceStatus.NOT_GIVEN,
        notice=None,
        deadline=date(2026, 11, 4),
        days_used=None,
    )
    defaults.update(kwargs)
    return finding(**defaults)


def detailed_input(**kwargs):
    """A 2017 claim carrying both obligations."""
    defaults = dict(
        edition_code="red-book-2017",
        edition_label="FIDIC Red Book 2017 (2nd Edition)",
        contractual_basis=("20.2.1",),
        clauses_found=("20.2.1",),
        notice_findings=(
            finding(requirement=CONDITION_PRECEDENT),
            detailed_finding(),
        ),
    )
    defaults.update(kwargs)
    return complete_input(**defaults)


def both(detailed):
    """The pair of findings, with the detailed one overridden."""
    return (finding(requirement=CONDITION_PRECEDENT), detailed)


# ---------------------------------------------------------------------------
# The split
# ---------------------------------------------------------------------------


def test_the_input_separates_the_two_obligations() -> None:
    data = detailed_input()
    assert [f.requirement.clause_number for f in data.notice_of_claim_findings] == [
        "20.2.1"
    ]
    assert [f.requirement.clause_number for f in data.detailed_claim_findings] == [
        "20.2.4"
    ]


def test_an_unlabelled_requirement_counts_as_a_notice_of_claim() -> None:
    """The safe default for a project-specific requirement."""
    assert REQUIREMENT.obligation is Obligation.NOTICE_OF_CLAIM
    assert complete_input().detailed_claim_findings == ()


def test_a_missing_detailed_claim_is_not_reported_as_a_missing_notice() -> None:
    """The split, asserted at the point it used to go wrong."""
    report = screen_claim(detailed_input())
    assert report.by_code("NC3").status is CheckStatus.SATISFIED
    assert report.by_code("DC1").status is CheckStatus.INCOMPLETE


def test_the_notice_checks_ignore_the_detailed_claim_entirely() -> None:
    """NC5, NC7 and NC8 would all have fired on the detailed-claim finding."""
    data = detailed_input(
        notice_findings=both(
            detailed_finding(
                status=ComplianceStatus.LATE,
                notice=notice(received_date=None, is_confirmed_notice=False),
                days_late=11,
            )
        )
    )
    report = screen_claim(data)
    for code in ("NC5", "NC7", "NC8"):
        assert report.by_code(code).status is CheckStatus.SATISFIED, code


def test_the_detailed_claim_checks_sit_in_the_claim_stage() -> None:
    for code in ("DC1", "DC2", "DC3"):
        check = next(c for c in CHECKS if c.code == code)
        assert check.area is Area.SUBMISSION
        assert stage_of(check.area) is Stage.CLAIM


# ---------------------------------------------------------------------------
# DC1 / DC2 — the submission and its period
# ---------------------------------------------------------------------------


def test_a_detailed_claim_not_yet_due_is_distinguished_from_a_late_one() -> None:
    """On day one of eighty-four, "not on record" is a deadline, not a failure."""
    result = screen_claim(detailed_input()).by_code("DC1")
    assert result.status is CheckStatus.INCOMPLETE
    assert "has not expired" in result.detail
    assert result.items == ("Clause 20.2.4 — due 2026-11-04",)


def test_an_overdue_detailed_claim_says_the_period_has_expired() -> None:
    data = detailed_input(notice_findings=both(detailed_finding(deadline=date(2026, 9, 1))))
    result = screen_claim(data).by_code("DC1")
    assert result.status is CheckStatus.INCOMPLETE
    assert "the period has expired" in result.detail
    assert result.items == ("Clause 20.2.4 — was due 2026-09-01",)


def test_dc1_keeps_the_warning_that_absence_is_not_proof() -> None:
    data = detailed_input(notice_findings=both(detailed_finding(deadline=date(2026, 9, 1))))
    assert "not proof" in screen_claim(data).by_code("DC1").detail


def test_a_detailed_claim_on_record_satisfies_dc1() -> None:
    data = detailed_input(
        notice_findings=both(
            detailed_finding(status=ComplianceStatus.COMPLIANT, notice=notice())
        )
    )
    assert screen_claim(data).by_code("DC1").status is CheckStatus.SATISFIED


def test_dc_checks_do_not_apply_where_no_detailed_claim_is_required() -> None:
    """The 1987 form registers no second submission."""
    report = screen_claim(complete_input())
    for code in ("DC1", "DC2", "DC3"):
        assert report.by_code(code).status is CheckStatus.NOT_APPLICABLE, code


def test_a_late_detailed_claim_is_reported_but_never_barred() -> None:
    data = detailed_input(
        notice_findings=both(
            detailed_finding(
                status=ComplianceStatus.LATE, notice=notice(), days_late=11
            )
        )
    )
    result = screen_claim(data).by_code("DC2")
    assert result.status is CheckStatus.INCOMPLETE
    assert result.items == ("Clause 20.2.4 — 11 day(s) late",)
    assert "does not bar the claim" in result.detail


def test_dc2_is_satisfied_where_the_claim_was_in_time() -> None:
    data = detailed_input(
        notice_findings=both(
            detailed_finding(status=ComplianceStatus.COMPLIANT, notice=notice())
        )
    )
    assert screen_claim(data).by_code("DC2").status is CheckStatus.SATISFIED


# ---------------------------------------------------------------------------
# DC3 — the Sub-Clause 20.2.4 lapse
# ---------------------------------------------------------------------------


def test_the_lapse_does_not_apply_to_a_form_that_states_no_consequence() -> None:
    """1999 has a 42-day detailed claim but no lapse provision."""
    data = detailed_input(
        edition_code="red-book-1999",
        edition_label="FIDIC Red Book 1999 (1st Edition)",
        notice_findings=both(
            detailed_finding(requirement=DETAILED_1999, deadline=date(2026, 8, 1))
        ),
    )
    assert screen_claim(data).by_code("DC3").status is CheckStatus.NOT_APPLICABLE


def test_no_lapse_while_the_period_is_still_running() -> None:
    result = screen_claim(detailed_input()).by_code("DC3")
    assert result.status is CheckStatus.SATISFIED
    assert "2026-11-04" in result.detail


def test_an_expired_period_with_no_detailed_claim_lapses_the_notice() -> None:
    data = detailed_input(notice_findings=both(detailed_finding(deadline=date(2026, 9, 1))))
    result = screen_claim(data).by_code("DC3")
    assert result.status is CheckStatus.LAPSED
    assert "no longer a valid Notice" in result.detail
    # The reversal matters as much as the lapse.
    assert "14 days" in result.detail
    assert "deemed" in result.detail
    assert "not a determination" in result.detail
    assert result.items == ("20.2.4",)


def test_a_lapse_is_never_reported_as_a_time_bar() -> None:
    """Different mechanism, and the reversible one. BARRED is reserved."""
    data = detailed_input(notice_findings=both(detailed_finding(deadline=date(2026, 9, 1))))
    report = screen_claim(data)
    assert report.lapsed
    assert not report.barred
    assert report.outcome is Outcome.LAPSED
    assert "lapsed" in report.summary()
    assert "time bar" not in report.summary()


def test_the_lapse_summary_names_the_clause() -> None:
    data = detailed_input(notice_findings=both(detailed_finding(deadline=date(2026, 9, 1))))
    assert "20.2.4" in screen_claim(data).summary()


def test_a_time_bar_outranks_a_lapse() -> None:
    """Both adverse; the bar is the one that is not reversible."""
    data = detailed_input(
        notice_findings=(
            finding(
                requirement=CONDITION_PRECEDENT,
                status=ComplianceStatus.LATE,
                days_late=9,
            ),
            detailed_finding(deadline=date(2026, 9, 1)),
        ),
    )
    report = screen_claim(data)
    assert report.barred and report.lapsed
    assert report.outcome is Outcome.BARRED
    assert OUTCOME_PRECEDENCE.index(Outcome.BARRED) < OUTCOME_PRECEDENCE.index(
        Outcome.LAPSED
    )


def test_both_adverse_outcomes_are_lifted_out_together() -> None:
    data = detailed_input(
        notice_findings=(
            finding(
                requirement=CONDITION_PRECEDENT,
                status=ComplianceStatus.LATE,
                days_late=9,
            ),
            detailed_finding(deadline=date(2026, 9, 1)),
        ),
    )
    report = screen_claim(data)
    assert set(report.adverse) == set(report.barred + report.lapsed)
    assert len(report.adverse) == 2


def test_a_submitted_claim_with_a_recorded_basis_does_not_lapse() -> None:
    data = detailed_input(
        notice_findings=both(
            detailed_finding(
                status=ComplianceStatus.COMPLIANT,
                notice=notice(),
                deadline=date(2026, 9, 1),
            )
        )
    )
    assert screen_claim(data).by_code("DC3").status is CheckStatus.SATISFIED


def test_a_submitted_claim_without_a_recorded_basis_is_not_claimed_either_way() -> None:
    """Whether the submission contained the (b) statement is not in the record."""
    data = detailed_input(
        contractual_basis=(),
        clauses_found=(),
        notice_findings=both(
            detailed_finding(
                status=ComplianceStatus.COMPLIANT,
                notice=notice(),
                deadline=date(2026, 9, 1),
            )
        ),
    )
    result = screen_claim(data).by_code("DC3")
    assert result.status is CheckStatus.INCOMPLETE
    assert "cannot be read from the record" in result.detail


def test_the_lapse_is_indeterminate_without_an_awareness_date() -> None:
    """Every period runs from it; nothing here is computable until it is set."""
    report = screen_claim(detailed_input(awareness_date=None))
    assert report.by_code("DC3").status is CheckStatus.INDETERMINATE
    assert not report.lapsed


def test_only_dc3_may_return_lapsed() -> None:
    """Like BARRED, the status is reserved to the check that computes it."""
    data = detailed_input(notice_findings=both(detailed_finding(deadline=date(2026, 9, 1))))
    report = screen_claim(data)
    assert {r.check.code for r in report.lapsed} == {"DC3"}


def test_a_lapse_blocks_assessment() -> None:
    """DC3 is blocking: there is no point assessing a claim whose notice may
    have gone."""
    check = next(c for c in CHECKS if c.code == "DC3")
    assert check.weight.value == "blocking"
