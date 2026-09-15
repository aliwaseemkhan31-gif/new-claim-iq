"""Tests for notice-compliance analysis.

Notice timing decides many construction claims outright, so the arithmetic must
be right and the uncertainty must be visible.
"""
from __future__ import annotations

from datetime import date

import pytest

from claimiq.claims.domain.notice_compliance import (
    FIDIC_1987_NOTICE_OF_CLAIM,
    FIDIC_2017_NOTICE_OF_CLAIM,
    ComplianceStatus,
    DayCount,
    NoticeEvent,
    NoticeRequirement,
    add_days,
    assess_notice,
    count_days,
    given_under,
    recipient_label,
    requirements_for_edition,
)

AWARE = date(2026, 3, 1)


def notice(
    sent: date | None,
    *,
    received: date | None = None,
    confirmed: bool = True,
    recipient: str = "the Engineer",
    title: str = "Notice of Claim.pdf",
) -> NoticeEvent:
    return NoticeEvent(
        document_id="doc-1",
        document_title=title,
        sent_date=sent,
        received_date=received,
        recipient=recipient,
        is_confirmed_notice=confirmed,
    )


# ---------------------------------------------------------------------------
# Which provision a notice is given under
# ---------------------------------------------------------------------------


def _under(clause: str, sent: date, title: str = "Notice of Claim.pdf") -> NoticeEvent:
    return NoticeEvent(
        document_id=f"doc-{clause}",
        document_title=title,
        sent_date=sent,
        received_date=sent,
        recipient="the Engineer",
        is_confirmed_notice=True,
        clause_number=clause,
    )


def test_notice_under_another_provision_does_not_count() -> None:
    """Regression: a Notice of Claim was accepted as a fully detailed claim."""
    detailed = next(r for r in requirements_for_edition("red-book-2017") if r.clause_number == "20.2.4")
    finding = assess_notice(detailed, awareness_date=AWARE, notices=[_under("20.2.1", date(2026, 3, 18))])

    assert finding.status is ComplianceStatus.NOT_GIVEN
    assert finding.notice is None
    assert any("other provisions" in w for w in finding.warnings)


def test_notice_under_its_own_provision_counts() -> None:
    finding = assess_notice(
        FIDIC_2017_NOTICE_OF_CLAIM,
        awareness_date=AWARE,
        notices=[_under("20.2.4", date(2026, 3, 10)), _under("20.2.1", date(2026, 3, 18))],
    )
    assert finding.status is ComplianceStatus.COMPLIANT
    assert finding.notice.clause_number == "20.2.1", "the earlier 20.2.4 document is not used"
    assert finding.days_used == 17


def test_parent_clause_is_not_a_prefix_match() -> None:
    assert not given_under(_under("20.2", date(2026, 3, 18)), FIDIC_2017_NOTICE_OF_CLAIM)
    assert not given_under(_under("20.2.10", date(2026, 3, 18)), FIDIC_2017_NOTICE_OF_CLAIM)
    assert given_under(_under(" 20.2.1 ", date(2026, 3, 18)), FIDIC_2017_NOTICE_OF_CLAIM)


def test_unattributed_notice_is_assessed_with_a_warning() -> None:
    finding = assess_notice(
        FIDIC_2017_NOTICE_OF_CLAIM, awareness_date=AWARE, notices=[_under("", date(2026, 3, 18))]
    )
    assert finding.status is ComplianceStatus.COMPLIANT
    assert any("not recorded as given under any provision" in w for w in finding.warnings)


@pytest.mark.parametrize(
    ("name", "role", "expected"),
    [
        ("Oversight Engineering", "engineer", "Oversight Engineering (the Engineer)"),
        ("Supplier Ltd", "supplier", "Supplier Ltd"),
        ("", "engineer", "the Engineer"),
        ("", None, ""),
    ],
)
def test_recipient_label(name: str, role: str | None, expected: str) -> None:
    assert recipient_label(name, role) == expected


def test_role_label_satisfies_recipient_check() -> None:
    event = NoticeEvent(
        document_id="d", document_title="Notice", sent_date=date(2026, 3, 18),
        received_date=date(2026, 3, 18), is_confirmed_notice=True, clause_number="20.2.1",
        recipient=recipient_label("Oversight Engineering", "engineer"),
    )
    finding = assess_notice(FIDIC_2017_NOTICE_OF_CLAIM, awareness_date=AWARE, notices=[event])
    assert not any("recipient" in w for w in finding.warnings)


# ---------------------------------------------------------------------------
# Date arithmetic
# ---------------------------------------------------------------------------


def test_period_runs_from_the_day_after_the_trigger() -> None:
    """A 28-day period from 1 March expires on 29 March, not 28 March.

    The trigger day is not counted. Getting this wrong shifts every deadline.
    """
    assert add_days(date(2026, 3, 1), 28) == date(2026, 3, 29)


