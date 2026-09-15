"""Notice compliance analysis.

Whether a notice was given in time is frequently the whole claim. Under the
FIDIC 2017 Red Book, Sub-Clause 20.2.1 requires a Notice of Claim within 28
days of the claiming Party becoming aware, or having become aware, of the event
— and 20.2.2 makes the consequence of missing it explicit: the claim lapses.
A contractor with a strong causation case and a late notice can recover nothing.

This module computes the arithmetic and states the assumptions. It deliberately
does **not** determine entitlement:

- Whether the time bar is enforceable can depend on the governing law
  (prevention principle, penalty/forfeiture doctrines, local statute).
- Whether notice was "given" can turn on the contractual definition of a
  Notice, its addressee, and its form.
- The awareness date is a question of fact, often disputed, and is exactly what
  the parties argue about.

So the output is a **finding with its inputs and assumptions exposed**, not a
verdict. Where an input is unknown, the result says so rather than assuming a
value — assuming an awareness date would manufacture the very fact in dispute.

Pure stdlib; runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Iterable, Sequence


class DayCount(str, Enum):
    """How a contractual period is counted."""

    CALENDAR = "calendar"
    """Every day counts. The FIDIC default: periods are expressed in days and
    are not qualified as working days."""

    WORKING = "working"
    """Weekends and specified holidays are excluded. Used where the Particular
    Conditions or governing law so provide."""


class ComplianceStatus(str, Enum):
    COMPLIANT = "compliant"
    LATE = "late"
    NOT_GIVEN = "not_given"
    INDETERMINATE = "indeterminate"
    """A required input is unknown or disputed. A first-class outcome: the
    honest answer when the awareness date is not established."""

    NOT_REQUIRED = "not_required"


@dataclass(frozen=True)
class NoticeRequirement:
    """A contractual notice obligation.

    Attributes:
        clause_number: The provision imposing it, e.g. ``20.2.1``.
        edition: Governing edition. Required — periods differ between editions,
            and a requirement without one cannot be evaluated (ADR 0004).
        period_days: The period allowed.
        day_count: Calendar or working days.
        trigger_description: What starts time running, in the contract's words.
        is_condition_precedent: True where the contract makes compliance a
            condition of entitlement. Drives severity, not the arithmetic.
        recipient: Who must receive it, e.g. "the Engineer".
        description: Human-readable summary.
    """

    clause_number: str
    edition: str
    period_days: int
    day_count: DayCount = DayCount.CALENDAR
    trigger_description: str = ""
    is_condition_precedent: bool = False
    recipient: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        if self.period_days < 0:
            raise ValueError("period_days must not be negative")
        if not self.edition:
            raise ValueError(
                "A notice requirement must name its edition: notice periods "
                "differ between contract forms."
            )


@dataclass(frozen=True)
class NoticeEvent:
    """A notice, or a candidate for one, as evidenced by a document."""

    document_id: str
    document_title: str
    sent_date: date | None
    received_date: date | None = None
    subject: str = ""
    recipient: str = ""
    is_confirmed_notice: bool = False
    """True when the document has been confirmed as a contractual Notice rather
    than ordinary correspondence. Unconfirmed candidates are still evaluated,
    and flagged."""

    clause_number: str = ""
    """The provision the notice is given under. A notice under one provision
    does not satisfy another: a Notice of Claim is not a fully detailed claim.
    Blank means not recorded, and is evaluated with a warning."""

    @property
    def effective_date(self) -> date | None:
        """The date used for the calculation.

        Receipt is preferred where known. Most notice provisions are framed
        around the recipient having been notified, and receipt is the date a
        tribunal is more likely to treat as decisive. Where only the sent date
        is known, it is used and the assumption is recorded.
        """
        return self.received_date or self.sent_date


@dataclass
class ComplianceFinding:
    """The outcome of one notice-compliance assessment."""

    status: ComplianceStatus
    requirement: NoticeRequirement
    awareness_date: date | None
    deadline: date | None
    notice: NoticeEvent | None
    days_used: int | None = None
    days_late: int | None = None
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_determinate(self) -> bool:
        return self.status is not ComplianceStatus.INDETERMINATE

    @property
    def is_time_barred(self) -> bool:
        """True only where the notice was late *and* compliance is a condition
        precedent. Lateness alone does not bar a claim."""
        return (
            self.status is ComplianceStatus.LATE
            and self.requirement.is_condition_precedent
        )

    def summary(self) -> str:
        clause = f"Clause {self.requirement.clause_number}"
        if self.status is ComplianceStatus.INDETERMINATE:
            return f"{clause}: cannot be determined on the available evidence."
        if self.status is ComplianceStatus.NOT_GIVEN:
            return f"{clause}: no notice identified in the available documents."
        if self.status is ComplianceStatus.COMPLIANT:
            return (
                f"{clause}: notice given on day {self.days_used} of "
                f"{self.requirement.period_days}."
            )
        if self.status is ComplianceStatus.LATE:
            bar = " The provision is expressed as a condition precedent." if (
                self.requirement.is_condition_precedent
            ) else ""
            return (
                f"{clause}: notice given {self.days_late} day(s) after the "
                f"{self.requirement.period_days}-day period expired.{bar}"
            )
        return f"{clause}: no notice required."


def given_under(notice: NoticeEvent, requirement: NoticeRequirement) -> bool:
    """Whether ``notice`` can count towards ``requirement``.

    Exact clause match, or no clause recorded (assessed, with a warning). A
    notice under a parent or sibling provision does not count: which provision
    a notice satisfies is a question for a person, not a prefix match.
    """
    clause = notice.clause_number.strip()
    return not clause or clause == requirement.clause_number


_PARTY_ROLE_LABELS = {
    "employer": "the Employer",
    "contractor": "the Contractor",
    "engineer": "the Engineer",
}


def recipient_label(name: str, role: str | None) -> str:
    """How a notice's recipient is described for the compliance check.

    A requirement names its recipient by contractual role ("the Engineer");
    correspondence names a firm ("Oversight Engineering"). Matching the firm
    against the role would flag every correctly addressed notice, so the
    party's role travels with its name.
    """
    label = _PARTY_ROLE_LABELS.get(role or "")
    if name and label:
        return f"{name} ({label})"
    return name or label or ""


DEFAULT_WEEKEND = frozenset({5, 6})  # Saturday, Sunday


def add_days(
    start: date,
    days: int,
    day_count: DayCount = DayCount.CALENDAR,
    *,
    holidays: Iterable[date] = (),
    weekend: frozenset[int] = DEFAULT_WEEKEND,
) -> date:
    """Add a contractual period to ``start``.

    Time runs from the **day after** the trigger. A 28-day period beginning on
    1 March expires on 29 March, not 28 March: the trigger day is not counted.
    This is the ordinary construction of "within N days after", and getting it
    wrong shifts every deadline by one day.

    >>> add_days(date(2026, 3, 1), 28)
    datetime.date(2026, 3, 29)
    """
    if days < 0:
        raise ValueError("days must not be negative")

    if day_count is DayCount.CALENDAR:
        return start + timedelta(days=days)

    holiday_set = set(holidays)
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() in weekend or current in holiday_set:
            continue
        remaining -= 1
    return current


def count_days(
    start: date,
    end: date,
    day_count: DayCount = DayCount.CALENDAR,
    *,
    holidays: Iterable[date] = (),
    weekend: frozenset[int] = DEFAULT_WEEKEND,
) -> int:
    """Count the period between ``start`` and ``end``, excluding ``start``.

    Negative when ``end`` precedes ``start``, which is meaningful: a notice
    dated before the event it notifies is a real and important anomaly.
    """
    if end < start:
        return -count_days(end, start, day_count, holidays=holidays, weekend=weekend)

    if day_count is DayCount.CALENDAR:
        return (end - start).days

    holiday_set = set(holidays)
    counted = 0
    current = start
    while current < end:
        current += timedelta(days=1)
        if current.weekday() in weekend or current in holiday_set:
            continue
        counted += 1
    return counted


def assess_notice(
    requirement: NoticeRequirement,
    *,
    awareness_date: date | None,
    notices: Sequence[NoticeEvent] = (),
    holidays: Iterable[date] = (),
    weekend: frozenset[int] = DEFAULT_WEEKEND,
) -> ComplianceFinding:
    """Assess compliance with one notice requirement.

    Args:
        requirement: The obligation.
        awareness_date: When time started running. ``None`` yields
            ``INDETERMINATE`` — the date is a disputed question of fact and is
            never assumed.
        notices: Candidate notices. The earliest by effective date is assessed,
            since a claiming party is entitled to rely on its earliest
            qualifying notice.
        holidays: Non-working days, for a working-day period.
        weekend: Weekday indices treated as weekend.

    Returns:
        A finding with its assumptions and warnings exposed.
    """
    assumptions: list[str] = []
    warnings: list[str] = []

    if awareness_date is None:
        return ComplianceFinding(
            status=ComplianceStatus.INDETERMINATE,
            requirement=requirement,
            awareness_date=None,
            deadline=None,
            notice=None,
            warnings=[
                "The date on which the claiming Party became aware, or should "
                "have become aware, of the event is not established by the "
                "available documents. Without it, compliance cannot be "
                "assessed. This date is commonly disputed and is a question of "
                "fact."
            ],
        )

    deadline = add_days(
        awareness_date,
        requirement.period_days,
        requirement.day_count,
        holidays=holidays,
        weekend=weekend,
    )

    if requirement.day_count is DayCount.WORKING and not holidays:
        warnings.append(
            "A working-day period was applied with no holiday calendar. Public "
            "holidays have not been excluded, so the deadline may be earlier "
            "than the true contractual date."
        )

    applicable = [n for n in notices if given_under(n, requirement)]
    other_provisions = len(notices) - len(applicable)
    candidates = [n for n in applicable if n.effective_date is not None]
    if not candidates:
        if other_provisions:
            warnings.append(
                f"{other_provisions} notice(s) recorded under other provisions "
                f"were not counted. A notice under one provision does not "
                f"satisfy Clause {requirement.clause_number}."
            )
        return ComplianceFinding(
            status=ComplianceStatus.NOT_GIVEN,
            requirement=requirement,
            awareness_date=awareness_date,
            deadline=deadline,
            notice=None,
            assumptions=assumptions,
            warnings=warnings
            + (
                [
                    "No dated notice was identified. Absence from the reviewed "
                    "documents is not proof that no notice was given."
                ]
            ),
        )

    notice = min(candidates, key=lambda n: n.effective_date)  # type: ignore[arg-type]
    effective = notice.effective_date
    assert effective is not None  # guaranteed by the filter above

    if not notice.clause_number.strip():
        warnings.append(
            f"{notice.document_title!r} is not recorded as given under any "
            f"provision. It has been assessed against Clause "
            f"{requirement.clause_number}, which may not be what it was given under."
        )

    if notice.received_date is None:
        assumptions.append(
            "No receipt date is recorded; the date the notice was sent has been "
            "used. If the provision is framed around receipt, the operative "
            "date may be later."
        )
    if not notice.is_confirmed_notice:
        warnings.append(
            f"{notice.document_title!r} has not been confirmed as a contractual "
            f"Notice. Whether it satisfies the requirement depends on its form, "
            f"content and addressee."
        )
    if requirement.recipient and notice.recipient and (
        requirement.recipient.lower() not in notice.recipient.lower()
    ):
        warnings.append(
            f"The requirement names {requirement.recipient!r} as recipient; the "
            f"notice is addressed to {notice.recipient!r}."
        )

    days_used = count_days(
        awareness_date, effective, requirement.day_count, holidays=holidays, weekend=weekend
    )

    if days_used < 0:
        warnings.append(
            "The notice predates the awareness date. Either the notice relates "
            "to a different event, or one of the dates is wrong."
        )
        return ComplianceFinding(
            status=ComplianceStatus.INDETERMINATE,
            requirement=requirement,
            awareness_date=awareness_date,
            deadline=deadline,
            notice=notice,
            days_used=days_used,
            assumptions=assumptions,
            warnings=warnings,
        )

    if effective <= deadline:
        return ComplianceFinding(
            status=ComplianceStatus.COMPLIANT,
            requirement=requirement,
            awareness_date=awareness_date,
            deadline=deadline,
            notice=notice,
            days_used=days_used,
            assumptions=assumptions,
            warnings=warnings,
        )

    days_late = count_days(
        deadline, effective, requirement.day_count, holidays=holidays, weekend=weekend
    )
    if requirement.is_condition_precedent:
        warnings.append(
            "The provision is expressed as a condition precedent. Whether a "
            "time bar is enforceable can depend on the governing law and on the "
            "conduct of the parties; this is a legal question outside the scope "
            "of this assessment."
        )

    return ComplianceFinding(
        status=ComplianceStatus.LATE,
        requirement=requirement,
        awareness_date=awareness_date,
        deadline=deadline,
        notice=notice,
        days_used=days_used,
        days_late=days_late,
        assumptions=assumptions,
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# Known requirements
#
# Edition-specific, because the periods and their consequences differ. Used to
# seed the requirement catalogue; a project's Particular Conditions may amend
# them, in which case the project's own values take precedence.
# --------------------------------------------------------------------------

FIDIC_2017_NOTICE_OF_CLAIM = NoticeRequirement(
    clause_number="20.2.1",
    edition="red-book-2017",
    period_days=28,
    day_count=DayCount.CALENDAR,
    trigger_description=(
        "the date the claiming Party became aware, or should have become "
        "aware, of the event or circumstance"
    ),
    is_condition_precedent=True,
    recipient="the Engineer",
    description="Notice of Claim",
)

FIDIC_2017_FULLY_DETAILED_CLAIM = NoticeRequirement(
    clause_number="20.2.4",
    edition="red-book-2017",
    period_days=84,
    day_count=DayCount.CALENDAR,
    trigger_description="the date of awareness of the event or circumstance",
    is_condition_precedent=False,
    recipient="the Engineer",
    description="fully detailed Claim",
)

FIDIC_1987_NOTICE_OF_CLAIM = NoticeRequirement(
    clause_number="53.1",
    edition="red-book-1987",
    period_days=28,
    day_count=DayCount.CALENDAR,
    trigger_description="the event giving rise to the claim first arising",
    is_condition_precedent=False,
    recipient="the Engineer",
    description=(
        "Notice of intention to claim. The 1987 form is not generally framed as "
        "a condition precedent in the manner of the 2017 form, and the "
        "consequences of late notice differ accordingly."
    ),
)

KNOWN_REQUIREMENTS: tuple[NoticeRequirement, ...] = (
    FIDIC_2017_NOTICE_OF_CLAIM,
    FIDIC_2017_FULLY_DETAILED_CLAIM,
    FIDIC_1987_NOTICE_OF_CLAIM,
)


def requirements_for_edition(edition: str) -> tuple[NoticeRequirement, ...]:
    """Known notice requirements for ``edition``.

    Returns an empty tuple for an unregistered edition rather than falling back
    to another — applying 2017 periods to a 1999 contract would be exactly the
    error ADR 0004 exists to prevent.
    """
    return tuple(r for r in KNOWN_REQUIREMENTS if r.edition == edition)
