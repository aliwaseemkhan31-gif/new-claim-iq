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


class Obligation(str, Enum):
    """Which of the two submissions a requirement is about.

    The standard forms impose two in sequence, and they are different things.
    FIDIC 2017 Sub-Clause 20.2.1 requires a Notice of Claim within 28 days of
    awareness; Sub-Clause 20.2.4 requires a fully detailed Claim within 84. The
    1999 forms have the same shape at Sub-Clause 20.1 with 28 and 42 days.

    Keeping them apart matters because they fail differently and belong to
    different stages of the file. A missing Notice of Claim is a notice
    failure; a missing fully detailed Claim is a failure of the Claim itself,
    and reporting the second as the first tells a user to go and look in the
    wrong place.
    """

    NOTICE_OF_CLAIM = "notice_of_claim"
    """The first, short-period communication: "this happened, and I may have a
    claim"."""

    DETAILED_CLAIM = "detailed_claim"
    """The substantiated submission that follows it. Under FIDIC 2017
    Sub-Clause 20.2.4 it is defined as including (a) a detailed description of
    the event, (b) a statement of the contractual and/or other legal basis,
    (c) the contemporary records relied on, and (d) detailed supporting
    particulars of the amount and/or EOT claimed."""


class PeriodStart(str, Enum):
    """What starts a period running.

    Most periods run from the event, or from awareness of it. Some run from an
    earlier submission instead: FIDIC 1987 Sub-Clauses 44.2(b) and 53.3 allow
    28 days for detailed particulars *after the notice*, so a late notice moves
    the second deadline with it, and an unrecorded notice leaves it unknown.
    """

    AWARENESS = "awareness"
    NOTICE = "notice"