def test_period_crosses_month_and_year_boundaries() -> None:
    assert add_days(date(2026, 12, 20), 28) == date(2027, 1, 17)


def test_leap_year_is_handled() -> None:
    assert add_days(date(2028, 2, 1), 28) == date(2028, 2, 29)


def test_working_days_skip_weekends() -> None:
    # Monday 2 March 2026 + 5 working days -> Monday 9 March
    assert add_days(date(2026, 3, 2), 5, DayCount.WORKING) == date(2026, 3, 9)


def test_working_days_skip_holidays() -> None:
    holidays = [date(2026, 3, 4)]
    assert add_days(date(2026, 3, 2), 5, DayCount.WORKING, holidays=holidays) == date(
        2026, 3, 10
    )


def test_zero_day_period_expires_on_the_trigger_date() -> None:
    assert add_days(AWARE, 0) == AWARE


def test_negative_period_is_rejected() -> None:
    with pytest.raises(ValueError):
        add_days(AWARE, -1)


def test_count_days_excludes_the_start_date() -> None:
    assert count_days(date(2026, 3, 1), date(2026, 3, 29)) == 28


def test_count_days_is_negative_when_reversed() -> None:
    """A notice dated before its event is a real anomaly, not an error."""
    assert count_days(date(2026, 3, 29), date(2026, 3, 1)) == -28


def test_count_working_days_excludes_weekends() -> None:
    assert count_days(date(2026, 3, 2), date(2026, 3, 9), DayCount.WORKING) == 5


# ---------------------------------------------------------------------------
# Compliance outcomes
# ---------------------------------------------------------------------------


def test_notice_within_the_period_is_compliant() -> None:
    finding = assess_notice(
        FIDIC_2017_NOTICE_OF_CLAIM, awareness_date=AWARE, notices=[notice(date(2026, 3, 20))]
    )
    assert finding.status is ComplianceStatus.COMPLIANT
    assert finding.days_used == 19
    assert finding.deadline == date(2026, 3, 29)
    assert finding.is_time_barred is False


def test_notice_on_the_final_day_is_compliant() -> None:
    """The boundary case that decides real disputes."""
    finding = assess_notice(
        FIDIC_2017_NOTICE_OF_CLAIM, awareness_date=AWARE, notices=[notice(date(2026, 3, 29))]
    )
    assert finding.status is ComplianceStatus.COMPLIANT
    assert finding.days_used == 28


def test_notice_one_day_after_the_deadline_is_late() -> None:
    finding = assess_notice(
        FIDIC_2017_NOTICE_OF_CLAIM, awareness_date=AWARE, notices=[notice(date(2026, 3, 30))]
    )
    assert finding.status is ComplianceStatus.LATE
    assert finding.days_late == 1


def test_late_notice_under_a_condition_precedent_is_time_barred() -> None:
    finding = assess_notice(
        FIDIC_2017_NOTICE_OF_CLAIM, awareness_date=AWARE, notices=[notice(date(2026, 4, 15))]
    )
    assert finding.status is ComplianceStatus.LATE
    assert finding.is_time_barred is True
    assert any("condition precedent" in w for w in finding.warnings)
    assert any("governing law" in w for w in finding.warnings)


def test_late_notice_without_a_condition_precedent_is_not_time_barred() -> None:
    """The 1987 form differs from 2017 in exactly this respect."""
    finding = assess_notice(
        FIDIC_1987_NOTICE_OF_CLAIM, awareness_date=AWARE, notices=[notice(date(2026, 4, 15))]
    )
    assert finding.status is ComplianceStatus.LATE
    assert finding.is_time_barred is False


def test_no_notice_is_reported_as_not_given_with_a_caveat() -> None:
    finding = assess_notice(FIDIC_2017_NOTICE_OF_CLAIM, awareness_date=AWARE, notices=[])
    assert finding.status is ComplianceStatus.NOT_GIVEN
    assert any("not proof" in w for w in finding.warnings)


def test_undated_notices_are_ignored() -> None:
    finding = assess_notice(
        FIDIC_2017_NOTICE_OF_CLAIM, awareness_date=AWARE, notices=[notice(None)]
    )
    assert finding.status is ComplianceStatus.NOT_GIVEN


# ---------------------------------------------------------------------------
# Uncertainty is surfaced, never assumed away
# ---------------------------------------------------------------------------


def test_unknown_awareness_date_is_indeterminate() -> None:
    """The awareness date is the disputed fact. Assuming it would manufacture it."""
    finding = assess_notice(
        FIDIC_2017_NOTICE_OF_CLAIM, awareness_date=None, notices=[notice(date(2026, 3, 5))]
    )
    assert finding.status is ComplianceStatus.INDETERMINATE
    assert finding.is_determinate is False
    assert finding.deadline is None
    assert any("question of fact" in w for w in finding.warnings)


