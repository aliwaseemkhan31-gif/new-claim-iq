"""Tests for preliminary claim screening.

Screening answers "is this claim in a fit state to work on", from the recorded
facts alone, on the day it is raised. The properties that matter:

- it never reports more certainty than the record supports;
- only a notice late under a condition precedent is adverse to the claim;
- a missing input produces one honest "cannot be determined", not a column of
  failures that all share a cause.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from claimiq.claims.domain.notice_compliance import (
    ComplianceFinding,
    ComplianceStatus,
    NoticeEvent,
    NoticeRequirement,
)
from claimiq.claims.domain.screening import (
    CHECKS,
    CHECKS_BY_AREA,
    Area,
    CheckStatus,
    Outcome,
    ScreeningInput,
    Weight,
    screen_claim,
)

TODAY = date(2026, 9, 27)

REQUIREMENT = NoticeRequirement(
    clause_number="53.1",
    edition="red-book-1987",
    period_days=28,
    recipient="the Engineer",
    description="Notice of intention to claim additional payment.",
)

CONDITION_PRECEDENT = NoticeRequirement(
    clause_number="20.2.1",
    edition="red-book-2017",
    period_days=28,
    is_condition_precedent=True,
    recipient="the Engineer",
    description="Notice of Claim.",
)


def notice(**kwargs) -> NoticeEvent:
    defaults = dict(
        document_id="d1",
        document_title="Notice of claim",
        sent_date=date(2026, 8, 20),
        received_date=date(2026, 8, 21),
        recipient="the Engineer",
        is_confirmed_notice=True,
        clause_number="53.1",
    )
    defaults.update(kwargs)
    return NoticeEvent(**defaults)


def finding(**kwargs) -> ComplianceFinding:
    defaults = dict(
        status=ComplianceStatus.COMPLIANT,
        requirement=REQUIREMENT,
        awareness_date=date(2026, 8, 12),
        deadline=date(2026, 9, 9),
        notice=notice(),
        days_used=8,
    )
    defaults.update(kwargs)
    return ComplianceFinding(**defaults)


def complete_input(**kwargs) -> ScreeningInput:
    """A claim with everything recorded and nothing wrong with it."""
    defaults = dict(
        claim_type="eot",
        today=TODAY,
        edition_code="red-book-1987",
        edition_label="FIDIC Red Book 1987 (4th Edition)",
        contractual_basis=("44.1", "53.1"),
        clauses_found=("44.1", "53.1"),
        knowledge_base_published=True,
        has_claimant=True,
        has_respondent=True,
        notice_requirements_registered=True,
        notice_findings=(finding(),),
        event_date=date(2026, 8, 10),
        awareness_date=date(2026, 8, 12),
        description="Access to the Section 2 embankment area was not given.",
        evidence_count=2,
        document_count=1,
        required_records=(("The triggering event", ("daily_report", "site_record")),),
        time_claimed_days=30,
    )
    defaults.update(kwargs)
    return ScreeningInput(**defaults)


# ---------------------------------------------------------------------------
# The checklist itself
# ---------------------------------------------------------------------------


def test_every_check_is_reachable_and_uniquely_coded() -> None:
    codes = [c.code for c in CHECKS]
    assert len(codes) == len(set(codes))
    assert sum(len(v) for v in CHECKS_BY_AREA.values()) == len(CHECKS)
    assert {c.area for c in CHECKS} == set(Area)


def test_every_declared_dependency_exists_and_precedes_its_dependant() -> None:
    """A dependency declared after its dependant would silently never apply."""
    seen: set = set()
    for check in CHECKS:
        for code in check.depends_on:
            assert code in seen, f"{check.code} depends on {code}, which runs later"
        seen.add(check.code)


def test_every_check_states_a_condition_and_a_reason() -> None:
    for check in CHECKS:
        assert check.question.endswith("?"), check.code
        assert check.why.strip(), check.code
        assert check.condition.strip(), check.code


def test_informational_checks_declare_no_remedy() -> None:
    """They never fail, so there is nothing to remedy."""
    for check in CHECKS:
        if check.weight is Weight.INFORMATIONAL:
            assert not check.remedy, check.code


def test_every_check_is_evaluated_even_when_it_does_not_apply() -> None:
    """A check that vanishes reads as a screen that forgot to look."""
    report = screen_claim(ScreeningInput(claim_type="eot", today=TODAY))
    assert len(report.results) == len(CHECKS)


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


def test_a_fully_recorded_claim_is_assessable() -> None:
    report = screen_claim(complete_input())
    assert report.outcome is Outcome.READY
    assert not report.blocking_outstanding
    assert not report.advisory_outstanding


def test_a_bare_claim_is_not_assessable_and_says_why() -> None:
    report = screen_claim(ScreeningInput(claim_type="eot", today=TODAY))
    assert report.outcome is Outcome.NOT_READY
    codes = {r.check.code for r in report.blocking_outstanding}
    assert {"CB1", "CB2", "EV1", "QR1"} <= codes


def test_advisory_gaps_alone_leave_the_claim_assessable() -> None:
    report = screen_claim(complete_input(evidence_count=0, document_count=0))
    assert report.outcome is Outcome.READY_WITH_QUERIES
    assert report.by_code("RC1").status is CheckStatus.INCOMPLETE
    assert not report.blocking_outstanding


def test_not_ready_is_not_a_view_on_the_merits() -> None:
    """NOT_READY must be reachable with nothing adverse to the claim on record."""
    report = screen_claim(complete_input(event_date=None))
    assert report.outcome is Outcome.NOT_READY
    assert not report.barred


# ---------------------------------------------------------------------------
# Area 2 — the only area that can be adverse to the claim
# ---------------------------------------------------------------------------


def test_a_time_bar_is_reported_as_barred_and_names_the_clause() -> None:
    late = finding(
        status=ComplianceStatus.LATE,
        requirement=CONDITION_PRECEDENT,
        days_late=5,
        notice=notice(clause_number="20.2.1"),
    )
    report = screen_claim(
        complete_input(edition_code="red-book-2017", notice_findings=(late,))
    )
    assert report.outcome is Outcome.BARRED
    barred = report.by_code("NC4")
    assert barred.status is CheckStatus.BARRED
    assert barred.items == ("20.2.1",)
    assert "condition precedent" in barred.detail
    assert "not a determination" in barred.detail


def test_lateness_without_a_condition_precedent_is_never_barred() -> None:
    late = finding(status=ComplianceStatus.LATE, days_late=3)
    report = screen_claim(complete_input(notice_findings=(late,)))
    assert report.by_code("NC4").status is CheckStatus.SATISFIED
    assert report.by_code("NC5").status is CheckStatus.INCOMPLETE
    assert report.outcome is Outcome.READY_WITH_QUERIES
    assert not report.barred


def test_only_the_time_bar_check_may_return_barred() -> None:
    """Widening BARRED would overstate four areas that are readiness, not law."""
    late = finding(
        status=ComplianceStatus.LATE, requirement=CONDITION_PRECEDENT, days_late=5
    )
    report = screen_claim(
        ScreeningInput(
            claim_type="eot",
            today=TODAY,
            edition_code="red-book-2017",
            notice_requirements_registered=True,
            awareness_date=date(2026, 8, 12),
            notice_findings=(late,),
        )
    )
    barred = [r.check.code for r in report.results if r.status is CheckStatus.BARRED]
    assert barred == ["NC4"]


def test_a_missing_notice_says_absence_is_not_proof() -> None:
    missing = finding(status=ComplianceStatus.NOT_GIVEN, notice=None, days_used=None)
    report = screen_claim(complete_input(notice_findings=(missing,)))
    result = report.by_code("NC3")
    assert result.status is CheckStatus.INCOMPLETE
    assert result.items == ("53.1",)
    assert "not proof" in result.detail


def test_a_notice_to_the_wrong_party_is_reported() -> None:
    misdirected = finding(notice=notice(recipient="National Highway Authority"))
    report = screen_claim(complete_input(notice_findings=(misdirected,)))
    result = report.by_code("NC6")
    assert result.status is CheckStatus.INCOMPLETE
    assert "the Engineer" in result.items[0]
    assert "National Highway Authority" in result.items[0]


def test_an_unreceipted_or_unconfirmed_notice_is_reported_separately() -> None:
    report = screen_claim(
        complete_input(
            notice_findings=(
                finding(notice=notice(received_date=None, is_confirmed_notice=False)),
            )
        )
    )
    assert report.by_code("NC7").status is CheckStatus.INCOMPLETE
    assert report.by_code("NC8").status is CheckStatus.INCOMPLETE
    assert report.outcome is Outcome.READY_WITH_QUERIES


def test_notice_requirements_from_another_edition_are_not_substituted() -> None:
    report = screen_claim(complete_input(notice_requirements_registered=False))
    assert report.by_code("NC2").status is CheckStatus.NOT_APPLICABLE
    # Everything downstream follows, rather than reporting a missing notice.
    for code in ("NC3", "NC4", "NC5"):
        assert report.by_code(code).status is CheckStatus.NOT_APPLICABLE, code


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def test_a_missing_awareness_date_gives_one_cause_not_a_column_of_failures() -> None:
    report = screen_claim(complete_input(awareness_date=None))
    assert report.by_code("NC1").status is CheckStatus.INDETERMINATE
    for code in ("NC3", "NC4", "NC5", "NC6", "NC7"):
        result = report.by_code(code)
        assert result.status is CheckStatus.INDETERMINATE, code
        assert "NC1" in result.detail, code
    # None of them is reported as a failure of the claim.
    assert not report.barred


def test_a_chained_dependency_points_at_the_root_cause() -> None:
    report = screen_claim(complete_input(awareness_date=None))
    detail = report.by_code("NC8").detail
    assert detail.count("Cannot be determined until") == 1
    assert "NC1" in detail


def test_an_undeclared_edition_makes_the_clause_lookup_indeterminate() -> None:
    report = screen_claim(complete_input(edition_code="", edition_label=""))
    assert report.by_code("CB1").status is CheckStatus.INDETERMINATE
    assert report.by_code("CB3").status is CheckStatus.INDETERMINATE


def test_an_inapplicable_check_says_so_rather_than_blaming_a_dependency() -> None:
    report = screen_claim(complete_input(amount_claimed=None, time_claimed_days=None))
    assert report.by_code("QR1").status is CheckStatus.INCOMPLETE
    assert report.by_code("QR2").status is CheckStatus.NOT_APPLICABLE
    assert "No amount is claimed" in report.by_code("QR2").detail


# ---------------------------------------------------------------------------
# Area 1 — contractual entitlement
# ---------------------------------------------------------------------------


def test_a_clause_not_found_is_not_reported_as_absent_from_the_contract() -> None:
    """Clause detection over a scanned form is imperfect; the wording must own that."""
    report = screen_claim(complete_input(clauses_found=("44.1",)))
    result = report.by_code("CB3")
    assert result.status is CheckStatus.INCOMPLETE
    assert result.items == ("53.1",)
    assert "not absent from the contract" in result.detail


def test_clause_lookup_is_indeterminate_without_a_published_standard_form() -> None:
    report = screen_claim(complete_input(knowledge_base_published=False))
    assert report.by_code("CB3").status is CheckStatus.INDETERMINATE


def test_missing_parties_are_named_individually() -> None:
    report = screen_claim(complete_input(has_respondent=False))
    result = report.by_code("CB4")
    assert result.status is CheckStatus.INCOMPLETE
    assert result.items == ("respondent",)


# ---------------------------------------------------------------------------
# Area 3 — event occurrence
# ---------------------------------------------------------------------------


def test_awareness_before_the_event_is_reported_as_a_data_error() -> None:
    report = screen_claim(
        complete_input(event_date=date(2026, 8, 12), awareness_date=date(2026, 8, 10))
    )
    result = report.by_code("EV3")
    assert result.status is CheckStatus.INCOMPLETE
    assert "2026-08-10" in result.detail and "2026-08-12" in result.detail


def test_a_long_gap_between_event_and_awareness_is_not_a_finding() -> None:
    """Awareness can legitimately be much later. No maximum gap is tested."""
    report = screen_claim(
        complete_input(event_date=date(2024, 1, 1), awareness_date=date(2026, 8, 12))
    )
    assert report.by_code("EV3").status is CheckStatus.SATISFIED


def test_a_future_date_is_reported() -> None:
    report = screen_claim(complete_input(event_date=date(2027, 1, 1)))
    result = report.by_code("EV4")
    assert result.status is CheckStatus.INCOMPLETE
    assert "2027-01-01" in result.items[0]


def test_today_is_supplied_not_read_from_the_clock() -> None:
    """Otherwise the check is untestable and changes answer overnight."""
    past = screen_claim(complete_input(event_date=date(2026, 9, 26)))
    assert past.by_code("EV4").status is CheckStatus.SATISFIED
    future = screen_claim(
        complete_input(event_date=date(2026, 9, 26), today=date(2026, 9, 1))
    )
    assert future.by_code("EV4").status is CheckStatus.INCOMPLETE


@pytest.mark.parametrize("claim_type", ["variation", "acceleration"])
def test_an_instruction_is_required_only_where_the_claim_stands_on_one(
    claim_type: str,
) -> None:
    report = screen_claim(complete_input(claim_type=claim_type, amount_claimed=Decimal("1000"), currency="PKR"))
    assert report.by_code("EV5").status is CheckStatus.INCOMPLETE

    linked = screen_claim(
        complete_input(
            claim_type=claim_type,
            amount_claimed=Decimal("1000"),
            currency="PKR",
            instruction_linked=True,
        )
    )
    assert linked.by_code("EV5").status is CheckStatus.SATISFIED


def test_an_instruction_is_not_expected_of_an_extension_of_time_claim() -> None:
    report = screen_claim(complete_input())
    assert report.by_code("EV5").status is CheckStatus.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# Area 4 — records
# ---------------------------------------------------------------------------


def test_the_records_area_never_blocks() -> None:
    """Gathering evidence is the work screening precedes."""
    for check in CHECKS_BY_AREA[Area.RECORDS]:
        assert check.weight is not Weight.BLOCKING, check.code


def test_what_the_claim_type_will_need_is_listed_and_never_fails() -> None:
    report = screen_claim(
        complete_input(
            evidence_count=0,
            document_count=0,
            required_records=(
                ("The triggering event", ("daily_report", "site_record")),
                ("Time impact", ("programme",)),
            ),
        )
    )
    result = report.by_code("RC2")
    assert result.status is CheckStatus.SATISFIED
    assert result.items == (
        "The triggering event: daily report, site record",
        "Time impact: programme",
    )


def test_contemporaneity_is_declared_uncomputable_rather_than_guessed() -> None:
    report = screen_claim(complete_input())
    result = report.by_code("RC3")
    assert result.status is CheckStatus.SATISFIED
    assert "not computed" in result.detail.lower()


# ---------------------------------------------------------------------------
# Area 5 — relief
# ---------------------------------------------------------------------------


def test_relief_expected_of_the_claim_type_is_named_when_absent() -> None:
    report = screen_claim(complete_input(time_claimed_days=None))
    result = report.by_code("QR1")
    assert result.status is CheckStatus.INCOMPLETE
    assert "extension of time" in result.detail


def test_a_money_claim_without_a_currency_is_reported() -> None:
    report = screen_claim(
        complete_input(claim_type="cost", amount_claimed=Decimal("4500000"), currency="")
    )
    assert report.by_code("QR2").status is CheckStatus.INCOMPLETE


def test_zero_or_negative_relief_is_reported() -> None:
    report = screen_claim(complete_input(time_claimed_days=0))
    result = report.by_code("QR3")
    assert result.status is CheckStatus.INCOMPLETE
    assert "0 day(s)" in result.items[0]


def test_relief_that_does_not_match_the_claim_type_is_a_query_not_an_error() -> None:
    report = screen_claim(
        complete_input(claim_type="payment", amount_claimed=Decimal("100"), currency="PKR")
    )
    result = report.by_code("QR4")
    assert result.status is CheckStatus.INCOMPLETE
    assert result.check.weight is Weight.ADVISORY
    assert "time is claimed" in result.items[0]


def test_a_claim_seeking_both_time_and_money_satisfies_the_relief_check() -> None:
    report = screen_claim(
        complete_input(
            claim_type="compensation_event",
            time_claimed_days=30,
            amount_claimed=Decimal("4500000"),
            currency="PKR",
        )
    )
    assert report.by_code("QR1").status is CheckStatus.SATISFIED
    assert report.by_code("QR4").status is CheckStatus.SATISFIED


# ---------------------------------------------------------------------------
# The real claim on file, as a regression case
# ---------------------------------------------------------------------------


def test_the_n55_claim_as_recorded_is_assessable_with_queries() -> None:
    """CL-001 on the Indus Highway N-55 project, exactly as recorded.

    Screened against the live record, this is what it finds: notice in time
    under Clause 53.1 but addressed to the Employer where the provision names
    the Engineer, with no receipt date and no human confirmation; no account of
    the event; and money claimed on an extension-of-time claim, which usually
    means a second claim is hiding inside the first.
    """
    report = screen_claim(
        complete_input(
            description="",
            amount_claimed=Decimal("4500000"),
            currency="PKR",
            time_claimed_days=30,
            notice_findings=(
                finding(
                    notice=notice(
                        recipient="National Highway Authority (the Employer)",
                        received_date=None,
                        is_confirmed_notice=False,
                    )
                ),
            ),
        )
    )
    assert report.outcome is Outcome.READY_WITH_QUERIES
    outstanding = {r.check.code for r in report.advisory_outstanding}
    assert outstanding == {"EV2", "NC6", "NC7", "NC8", "QR4"}
    assert not report.barred
    assert not report.blocking_outstanding