class Applies(str, Enum):
    """Which claims a requirement governs.

    The 1987 form runs two separate procedures: Clause 44 for extensions of
    time and Clause 53 for additional payment. Checking an extension-of-time
    claim against Clause 53 alone, as this module once did, reports on a
    provision the claim was never made under.
    """

    ALL = "all"
    TIME = "time"
    MONEY = "money"


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
        obligation: Notice of Claim, or the fully detailed Claim that follows
            it. Defaults to the former, which is what an unlabelled
            project-specific requirement is most likely to be.
        lapse_consequence: What the contract says happens if the period is
            missed, where that is something other than a condition precedent
            barring the claim. Set on FIDIC 2017 Sub-Clause 20.2.4, where
            failing to submit the statement of contractual basis in time makes
            the Notice of Claim lapse — a distinct mechanism from a time bar,
            and a reversible one. Empty where the contract states no
            consequence beyond the general law.
        title: Short name, e.g. "Notice of claim (Sub-Clause 53.1)".
        runs_from: What starts the period. ``NOTICE`` periods run from the
            effective date of the notice given under ``runs_from_clause``.
        runs_from_clause: The provision whose notice starts a ``NOTICE``
            period.
        applies_to: Which claims the requirement governs: all, those seeking
            time, or those seeking money.
        late_consequence: What the contract says follows from lateness where
            it is not a condition precedent but is still adverse, e.g. FIDIC
            1987 Sub-Clause 53.4 limiting recovery to what contemporary records
            verify. Reported with a late finding so "late" is never read as
            "harmless".
        consequence_clause: The provision that states ``late_consequence``,
            where it is not this one (FIDIC 1987 Sub-Clause 53.4 for 53.1 and
            53.3). Read so that a contract deleting it is noticed.
        source: Empty for the standard form. Where the project's own contract
            documents amend or add the requirement, says where, e.g.
            "Particular Conditions, p. 12". Shown wherever the requirement is,
            so an amended period is never mistaken for the FIDIC default.
    """

    clause_number: str
    edition: str
    period_days: int
    day_count: DayCount = DayCount.CALENDAR
    trigger_description: str = ""
    is_condition_precedent: bool = False
    recipient: str = ""
    description: str = ""
    obligation: Obligation = Obligation.NOTICE_OF_CLAIM
    lapse_consequence: str = ""
    title: str = ""
    runs_from: PeriodStart = PeriodStart.AWARENESS
    runs_from_clause: str = ""
    applies_to: Applies = Applies.ALL
    late_consequence: str = ""
    source: str = ""
    consequence_clause: str = ""

    def __post_init__(self) -> None:
        if self.period_days < 0:
            raise ValueError("period_days must not be negative")
        if not self.edition:
            raise ValueError(
                "A notice requirement must name its edition: notice periods "
                "differ between contract forms."
            )
        if self.runs_from is PeriodStart.NOTICE and not self.runs_from_clause:
            raise ValueError(
                "A period that runs from a notice must name the provision that "
                "notice is given under."
            )

    @property
    def label(self) -> str:
        """A short name for a list or checklist row."""
        if self.title:
            return self.title
        if self.obligation is Obligation.DETAILED_CLAIM:
            return "Fully detailed claim"
        return "Notice of claim"

    @property
    def is_amended(self) -> bool:
        """True where the project's own contract documents set this requirement."""
        return bool(self.source)


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

    obligation: Obligation | None = None
    """Whether this is the notice or the detailed submission that follows it.

    Needed where one provision imposes both: FIDIC 1987 Sub-Clause 44.2 and the
    1999 Sub-Clause 20.1 each require a notice and then detailed particulars
    under the same clause number, so the clause alone cannot say which a
    document is. None means not recorded, and the document counts towards
    either, as it did before the distinction existed."""

    record_id: str = ""
    """Identifies this record when several share one document: notices bound
    into a claim bundle are each a record, but all point at the bundle."""

    @property
    def key(self) -> str:
        return self.record_id or self.document_id

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
    if notice.obligation is not None and notice.obligation is not requirement.obligation:
        return False
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
    period_start_label: str = "the awareness date",
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
        period_start_label: How the start of the period is described in a
            warning. :func:`assess_requirements` passes the notice a period
            runs from.

    Returns:
        A finding with its assumptions and warnings exposed.
    """
    assumptions: list[str] = []
    warnings: list[str] = []

    if awareness_date is None:
        # The timing cannot be assessed, but whether anything was given at all
        # still can. Without this, "is a detailed claim on record?" read an
        # unknown awareness date as an answer.
        dated = [
            n for n in notices if given_under(n, requirement) and n.effective_date is not None
        ]
        return ComplianceFinding(
            status=ComplianceStatus.INDETERMINATE,
            requirement=requirement,
            awareness_date=None,
            deadline=None,
            notice=min(dated, key=lambda n: n.effective_date) if dated else None,  # type: ignore[arg-type]
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
            f"The notice predates {period_start_label}. Either the notice "
            f"relates to a different event, or one of the dates is wrong."
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
    if requirement.late_consequence:
        warnings.append(requirement.late_consequence)
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
    title="Notice of Claim (Sub-Clause 20.2.1)",
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
    title="Fully detailed Claim (Sub-Clause 20.2.4)",
    clause_number="20.2.4",
    edition="red-book-2017",
    period_days=84,
    day_count=DayCount.CALENDAR,
    trigger_description="the date of awareness of the event or circumstance",
    is_condition_precedent=False,
    recipient="the Engineer",
    description="fully detailed Claim",
    obligation=Obligation.DETAILED_CLAIM,
    lapse_consequence=(
        "Failure to submit the statement of the contractual and/or other legal basis within the period makes the Notice of Claim lapse: it is no longer a valid Notice. The Engineer must say so within 14 days, and if no such Notice is given the Notice of Claim is deemed valid after all."
    ),
)

# --- The 1987 form --------------------------------------------------------
#
# Two procedures, not one. Clause 44 governs extensions of time and Clause 53
# governs additional payment, and each has a notice followed by detailed
# particulars that run from the notice rather than from the event. Verified
# against the Red Book 1987 text held in this installation's knowledge base:
#
#   44.2 "Provided that the Engineer is not bound to make any determination
#        unless the Contractor has (a) within 28 days after such event has
#        first arisen notified the Engineer with a copy to the Employer, and
#        (b) within 28 days, or such other reasonable time as may be agreed by
#        the Engineer, after such notification submitted to the Engineer
#        detailed particulars of any extension of time ..."
#   53.1 "... he shall give notice of his intention to the Engineer, with a
#        copy to the Employer, within 28 days after the event giving rise to
#        the claim has first arisen."
#   53.3 "Within 28 days, or such other reasonable time as may be agreed by the
#        Engineer, of giving notice under Sub-Clause 53.1, the Contractor shall
#        send to the Engineer an account giving detailed particulars ..."
#   53.4 "If the Contractor fails to comply ... his entitlement to payment ...
#        shall not exceed such amount as the Engineer ... considers to be
#        verified by contemporary records ..."
#
# Neither is framed as a condition precedent in the 2017 manner, so neither is
# reported as a time bar. Each states its own adverse consequence instead,
# carried as ``late_consequence``.

_CONSEQUENCE_1987_44 = (
    "Under Sub-Clause 44.2 the Engineer is not bound to make any determination "
    "of extension of time unless the notice and particulars were given in time. "
    "Whether that defeats the claim is a legal question; it is not a condition "
    "precedent in the 2017 form's terms."
)

_CONSEQUENCE_1987_53 = (
    "Under Sub-Clause 53.4, failure to comply limits entitlement to the amount "
    "the Engineer or an arbitrator considers verified by contemporary records. "
    "The claim is not barred, but unrecorded cost is at risk."
)

FIDIC_1987_EOT_NOTICE = NoticeRequirement(
    clause_number="44.2",
    edition="red-book-1987",
    period_days=28,
    day_count=DayCount.CALENDAR,
    trigger_description="the event first arising",
    is_condition_precedent=False,
    recipient="the Engineer",
    title="Notice of the delay event (Sub-Clause 44.2(a))",
    description=(
        "Notification to the Engineer, with a copy to the Employer, within 28 "
        "days after the event has first arisen."
    ),
    applies_to=Applies.TIME,
    late_consequence=_CONSEQUENCE_1987_44,
)

FIDIC_1987_EOT_PARTICULARS = NoticeRequirement(
    clause_number="44.2",
    edition="red-book-1987",
    period_days=28,
    day_count=DayCount.CALENDAR,
    trigger_description="the notification under Sub-Clause 44.2(a)",
    is_condition_precedent=False,
    recipient="the Engineer",
    title="Detailed particulars of the extension (Sub-Clause 44.2(b))",
    description=(
        "Detailed particulars of the extension of time claimed, within 28 days "
        "after the notification, or such other reasonable time as the Engineer "
        "agrees. Where the event has a continuing effect, Sub-Clause 44.3 "
        "allows interim particulars at intervals of not more than 28 days and "
        "final particulars within 28 days of the end of its effects."
    ),
    obligation=Obligation.DETAILED_CLAIM,
    runs_from=PeriodStart.NOTICE,
    runs_from_clause="44.2",
    applies_to=Applies.TIME,
    late_consequence=_CONSEQUENCE_1987_44,
)

FIDIC_1987_NOTICE_OF_CLAIM = NoticeRequirement(
    clause_number="53.1",
    edition="red-book-1987",
    period_days=28,
    day_count=DayCount.CALENDAR,
    trigger_description="the event giving rise to the claim first arising",
    is_condition_precedent=False,
    recipient="the Engineer",
    title="Notice of intention to claim (Sub-Clause 53.1)",
    description=(
        "Notice of intention to claim. The 1987 form is not generally framed as "
        "a condition precedent in the manner of the 2017 form, and the "
        "consequences of late notice differ accordingly."
    ),
    applies_to=Applies.MONEY,
    late_consequence=_CONSEQUENCE_1987_53,
    consequence_clause="53.4",
)

FIDIC_1987_ACCOUNT = NoticeRequirement(
    clause_number="53.3",
    edition="red-book-1987",
    period_days=28,
    day_count=DayCount.CALENDAR,
    trigger_description="giving notice under Sub-Clause 53.1",
    is_condition_precedent=False,
    recipient="the Engineer",
    title="Account with detailed particulars (Sub-Clause 53.3)",
    description=(
        "An account giving detailed particulars of the amount claimed and its "
        "grounds, within 28 days of giving notice under Sub-Clause 53.1, or "
        "such other reasonable time as the Engineer agrees. A continuing event "
        "calls for interim accounts and a final account within 28 days of the "
        "end of its effects."
    ),
    obligation=Obligation.DETAILED_CLAIM,
    runs_from=PeriodStart.NOTICE,
    runs_from_clause="53.1",
    applies_to=Applies.MONEY,
    late_consequence=_CONSEQUENCE_1987_53,
    consequence_clause="53.4",
)

# --- The 1999 suite -------------------------------------------------------
#
# Sub-Clause 20.1 [Contractor's Claims] is common to the 1999 Red, Yellow and
# Silver Books: notice within 28 days of awareness, expressed as a condition
# precedent, and a fully detailed claim within 42 days. The recipient differs,
# because the Silver Book is an EPC/Turnkey form with no Engineer.
#
# Verified against the Silver Book 1999 text held in this installation's
# knowledge base:
#
#   "The notice shall be given as soon as practicable, and not later than 28
#    days after the Contractor became aware, or should have become aware, of
#    the event or circumstance. If the Contractor fails to give notice of a
#    claim within such period of 28 days, the Time for Completion shall not be
#    extended, the Contractor shall not be entitled to additional payment, and
#    the Employer shall be discharged from all liability in connection with
#    the claim."

FIDIC_1999_SILVER_NOTICE_OF_CLAIM = NoticeRequirement(
    title="Notice of claim (Sub-Clause 20.1)",
    clause_number="20.1",
    edition="silver-book-1999",
    period_days=28,
    day_count=DayCount.CALENDAR,
    trigger_description=(
        "the date the Contractor became aware, or should have become aware, "
        "of the event or circumstance"
    ),
    is_condition_precedent=True,
    recipient="the Employer",
    description=(
        "Notice of claim. The Silver Book has no Engineer, so notice is given "
        "to the Employer. Late notice discharges the Employer from all "
        "liability in connection with the claim."
    ),
)

FIDIC_1999_SILVER_FULLY_DETAILED_CLAIM = NoticeRequirement(
    title="Fully detailed claim (Sub-Clause 20.1)",
    clause_number="20.1",
    edition="silver-book-1999",
    period_days=42,
    day_count=DayCount.CALENDAR,
    trigger_description=(
        "the date the Contractor became aware, or should have become aware, "
        "of the event or circumstance"
    ),
    is_condition_precedent=False,
    recipient="the Employer",
    description=(
        "Fully detailed claim with full supporting particulars. The contract "
        "allows another period where the Contractor proposes one and the "
        "Employer approves it, so check the correspondence before relying on "
        "the 42 days."
    ),
    obligation=Obligation.DETAILED_CLAIM,
)

FIDIC_1999_RED_NOTICE_OF_CLAIM = NoticeRequirement(
    title="Notice of claim (Sub-Clause 20.1)",
    clause_number="20.1",
    edition="red-book-1999",
    period_days=28,
    day_count=DayCount.CALENDAR,
    trigger_description=(
        "the date the Contractor became aware, or should have become aware, "
        "of the event or circumstance"
    ),
    is_condition_precedent=True,
    recipient="the Engineer",
    description=(
        "Notice of claim. Taken from the 1999 suite's common Sub-Clause 20.1, "
        "which this installation holds and has verified in its Silver Book "
        "edition; the Red Book differs in addressing notice to the Engineer. "
        "Confirm against the Particular Conditions before relying on it."
    ),
)

FIDIC_1999_RED_FULLY_DETAILED_CLAIM = NoticeRequirement(
    title="Fully detailed claim (Sub-Clause 20.1)",
    clause_number="20.1",
    edition="red-book-1999",
    period_days=42,
    day_count=DayCount.CALENDAR,
    trigger_description=(
        "the date the Contractor became aware, or should have become aware, "
        "of the event or circumstance"
    ),
    is_condition_precedent=False,
    recipient="the Engineer",
    description=(
        "Fully detailed claim with full supporting particulars. Another period "
        "may be agreed with the Engineer."
    ),
    obligation=Obligation.DETAILED_CLAIM,
)

# --- Yellow Book 1999 -----------------------------------------------------
#
# Sub-Clause 20.1 is common to the 1999 suite. This installation holds no
# Yellow Book 1999 text, so these are taken from the Silver Book 1999 wording
# verified above, with the Engineer as recipient as in the Red Book. Confirm
# against the contract before relying on them.

FIDIC_1999_YELLOW_NOTICE_OF_CLAIM = NoticeRequirement(
    title="Notice of claim (Sub-Clause 20.1)",
    clause_number="20.1",
    edition="yellow-book-1999",
    period_days=28,
    day_count=DayCount.CALENDAR,
    trigger_description=(
        "the date the Contractor became aware, or should have become aware, "
        "of the event or circumstance"
    ),
    is_condition_precedent=True,
    recipient="the Engineer",
    description=(
        "Notice of claim, from the 1999 suite's common Sub-Clause 20.1. Not "
        "verified against a Yellow Book 1999 text held here; confirm against "
        "the contract before relying on it."
    ),
)

FIDIC_1999_YELLOW_FULLY_DETAILED_CLAIM = NoticeRequirement(
    title="Fully detailed claim (Sub-Clause 20.1)",
    clause_number="20.1",
    edition="yellow-book-1999",
    period_days=42,
    day_count=DayCount.CALENDAR,
    trigger_description=(
        "the date the Contractor became aware, or should have become aware, "
        "of the event or circumstance"
    ),
    is_condition_precedent=False,
    recipient="the Engineer",
    description=(
        "Fully detailed claim with full supporting particulars. Another period "
        "may be agreed with the Engineer."
    ),
    obligation=Obligation.DETAILED_CLAIM,
)

# --- The rest of the 2017 suite -------------------------------------------
#
# Clause 20.2 is common to the 2017 Red, Yellow and Silver Books. Verified
# against the Yellow Book 2017 text held in the knowledge base:
#
#   "The claiming Party shall give a Notice to the Engineer ... as soon as
#    practicable, and no later than 28 days after the claiming Party became
#    aware, or should have become aware, of the event or circumstance ... If
#    the claiming Party fails to give a Notice of Claim within this period of
#    28 days, ... the other Party shall be discharged from any liability in
#    connection with the event or circumstance giving rise to the Claim."

FIDIC_2017_YELLOW_NOTICE_OF_CLAIM = NoticeRequirement(
    title="Notice of Claim (Sub-Clause 20.2.1)",
    clause_number="20.2.1",
    edition="yellow-book-2017",
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

FIDIC_2017_YELLOW_FULLY_DETAILED_CLAIM = NoticeRequirement(
    title="Fully detailed Claim (Sub-Clause 20.2.4)",
    clause_number="20.2.4",
    edition="yellow-book-2017",
    period_days=84,
    day_count=DayCount.CALENDAR,
    trigger_description="the date of awareness of the event or circumstance",
    is_condition_precedent=False,
    recipient="the Engineer",
    description="fully detailed Claim",
    obligation=Obligation.DETAILED_CLAIM,
    lapse_consequence=(
        "Failure to submit the statement of the contractual and/or other legal basis within the period makes the Notice of Claim lapse: it is no longer a valid Notice. The Engineer must say so within 14 days, and if no such Notice is given the Notice of Claim is deemed valid after all."
    ),
)

FIDIC_2017_SILVER_NOTICE_OF_CLAIM = NoticeRequirement(
    title="Notice of Claim (Sub-Clause 20.2.1)",
    clause_number="20.2.1",
    edition="silver-book-2017",
    period_days=28,
    day_count=DayCount.CALENDAR,
    trigger_description=(
        "the date the claiming Party became aware, or should have become "
        "aware, of the event or circumstance"
    ),
    is_condition_precedent=True,
    recipient="the Employer",
    description=(
        "Notice of Claim. Taken from Clause 20.2, common to the 2017 suite and "
        "verified here in the Yellow Book edition; the Silver Book has no "
        "Engineer, so notice is given to the Employer. This installation's "
        "Silver Book 2017 is a scanned copy that has not been read, so confirm "
        "against the Particular Conditions before relying on it."
    ),
)

FIDIC_2017_SILVER_FULLY_DETAILED_CLAIM = NoticeRequirement(
    title="Fully detailed Claim (Sub-Clause 20.2.4)",
    clause_number="20.2.4",
    edition="silver-book-2017",
    period_days=84,
    day_count=DayCount.CALENDAR,
    trigger_description="the date of awareness of the event or circumstance",
    is_condition_precedent=False,
    recipient="the Employer",
    description="fully detailed Claim",
    obligation=Obligation.DETAILED_CLAIM,
    lapse_consequence=(
        "Failure to submit the statement of the contractual and/or other legal basis within the period makes the Notice of Claim lapse: it is no longer a valid Notice. The Engineer must say so within 14 days, and if no such Notice is given the Notice of Claim is deemed valid after all."
    ),
)

KNOWN_REQUIREMENTS: tuple[NoticeRequirement, ...] = (
    FIDIC_2017_NOTICE_OF_CLAIM,
    FIDIC_2017_FULLY_DETAILED_CLAIM,
    FIDIC_2017_YELLOW_NOTICE_OF_CLAIM,
    FIDIC_2017_YELLOW_FULLY_DETAILED_CLAIM,
    FIDIC_2017_SILVER_NOTICE_OF_CLAIM,
    FIDIC_2017_SILVER_FULLY_DETAILED_CLAIM,
    FIDIC_1999_RED_NOTICE_OF_CLAIM,
    FIDIC_1999_RED_FULLY_DETAILED_CLAIM,
    FIDIC_1999_YELLOW_NOTICE_OF_CLAIM,
    FIDIC_1999_YELLOW_FULLY_DETAILED_CLAIM,
    FIDIC_1999_SILVER_NOTICE_OF_CLAIM,
    FIDIC_1999_SILVER_FULLY_DETAILED_CLAIM,
    FIDIC_1987_EOT_NOTICE,
    FIDIC_1987_EOT_PARTICULARS,
    FIDIC_1987_NOTICE_OF_CLAIM,
    FIDIC_1987_ACCOUNT,
)


def requirements_for_edition(edition: str) -> tuple[NoticeRequirement, ...]:
    """Known notice requirements for ``edition``.

    Returns an empty tuple for an unregistered edition rather than falling back
    to another — applying 2017 periods to a 1999 contract would be exactly the
    error ADR 0004 exists to prevent.
    """
    return tuple(r for r in KNOWN_REQUIREMENTS if r.edition == edition)


# --------------------------------------------------------------------------
# Which requirements govern a claim, and assessing them together
# --------------------------------------------------------------------------

#: Mirrors ``analysis.domain.strands.TIME_CLAIM_TYPES``. Restated rather than
#: imported so this module keeps no dependency on the analysis app; a test
#: holds the two together.
TIME_CLAIM_TYPES = frozenset(
    {"eot", "delay", "disruption", "acceleration", "compensation_event"}
)

#: Mirrors ``analysis.domain.strands.MONEY_CLAIM_TYPES``.
MONEY_CLAIM_TYPES = frozenset(
    {"cost", "variation", "disruption", "acceleration", "payment", "compensation_event"}
)


def applicable_requirements(
    requirements: Sequence[NoticeRequirement],
    *,
    claim_type: str,
    seeks_time: bool = False,
    seeks_money: bool = False,
) -> tuple[NoticeRequirement, ...]:
    """The requirements that govern a claim of this kind.

    A claim seeks time when its type is a time type or a time is claimed, and
    money likewise. Where neither can be said, every requirement is kept: a
    claim whose relief is not yet recorded should be shown all the deadlines it
    might face, not none of them.
    """
    time = seeks_time or claim_type in TIME_CLAIM_TYPES
    money = seeks_money or claim_type in MONEY_CLAIM_TYPES
    if not time and not money:
        return tuple(requirements)
    return tuple(
        r
        for r in requirements
        if r.applies_to is Applies.ALL
        or (r.applies_to is Applies.TIME and time)
        or (r.applies_to is Applies.MONEY and money)
    )


def assess_requirements(
    requirements: Sequence[NoticeRequirement],
    *,
    awareness_date: date | None,
    notices: Sequence[NoticeEvent] = (),
    holidays: Iterable[date] = (),
    weekend: frozenset[int] = DEFAULT_WEEKEND,
) -> list[ComplianceFinding]:
    """Assess every requirement for a claim, in the order given.

    Periods that run from awareness are assessed first, so a period that runs
    from a notice can start from the notice that satisfied its anchor. Where
    that notice is not on record the dependent period has no start, and the
    finding says so rather than borrowing the awareness date.
    """
    findings: dict[int, ComplianceFinding] = {}
    for index, requirement in enumerate(requirements):
        if requirement.runs_from is PeriodStart.AWARENESS:
            findings[index] = assess_notice(
                requirement,
                awareness_date=awareness_date,
                notices=notices,
                holidays=holidays,
                weekend=weekend,
            )

    for index, requirement in enumerate(requirements):
        if requirement.runs_from is not PeriodStart.NOTICE:
            continue
        anchor = next(
            (
                f
                for f in findings.values()
                if f.requirement.clause_number == requirement.runs_from_clause
                and f.requirement.obligation is Obligation.NOTICE_OF_CLAIM
                and f.notice is not None
                and f.notice.effective_date is not None
            ),
            None,
        )
        if anchor is None:
            findings[index] = ComplianceFinding(
                status=ComplianceStatus.INDETERMINATE,
                requirement=requirement,
                awareness_date=None,
                deadline=None,
                notice=None,
                warnings=[
                    f"This period runs from the notice under Clause "
                    f"{requirement.runs_from_clause}, and no dated notice under "
                    f"it is on record. Record that notice and the deadline "
                    f"follows from it."
                ],
            )
            continue

        start_notice = anchor.notice
        assert start_notice is not None and start_notice.effective_date is not None
        # The notice that starts the period cannot also be the submission
        # that answers it.
        others = [n for n in notices if n.key != start_notice.key]
        finding = assess_notice(
            requirement,
            awareness_date=start_notice.effective_date,
            notices=others,
            holidays=holidays,
            weekend=weekend,
            period_start_label="the notice it follows",
        )
        finding.assumptions.insert(
            0,
            f"The period runs from the notice under Clause "
            f"{requirement.runs_from_clause} ({start_notice.document_title!r}), "
            f"effective {start_notice.effective_date.isoformat()}.",
        )
        findings[index] = finding

    return [findings[i] for i in range(len(requirements))]


# --------------------------------------------------------------------------
# The project's own contract: amendments to the standard requirements
# --------------------------------------------------------------------------


class AmendmentAction(str, Enum):
    AMEND = "amend"
    """Replaces the standard requirement under the same clause and obligation."""

    ADD = "add"
    """A requirement the standard form does not impose."""

    REMOVE = "remove"
    """The contract deletes the standard requirement."""


@dataclass(frozen=True)
class Amendment:
    """A requirement as the project's contract documents state it.

    Confirmed by a person before it is applied: a period read from a
    Particular Conditions page is a reading, and an amended deadline applied on
    a misreading moves every finding that depends on it.
    """

    clause_number: str
    obligation: Obligation
    action: AmendmentAction = AmendmentAction.AMEND
    period_days: int | None = None
    day_count: DayCount | None = None
    is_condition_precedent: bool | None = None
    recipient: str | None = None
    runs_from: PeriodStart | None = None
    runs_from_clause: str | None = None
    applies_to: Applies | None = None
    title: str = ""
    description: str = ""
    late_consequence: str | None = None
    source: str = "the project's contract documents"


def apply_amendments(
    standard: Sequence[NoticeRequirement],
    amendments: Sequence[Amendment],
    *,
    edition: str,
) -> tuple[NoticeRequirement, ...]:
    """The requirements that apply once the contract's own terms are read.

    The Particular Conditions take precedence over the general conditions
    where they conflict, so an amendment replaces the standard requirement it
    matches (same clause, same obligation), keeping whatever it does not
    change. Unmatched amendments are added; removals drop the match.
    """
    from dataclasses import replace

    result = list(standard)
    for amendment in amendments:
        match = next(
            (
                i
                for i, r in enumerate(result)
                if r.clause_number == amendment.clause_number
                and r.obligation is amendment.obligation
            ),
            None,
        )
        if amendment.action is AmendmentAction.REMOVE:
            if match is not None:
                result.pop(match)
            continue

        changes = {
            key: value
            for key, value in (
                ("period_days", amendment.period_days),
                ("day_count", amendment.day_count),
                ("is_condition_precedent", amendment.is_condition_precedent),
                ("recipient", amendment.recipient),
                ("runs_from", amendment.runs_from),
                ("runs_from_clause", amendment.runs_from_clause),
                ("applies_to", amendment.applies_to),
                ("late_consequence", amendment.late_consequence),
            )
            if value is not None
        }
        if amendment.title:
            changes["title"] = amendment.title
        if amendment.description:
            changes["description"] = amendment.description

        if match is not None and amendment.action is AmendmentAction.AMEND:
            result[match] = replace(result[match], source=amendment.source, **changes)
            continue

        if amendment.period_days is None:
            # An addition with no period cannot be assessed; skip it rather
            # than invent one.
            continue
        result.append(
            NoticeRequirement(
                clause_number=amendment.clause_number,
                edition=edition,
                obligation=amendment.obligation,
                source=amendment.source,
                **changes,
            )
        )
    return tuple(result)