def test_notice_predating_the_event_is_indeterminate() -> None:
    finding = assess_notice(
        FIDIC_2017_NOTICE_OF_CLAIM, awareness_date=AWARE, notices=[notice(date(2026, 2, 1))]
    )
    assert finding.status is ComplianceStatus.INDETERMINATE
    assert any("predates" in w for w in finding.warnings)


def test_unconfirmed_notice_is_assessed_but_flagged() -> None:
    finding = assess_notice(
        FIDIC_2017_NOTICE_OF_CLAIM,
        awareness_date=AWARE,
        notices=[notice(date(2026, 3, 10), confirmed=False, title="Letter 042.pdf")],
    )
    assert finding.status is ComplianceStatus.COMPLIANT
    assert any("not been confirmed" in w for w in finding.warnings)


def test_wrong_recipient_is_flagged() -> None:
    finding = assess_notice(
        FIDIC_2017_NOTICE_OF_CLAIM,
        awareness_date=AWARE,
        notices=[notice(date(2026, 3, 10), recipient="the Employer")],
    )
    assert any("recipient" in w for w in finding.warnings)


def test_missing_receipt_date_is_recorded_as_an_assumption() -> None:
    finding = assess_notice(
        FIDIC_2017_NOTICE_OF_CLAIM, awareness_date=AWARE, notices=[notice(date(2026, 3, 10))]
    )
    assert any("receipt date" in a for a in finding.assumptions)


def test_receipt_date_is_preferred_over_sent_date() -> None:
    finding = assess_notice(
        FIDIC_2017_NOTICE_OF_CLAIM,
        awareness_date=AWARE,
        notices=[notice(date(2026, 3, 25), received=date(2026, 4, 2))],
    )
    assert finding.status is ComplianceStatus.LATE, "receipt is after the deadline"
    assert finding.assumptions == []


def test_working_day_period_without_holidays_warns() -> None:
    requirement = NoticeRequirement(
        clause_number="X",
        edition="red-book-2017",
        period_days=10,
        day_count=DayCount.WORKING,
    )
    finding = assess_notice(
        requirement, awareness_date=AWARE, notices=[notice(date(2026, 3, 10))]
    )
    assert any("holiday calendar" in w for w in finding.warnings)


def test_earliest_qualifying_notice_is_used() -> None:
    """A claiming party may rely on its earliest qualifying notice."""
    finding = assess_notice(
        FIDIC_2017_NOTICE_OF_CLAIM,
        awareness_date=AWARE,
        notices=[notice(date(2026, 4, 10)), notice(date(2026, 3, 15))],
    )
    assert finding.status is ComplianceStatus.COMPLIANT
    assert finding.notice is not None
    assert finding.notice.effective_date == date(2026, 3, 15)


# ---------------------------------------------------------------------------
# Edition safety
# ---------------------------------------------------------------------------


def test_requirement_without_an_edition_is_rejected() -> None:
    with pytest.raises(ValueError):
        NoticeRequirement(clause_number="20.2.1", edition="", period_days=28)


def test_editions_expose_different_requirements() -> None:
    r2017 = requirements_for_edition("red-book-2017")
    r1987 = requirements_for_edition("red-book-1987")
    assert {r.clause_number for r in r2017} == {"20.2.1", "20.2.4"}
    assert {r.clause_number for r in r1987} == {"53.1"}


def test_the_two_editions_differ_on_condition_precedent() -> None:
    """The substantive difference the analysis must not blur."""
    assert FIDIC_2017_NOTICE_OF_CLAIM.is_condition_precedent is True
    assert FIDIC_1987_NOTICE_OF_CLAIM.is_condition_precedent is False


def test_unregistered_edition_returns_nothing_rather_than_substituting() -> None:
    """Applying 2017 periods to a 1999 contract is the ADR 0004 error."""
    assert requirements_for_edition("red-book-1999") == ()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_summary_states_the_outcome_plainly() -> None:
    compliant = assess_notice(
        FIDIC_2017_NOTICE_OF_CLAIM, awareness_date=AWARE, notices=[notice(date(2026, 3, 10))]
    )
    assert "day 9 of 28" in compliant.summary()

    late = assess_notice(
        FIDIC_2017_NOTICE_OF_CLAIM, awareness_date=AWARE, notices=[notice(date(2026, 4, 5))]
    )
    assert "7 day(s) after" in late.summary()
    assert "condition precedent" in late.summary()

    unknown = assess_notice(FIDIC_2017_NOTICE_OF_CLAIM, awareness_date=None)
    assert "cannot be determined" in unknown.summary()
