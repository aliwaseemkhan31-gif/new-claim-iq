"""Preliminary claim screening — DRAFT, NOT WIRED IN.

Nothing imports this module. It declares the checklist for review before any
engine, API or screen is built. The rules below are the thing to review; the
mechanism that runs them is deliberately absent.

WHAT THIS IS
    A first pass over a claim as *recorded*, answerable the moment the claim is
    entered: before issues are created, before evidence is linked, before any
    model runs. It reads claim fields, the project's declared edition, and the
    notices on record. It reads nothing that takes a person days to assemble.

WHAT THIS IS NOT
    Not a view on the merits. A claim can pass every check here and still fail
    completely; a claim can fail several and still be sound once the record is
    assembled. ``GapReport.is_complete`` already carries this warning for
    evidential completeness, and the same applies with more force here —
    screening looks at less.

    Not a gate. Nothing should be blocked from proceeding because screening
    says so. The determination is a person's (ADR 0006), and so is the decision
    to pursue a thin claim.

    Not a score. There is no percentage and no weighting. A count of satisfied
    checks would be read as a probability of success, which it is not.

WHY IT IS SEPARATE FROM THE EXISTING COMPUTATIONS
    ``evidence_gaps`` answers "what does the record establish", and needs the
    record. ``notice_compliance`` answers "was notice in time", and needs the
    dates. The analysis strands answer "what do the contract and documents
    say", and need a published knowledge base and minutes of model time. None
    of them can answer "is this claim in a fit state to work on" on day one,
    which is the only question here.

RELATIONSHIP TO WHAT EXISTS
    Every check either reads a claim field directly or delegates to a
    computation that already exists. Nothing here re-implements notice timing,
    evidence assessment or clause lookup:

        Area 2 checks delegate to ``notice_compliance.assess_notice``.
        Area 4 advisories read ``evidence_gaps.REQUIRED_ELEMENTS``.
        Claim-type applicability reuses ``strands.TIME_CLAIM_TYPES`` and
        ``strands.MONEY_CLAIM_TYPES``.

Pure stdlib; must run on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import List, Mapping, Optional, Tuple

from claimiq.claims.domain.notice_compliance import (
    ComplianceFinding,
    ComplianceStatus,
    Obligation,
)


class CheckStatus(str, Enum):
    """The outcome of one check."""

    SATISFIED = "satisfied"
    """The record answers the question. Not "the answer is favourable"."""

    INCOMPLETE = "incomplete"
    """The record does not answer it, and what is missing can be named."""

    INDETERMINATE = "indeterminate"
    """A required input is itself unknown, so the check cannot run. Distinct
    from INCOMPLETE: no amount of work on *this* item will resolve it until
    the input it depends on is recorded. Mirrors
    ``ComplianceStatus.INDETERMINATE``."""

    BARRED = "barred"
    """The contract, on the recorded facts, bars the claim. Reserved for the
    single case the system can actually compute: notice late under a provision
    expressed as a condition precedent. No other check may return this."""

    LAPSED = "lapsed"
    """The contract, on the recorded facts, makes the Notice of Claim lapse.

    Deliberately not BARRED. FIDIC 2017 Sub-Clause 20.2.4 provides that where
    the statement of the contractual and/or other legal basis is not submitted
    within the period, "the Notice of Claim shall be deemed to have lapsed, it
    shall no longer be considered as a valid Notice". That is a different
    mechanism from a condition precedent, and a reversible one: the Engineer
    must give Notice of it within 14 days, and if no such Notice is given the
    Notice of Claim is deemed valid after all.

    Reporting it as a time bar would overstate it; reporting it as an ordinary
    outstanding item would understate it. Like BARRED, only the check that
    computes it may return it."""

    NOT_APPLICABLE = "not_applicable"
    """Does not apply to this claim type. Distinct from SATISFIED — a quantum
    check skipped on a pure extension-of-time claim has not passed."""


class Area(str, Enum):
    """A group of checks that ask about the same thing.

    Declaration order is presentation order: :func:`services.screening.payload`
    iterates this enum. It follows the chain in :class:`Stage` — event first,
    then notice, then the three parts of the Claim itself.
    """

    EVENT = "event"
    NOTICE = "notice"
    SUBMISSION = "submission"
    ENTITLEMENT = "entitlement"
    RECORDS = "records"
    RELIEF = "relief"


AREA_LABELS: Mapping[Area, str] = {
    Area.EVENT: "Event occurrence and causation",
    Area.NOTICE: "Notice and procedural compliance",
    # The four areas below map onto FIDIC 2017 Sub-Clause 20.2.4: the
    # submission itself, then (b) the contractual basis, (c) the contemporary
    # records and (d) the particulars of the amount and/or EOT claimed. They
    # are named as parts of the Claim rather than peers of it.
    Area.SUBMISSION: "The Claim as submitted",
    Area.ENTITLEMENT: "Contractual basis of the Claim",
    Area.RECORDS: "Supporting evidence and contemporary records",
    Area.RELIEF: "Quantum and relief claimed",
}


class Stage(str, Enum):
    """The three stages a claim passes through, in order.

    The areas above are the working groups; these are the chain a claims person
    reads them along. Event, then Notice, then Claim — and the Claim contains
    the basis, the records and the relief rather than sitting beside them.

    This is the contract's own structure, not a presentation choice. FIDIC 2017
    Sub-Clause 20.2.4 defines the "fully detailed Claim" as a submission which
    includes (a) a detailed description of the event, (b) a statement of the
    contractual and/or other legal basis, (c) all contemporary records relied
    on, and (d) detailed supporting particulars of the amount and/or EOT
    claimed. The 1999 forms have the same shape at Sub-Clause 20.1: notice
    first, fully detailed claim after.
    """

    EVENT = "event"
    NOTICE = "notice"
    CLAIM = "claim"


STAGE_LABELS: Mapping[Stage, str] = {
    Stage.EVENT: "Event",
    Stage.NOTICE: "Notice",
    Stage.CLAIM: "Claim",
}

#: What each stage asks, in one line. Shown under the stage name, because
#: "Event" and "Notice" on their own do not say what is being looked at.
STAGE_DESCRIPTIONS: Mapping[Stage, str] = {
    Stage.EVENT: "What happened, and when.",
    Stage.NOTICE: "Whether the event was notified, in time and to the right party.",
    Stage.CLAIM: "What is being asked for, on what basis, and on what evidence.",
}

#: Areas belonging to each stage, in reading order. Every area appears exactly
#: once; :data:`STAGE_AREAS` and :class:`Area` are kept consistent by a test.
STAGE_AREAS: Mapping[Stage, Tuple[Area, ...]] = {
    Stage.EVENT: (Area.EVENT,),
    Stage.NOTICE: (Area.NOTICE,),
    Stage.CLAIM: (
        Area.SUBMISSION,
        Area.ENTITLEMENT,
        Area.RECORDS,
        Area.RELIEF,
    ),
}


def stage_of(area: Area) -> Stage:
    """The stage ``area`` belongs to."""
    for stage, areas in STAGE_AREAS.items():
        if area in areas:
            return stage
    raise KeyError(area)


class Weight(str, Enum):
    """What an unsatisfied check means for working on the claim."""

    BLOCKING = "blocking"
    """The claim cannot be meaningfully assessed until this is resolved.
    Blocking is about assessability, never about merit."""

    ADVISORY = "advisory"
    """Worth knowing and worth fixing, but assessment can proceed."""

    INFORMATIONAL = "informational"
    """Never fails. States what this claim type will require, so the person
    knows what to go and get."""


@dataclass(frozen=True)
class ScreeningCheck:
    """One question asked of a claim as recorded.

    Attributes:
        code: Stable identifier. Referenced by tests and by any stored result,
            so it must not change once used.
        area: Which of the five areas it belongs to. The area in turn belongs
            to one of the three stages — see :class:`Stage`.
        question: The question, in the words a claims person would use.
        why: What turns on it. Shown when someone asks why they are being
            asked; a check nobody understands gets ignored or gamed.
        condition: The rule, stated precisely enough to implement without
            further interpretation. This is the part to review.
        weight: See :class:`Weight`.
        applies_to: Claim type codes this applies to, or empty for all.
        depends_on: Codes whose INCOMPLETE or INDETERMINATE result makes this
            check INDETERMINATE rather than failing on its own account.
        remedy: What to do about it, concretely.
    """

    code: str
    area: Area
    question: str
    why: str
    condition: str
    weight: Weight
    applies_to: Tuple[str, ...] = ()
    depends_on: Tuple[str, ...] = ()
    remedy: str = ""


# ---------------------------------------------------------------------------
# Claim-type groupings
#
# Mirrors strands.TIME_CLAIM_TYPES / MONEY_CLAIM_TYPES. Repeated as literals
# here only so this draft reads standalone; on implementation, import them
# rather than keeping a second copy.
# ---------------------------------------------------------------------------

_TIME_TYPES = ("eot", "delay", "disruption", "acceleration", "compensation_event")
_MONEY_TYPES = (
    "cost", "variation", "disruption", "acceleration", "payment",
    "compensation_event",
)
_INSTRUCTED_TYPES = ("variation", "acceleration")


# ---------------------------------------------------------------------------
# Claim stage — Contractual basis of the Claim
#
# Part of the Claim, not a stage of its own: FIDIC 2017 Sub-Clause 20.2.4(b)
# makes the statement of the contractual and/or other legal basis a
# constituent of the fully detailed Claim.
#
# Screening cannot decide whether a clause confers entitlement; that is the
# entitlement strand, and it needs the contract text. What it can decide is
# whether the claim has said which provisions it stands on, and whether those
# provisions are findable in the conditions the project declared.
# ---------------------------------------------------------------------------

ENTITLEMENT_CHECKS: Tuple[ScreeningCheck, ...] = (
    ScreeningCheck(
        code="CB1",
        area=Area.ENTITLEMENT,
        question="Has the project declared which conditions of contract govern it?",
        why=(
            "Without a declared edition no notice period can be computed and no "
            "standard-form text can be retrieved. An edition is never assumed "
            "(ADR 0004)."
        ),
        condition=(
            "SATISFIED when project.contract_edition is non-empty. "
            "INDETERMINATE otherwise, not INCOMPLETE: the omission belongs to "
            "the project, not to this claim."
        ),
        weight=Weight.BLOCKING,
        remedy="Set the conditions of contract on the project.",
    ),
    ScreeningCheck(
        code="CB2",
        area=Area.ENTITLEMENT,
        question="Does the claim say which provisions it relies on?",
        why=(
            "A claim that names no provision cannot be tested against one. It "
            "also leaves retrieval with nothing to look up: the entitlement "
            "strand builds its query from these clause numbers."
        ),
        condition=(
            "SATISFIED when claim.contractual_basis holds at least one "
            "non-blank entry. INCOMPLETE otherwise."
        ),
        weight=Weight.BLOCKING,
        remedy="Record the clause numbers relied on, for example 44.1, 53.1.",
    ),
    ScreeningCheck(
        code="CB3",
        area=Area.ENTITLEMENT,
        question="Do those provisions appear in the declared edition?",
        why=(
            "A clause number from the wrong edition, or a typo, sends every "
            "later retrieval to the wrong place or to nothing at all."
        ),
        condition=(
            "For each entry in contractual_basis, look it up in the published "
            "knowledge base for project.contract_edition. SATISFIED when every "
            "entry is found. INCOMPLETE, naming the entries not found, when any "
            "is absent. INDETERMINATE when no knowledge base is published for "
            "that edition. "
            "Advisory deliberately: clause detection over a scanned standard "
            "form is imperfect, so not-found means not found in the text we "
            "hold, never absent from the contract. The wording shown to the "
            "reader must say exactly that."
        ),
        weight=Weight.ADVISORY,
        depends_on=("CB1", "CB2"),
        remedy="Check the clause number, or publish the standard form for this edition.",
    ),
    ScreeningCheck(
        code="CB4",
        area=Area.ENTITLEMENT,
        question="Are the claimant and the respondent identified?",
        why=(
            "A claim is made by someone against someone. The respondent also "
            "decides whether a notice went to the right party, which NC6 tests."
        ),
        condition=(
            "SATISFIED when claim.claimant and claim.respondent are both set. "
            "INCOMPLETE naming whichever is absent."
        ),
        weight=Weight.ADVISORY,
        remedy="Record both parties, adding them to the project first if needed.",
    ),
)


# ---------------------------------------------------------------------------
# Notice stage — Notice and procedural compliance
#
# Scoped to the Notice of Claim — the first, short-period communication
# ("this happened, and I may have a claim"). The fully detailed Claim that
# follows it is a different obligation with a different period and a different
# consequence, and it is checked in the Claim stage under Area.SUBMISSION.
# Running these checks over both, as they used to, reported a missing detailed
# Claim as a notice failure and sent the reader to the wrong part of the file.
#
# The only area where screening can reach a conclusion adverse to the claim on
# the contract, because it is the only one that is arithmetic over recorded
# dates. Every check delegates to notice_compliance; none re-implements it.
# ---------------------------------------------------------------------------

NOTICE_CHECKS: Tuple[ScreeningCheck, ...] = (
    ScreeningCheck(
        code="NC1",
        area=Area.NOTICE,
        question="Is the awareness date recorded?",
        why=(
            "Every notice period runs from it. It is also the most commonly "
            "disputed date in the file, which is why it is recorded separately "
            "from the event date rather than inferred from it."
        ),
        condition=(
            "SATISFIED when claim.awareness_date is set. INDETERMINATE "
            "otherwise, and every other check in this area is then "
            "INDETERMINATE too."
        ),
        weight=Weight.BLOCKING,
        remedy="Record when the claiming party knew, or should have known.",
    ),
    ScreeningCheck(
        code="NC2",
        area=Area.NOTICE,
        question="Are notice requirements registered for this edition?",
        why=(
            "Periods from another edition are never substituted. Where none are "
            "registered, the honest answer is that nothing can be computed."
        ),
        condition=(
            "SATISFIED when requirements_for_edition(edition) is non-empty. "
            "NOT_APPLICABLE otherwise, naming the edition."
        ),
        weight=Weight.ADVISORY,
        depends_on=("CB1",),
    ),
    ScreeningCheck(
        code="NC3",
        area=Area.NOTICE,
        question="Is a notice on record for each requirement?",
        why=(
            "Absence from the record is not proof that no notice was given, but "
            "it is what the file shows, and it is what a respondent tests first."
        ),
        condition=(
            "Run assess_notice for each requirement. SATISFIED when none "
            "returns NOT_GIVEN. INCOMPLETE naming each requirement that does. "
            "The wording must state that absence from the record is not proof "
            "that no notice was given."
        ),
        weight=Weight.BLOCKING,
        depends_on=("NC1", "NC2"),
        remedy=(
            "Record the letter as correspondence, then assert it as a Notice "
            "under the clause it was given under."
        ),
    ),
    ScreeningCheck(
        code="NC4",
        area=Area.NOTICE,
        question="Is any notice late under a condition precedent?",
        why=(
            "This is the one thing screening can find that defeats a claim on "
            "the recorded facts alone, and it is worth finding on day one "
            "rather than after the evidence is assembled."
        ),
        condition=(
            "BARRED when any finding has is_time_barred True, that is LATE and "
            "requirement.is_condition_precedent. SATISFIED when none is. "
            "Lateness alone is not barred and must never be reported as such: "
            "NC5 reports that. The wording must name the clause and say the "
            "consequence depends on the provision terms."
        ),
        weight=Weight.BLOCKING,
        depends_on=("NC1", "NC2"),
        remedy=(
            "Read the provision and its stated consequence. A computed bar is a "
            "reason to take advice, not a determination."
        ),
    ),
    ScreeningCheck(
        code="NC5",
        area=Area.NOTICE,
        question="Is any notice late under a provision that is not a condition precedent?",
        why=(
            "Late notice that does not bar the claim still weakens it and "
            "invites an argument about prejudice."
        ),
        condition=(
            "SATISFIED when no finding is LATE. INCOMPLETE otherwise, naming "
            "each late requirement and the days late. Never BARRED."
        ),
        weight=Weight.ADVISORY,
        depends_on=("NC1", "NC2"),
    ),
    ScreeningCheck(
        code="NC6",
        area=Area.NOTICE,
        question="Did each notice go to the party the provision names?",
        why=(
            "A notice to the Employer under a clause naming the Engineer is a "
            "live argument. assess_notice already produces this warning; "
            "screening surfaces it at the front instead of inside a tab."
        ),
        condition=(
            "SATISFIED when no finding carries a recipient-mismatch warning. "
            "INCOMPLETE naming the requirement, the named recipient and the "
            "actual one."
        ),
        weight=Weight.ADVISORY,
        depends_on=("NC1", "NC3", "CB4"),
        remedy="Record the correct recipient, or note why the addressee stands.",
    ),
    ScreeningCheck(
        code="NC7",
        area=Area.NOTICE,
        question="Is the receipt date recorded for each notice relied on?",
        why=(
            "Most notice provisions turn on receipt. Where it is unknown the "
            "sent date is used and the assumption recorded, which is a weaker "
            "position than it appears."
        ),
        condition=(
            "SATISFIED when every notice relied on has a received_date. "
            "INCOMPLETE otherwise, naming which."
        ),
        weight=Weight.ADVISORY,
        depends_on=("NC1", "NC3"),
        remedy="Record the receipt date, or the proof of delivery.",
    ),
    ScreeningCheck(
        code="NC8",
        area=Area.NOTICE,
        question="Has a person confirmed each notice satisfies its provision?",
        why=(
            "Whether a letter is a Notice under a provision is contested, and "
            "the system records it as an assertion rather than a fact. An "
            "unconfirmed assertion should not read as compliance."
        ),
        condition=(
            "SATISFIED when every Notice relied on has is_confirmed_notice "
            "True. INCOMPLETE otherwise."
        ),
        weight=Weight.ADVISORY,
        depends_on=("NC3",),
        remedy="Read the letter against the provision and confirm it, or do not.",
    ),
)


# ---------------------------------------------------------------------------
# Event stage — Event occurrence and causation
#
# A boundary worth being explicit about: occurrence can be screened, causation
# cannot. Whether an event caused the effect claimed is a question about
# programmes, records and argument. Nothing in the recorded fields answers it,
# and a check that pretended to would be the kind of plausible-sounding
# judgement the evidence-gap engine was built to avoid. Causation is assessed
# at the evidence stage (element "causation") and by the causation strand.
# ---------------------------------------------------------------------------

EVENT_CHECKS: Tuple[ScreeningCheck, ...] = (
    ScreeningCheck(
        code="EV1",
        area=Area.EVENT,
        question="Is the date of the event recorded?",
        why=(
            "Without it there is no chronology, and no way to test whether the "
            "awareness date is credible."
        ),
        condition="SATISFIED when claim.event_date is set. INCOMPLETE otherwise.",
        weight=Weight.BLOCKING,
        remedy="Record the date the event or circumstance occurred.",
    ),
    ScreeningCheck(
        code="EV2",
        area=Area.EVENT,
        question="Is the claimant account of the event recorded?",
        why=(
            "The narrative is what a reader assesses everything else against. "
            "It is also the claimant own account, and is treated as an "
            "assertion rather than evidence throughout."
        ),
        condition=(
            "SATISFIED when claim.description is non-blank. INCOMPLETE "
            "otherwise. No length or quality test: judging prose is not "
            "screening."
        ),
        weight=Weight.ADVISORY,
        remedy="Record what happened, in the claimant own words.",
    ),
    ScreeningCheck(
        code="EV3",
        area=Area.EVENT,
        question="Are the event and awareness dates consistent?",
        why=(
            "An awareness date before the event is a data-entry error, and it "
            "silently shifts every notice deadline earlier."
        ),
        condition=(
            "SATISFIED when awareness_date >= event_date, or either is absent "
            "(EV1 and NC1 report those). INCOMPLETE when awareness_date is "
            "strictly earlier, stating both dates. "
            "Not a merits point: awareness can legitimately be much later than "
            "the event, and no maximum gap is tested."
        ),
        weight=Weight.ADVISORY,
        depends_on=("EV1", "NC1"),
        remedy="Correct whichever date is wrong.",
    ),
    ScreeningCheck(
        code="EV4",
        area=Area.EVENT,
        question="Are the recorded dates in the past?",
        why="A future event date is a typo, and it produces negative deadlines.",
        condition=(
            "SATISFIED when event_date and awareness_date are both on or "
            "before today. INCOMPLETE naming any that is not. "
            "Today is passed in, never read from the clock inside the domain, "
            "so the check is testable."
        ),
        weight=Weight.ADVISORY,
        depends_on=("EV1",),
        remedy="Correct the date.",
    ),
    ScreeningCheck(
        code="EV5",
        area=Area.EVENT,
        question="Is the instruction relied on identified?",
        why=(
            "A variation or acceleration claim stands or falls on a valid "
            "instruction from someone with authority. Without one identified "
            "there is nothing to test."
        ),
        condition=(
            "Applies only to claim types in _INSTRUCTED_TYPES. SATISFIED when "
            "at least one correspondence item of kind instruction or "
            "site_instruction is linked to the claim, or the claim names one in "
            "contractual_basis terms. INCOMPLETE otherwise. NOT_APPLICABLE for "
            "every other claim type."
        ),
        weight=Weight.ADVISORY,
        applies_to=_INSTRUCTED_TYPES,
        remedy="Record the instruction as correspondence and link it.",
    ),
)


# ---------------------------------------------------------------------------
# Claim stage — Supporting evidence and contemporary records
#
# Sub-Clause 20.2.4(c): the contemporary records relied on are submitted with
# the Claim.
#
# At screening time there is usually no evidence linked, and that is expected
# rather than a failure: gathering it is the work that screening precedes. So
# this area mostly tells the person what this claim type will need. Only one
# check can fail, and it asks whether anything at all has been identified.
#
# The evidence that *is* linked is assessed by evidence_gaps, which this does
# not duplicate.
# ---------------------------------------------------------------------------

RECORDS_CHECKS: Tuple[ScreeningCheck, ...] = (
    ScreeningCheck(
        code="RC1",
        area=Area.RECORDS,
        question="Has any record been identified in support of the claim?",
        why=(
            "A claim with no record identified at all is an assertion. This is "
            "not a judgement on sufficiency, only on whether the work has "
            "started."
        ),
        condition=(
            "SATISFIED when the claim has at least one linked evidence item or "
            "at least one source document. INCOMPLETE otherwise. "
            "Advisory, not blocking: a claim can properly be screened on the "
            "day it is raised, before anything is gathered."
        ),
        weight=Weight.ADVISORY,
        remedy="Link the records relied on, or note that gathering has not begun.",
    ),
    ScreeningCheck(
        code="RC2",
        area=Area.RECORDS,
        question="What records will this claim type require?",
        why=(
            "Naming them at the outset is more useful than reporting their "
            "absence later, when the contemporaneous records may no longer be "
            "obtainable."
        ),
        condition=(
            "Never fails. Lists, for each essential element in "
            "REQUIRED_ELEMENTS[claim_type], that element label and its "
            "typical_documents. Status is always SATISFIED; the value is the "
            "list itself."
        ),
        weight=Weight.INFORMATIONAL,
    ),
    ScreeningCheck(
        code="RC3",
        area=Area.RECORDS,
        question="Are the records contemporaneous?",
        why=(
            "A record made at the time carries weight that one reconstructed "
            "afterwards does not, and many provisions require contemporary "
            "records in terms."
        ),
        condition=(
            "Never fails, and is stated rather than computed. Whether a record "
            "is contemporaneous depends on when it was actually made, which is "
            "not something the system holds: a document date is the date on the "
            "document, and an upload date is when someone got round to it. "
            "Do not infer contemporaneity from either. "
            "If this is ever computed, it needs a recorded made-on date that a "
            "person asserts, which does not exist today."
        ),
        weight=Weight.INFORMATIONAL,
    ),
)


# ---------------------------------------------------------------------------
# Claim stage — Quantum and relief claimed
#
# Sub-Clause 20.2.4(d): the supporting particulars of the amount and/or EOT
# claimed are submitted with the Claim.
#
# Screening checks that relief is stated and internally coherent. It never
# checks whether the amount is right: that needs the measurement, the rates and
# the records, and it is the quantum strand and a quantity surveyor.
# ---------------------------------------------------------------------------

RELIEF_CHECKS: Tuple[ScreeningCheck, ...] = (
    ScreeningCheck(
        code="QR1",
        area=Area.RELIEF,
        question="Does the claim state what it asks for?",
        why=(
            "A claim with no relief stated cannot be determined, and cannot be "
            "valued or programmed against."
        ),
        condition=(
            "For a claim type in _TIME_TYPES only: SATISFIED when "
            "time_claimed_days is set. "
            "For a type in _MONEY_TYPES only: SATISFIED when amount_claimed is "
            "set. "
            "For a type in both: SATISFIED when at least one is set. "
            "For other: SATISFIED when either is set. "
            "INCOMPLETE otherwise, naming what the type would normally seek."
        ),
        weight=Weight.BLOCKING,
        remedy="Record the time claimed, the amount claimed, or both.",
    ),
    ScreeningCheck(
        code="QR2",
        area=Area.RELIEF,
        question="Is a currency recorded against the amount?",
        why=(
            "An amount without a currency cannot be totalled across a register "
            "or stated in a determination. The claims register already groups "
            "by currency and shows a no-currency bucket."
        ),
        condition=(
            "Applies when amount_claimed is set. SATISFIED when currency is "
            "non-blank. INCOMPLETE otherwise. NOT_APPLICABLE when no amount is "
            "claimed."
        ),
        weight=Weight.ADVISORY,
        depends_on=("QR1",),
        remedy="Record the currency of the amount claimed.",
    ),
    ScreeningCheck(
        code="QR3",
        area=Area.RELIEF,
        question="Is the relief claimed a positive quantity?",
        why="Zero or negative relief is a data-entry error, not a claim.",
        condition=(
            "SATISFIED when every value that is set is greater than zero. "
            "INCOMPLETE naming any that is zero or negative. NOT_APPLICABLE "
            "when neither is set, since QR1 reports that."
        ),
        weight=Weight.ADVISORY,
        depends_on=("QR1",),
        remedy="Correct the figure.",
    ),
    ScreeningCheck(
        code="QR4",
        area=Area.RELIEF,
        question="Does the relief match what the claim type normally seeks?",
        why=(
            "Money on an extension-of-time claim, or time on a payment claim, "
            "is not wrong, but it usually means the claim type is wrong or a "
            "second claim is hiding inside this one."
        ),
        condition=(
            "SATISFIED when the relief set matches the type groupings. "
            "INCOMPLETE, worded as a query rather than an error, when money is "
            "claimed on a type not in _MONEY_TYPES or time on a type not in "
            "_TIME_TYPES. Never blocking."
        ),
        weight=Weight.ADVISORY,
        depends_on=("QR1",),
        remedy="Check the claim type, or split the claim.",
    ),
    ScreeningCheck(
        code="QR5",
        area=Area.RELIEF,
        question="Is the basis of calculation stated?",
        why=(
            "An amount with no build-up behind it is a number, and it is the "
            "first thing a respondent will ask for."
        ),
        condition=(
            "Never fails. The system holds no field for a basis of "
            "calculation, so this cannot be computed and is stated as a "
            "prompt to the reader. "
            "If it is ever to be checked, the cleanest route is a claim issue "
            "of category quantum with evidence attached, which already exists, "
            "rather than a new field."
        ),
        weight=Weight.INFORMATIONAL,
    ),
)


# ---------------------------------------------------------------------------
# Claim stage — The Claim as submitted
#
# The second of the two obligations the standard forms impose. FIDIC 2017
# Sub-Clause 20.2.4 requires a fully detailed Claim within 84 days of
# awareness; the 1999 forms require one within 42 at Sub-Clause 20.1. It is not
# a notice and must not be checked as one — hence Area.SUBMISSION rather than
# Area.NOTICE.
#
# Two things make this area different from the notice checks it was split out
# of. The first is that a detailed Claim is routinely not yet due when a claim
# is screened, so "not on record" has to be told apart from "late"; the period
# is read from the requirement and compared against today rather than assumed
# to have run. The second is the consequence. Under 2017 the period is not a
# condition precedent, but failing to submit the sub-paragraph (b) statement of
# contractual basis within it makes the Notice of Claim lapse — a different
# mechanism, reported as LAPSED rather than BARRED, and a reversible one.
# ---------------------------------------------------------------------------

SUBMISSION_CHECKS: Tuple[ScreeningCheck, ...] = (
    ScreeningCheck(
        code="DC1",
        area=Area.SUBMISSION,
        question="Is a fully detailed Claim on record?",
        why=(
            "The Notice of Claim opens the claim; the fully detailed Claim is "
            "what the other party actually answers. A file holding only the "
            "notice is a claim that has been started and not made."
        ),
        condition=(
            "Over findings whose requirement obligation is DETAILED_CLAIM. "
            "NOT_APPLICABLE where none is registered for the edition. "
            "SATISFIED where none is NOT_GIVEN. INCOMPLETE otherwise, naming "
            "the clause and saying whether the period has expired — a Claim "
            "not yet due is outstanding work, not a failure."
        ),
        weight=Weight.ADVISORY,
        depends_on=("NC1", "NC2"),
        remedy=(
            "Record the detailed submission as correspondence, then assert it "
            "under the clause it was given under."
        ),
    ),
    ScreeningCheck(
        code="DC2",
        area=Area.SUBMISSION,
        question="Was the fully detailed Claim submitted within its period?",
        why=(
            "Late submission is not a time bar under these provisions, but it "
            "is the point at which the 2017 forms make the Notice of Claim "
            "lapse, and under any form it invites an argument about prejudice."
        ),
        condition=(
            "SATISFIED where no DETAILED_CLAIM finding is LATE. INCOMPLETE "
            "otherwise, naming the clause and the days late. Never BARRED: "
            "these provisions are not conditions precedent, and DC3 reports "
            "the lapse consequence where the contract provides one."
        ),
        weight=Weight.ADVISORY,
        depends_on=("NC1", "DC1"),
        remedy=(
            "Check for an agreed extension of the period before relying on the "
            "default: the forms allow another period where one is proposed and "
            "agreed."
        ),
    ),
    ScreeningCheck(
        code="DC3",
        area=Area.SUBMISSION,
        question="Has the period for stating the contractual basis expired?",
        why=(
            "FIDIC 2017 Sub-Clause 20.2.4 provides that if the statement of "
            "the contractual and/or other legal basis is not submitted within "
            "the period, the Notice of Claim is deemed to have lapsed and is "
            "no longer a valid Notice. It is the one consequence in this area "
            "that is adverse on the contract rather than on the state of the "
            "file, and it is worth finding before the evidence is assembled."
        ),
        condition=(
            "NOT_APPLICABLE where no DETAILED_CLAIM requirement carries a "
            "lapse_consequence — the 1999 and 1987 forms do not. SATISFIED "
            "where the period has not expired, naming the date, or where a "
            "detailed Claim is on record and a contractual basis is recorded. "
            "INCOMPLETE where a Claim is on record but no basis is recorded, "
            "because whether the submission contained the sub-paragraph (b) "
            "statement cannot be read from the record. LAPSED only where the "
            "period has expired and no detailed Claim is on record. The "
            "wording must state that the Engineer must give Notice of the "
            "lapse within 14 days and that absent such a Notice the Notice of "
            "Claim is deemed valid."
        ),
        weight=Weight.BLOCKING,
        depends_on=("NC1", "NC2"),
        remedy=(
            "Read Sub-Clause 20.2.4 and check whether the Engineer gave Notice "
            "of the lapse within 14 days. A computed lapse is a reason to take "
            "advice, not a determination."
        ),
    ),
)


# ---------------------------------------------------------------------------
# The checklist
# ---------------------------------------------------------------------------

#: Every check, in **evaluation** order — not the order they are shown in.
#:
#: ``screen_claim`` gates a check on results already computed, and skips a
#: dependency it has not reached yet (``by_code.get`` returns None). So a check
#: must run after everything in its ``depends_on``, and this tuple is ordered
#: to guarantee that. A test enforces it.
#:
#: Presentation order is a separate matter and is taken from the :class:`Area`
#: enum and :data:`STAGE_AREAS`, which follow the Event > Notice > Claim chain.
#: The two differ: EV3 compares the event date against the awareness date and
#: so depends on NC1, which means the Event stage is shown first but evaluated
#: after part of Notice. Reordering this tuple to match the screen would break
#: that gate silently.
CHECKS: Tuple[ScreeningCheck, ...] = (
    ENTITLEMENT_CHECKS
    + NOTICE_CHECKS
    + EVENT_CHECKS
    + RECORDS_CHECKS
    + RELIEF_CHECKS
    # Last: the submission checks gate on NC1, NC2 and each other.
    + SUBMISSION_CHECKS
)

CHECKS_BY_AREA: Mapping[Area, Tuple[ScreeningCheck, ...]] = {
    Area.EVENT: EVENT_CHECKS,
    Area.NOTICE: NOTICE_CHECKS,
    Area.SUBMISSION: SUBMISSION_CHECKS,
    Area.ENTITLEMENT: ENTITLEMENT_CHECKS,
    Area.RECORDS: RECORDS_CHECKS,
    Area.RELIEF: RELIEF_CHECKS,
}


class Outcome(str, Enum):
    """The result of screening as a whole.

    Four states, no score. A count of satisfied checks would be read as a
    probability of success, and this says nothing about the merits.
    """

    BARRED = "barred"
    """A notice was late under a condition precedent. Adverse on the contract
    rather than on the state of the file, and one a person needs to act on
    immediately."""

    LAPSED = "lapsed"
    """The period for the statement of contractual basis expired with no
    detailed Claim on record, so the Notice of Claim may have lapsed under
    FIDIC 2017 Sub-Clause 20.2.4. Also adverse on the contract, and ranked
    below BARRED only because it is the reversible one: the Engineer must give
    Notice of the lapse within 14 days, and absent that the Notice of Claim is
    deemed valid."""

    NOT_READY = "not_ready"
    """At least one blocking check is INCOMPLETE or INDETERMINATE. The claim
    cannot be meaningfully assessed until the named items are recorded. It says
    nothing about whether the claim is any good."""

    READY_WITH_QUERIES = "ready_with_queries"
    """No blocking failures; advisory items outstanding. The normal outcome for
    a claim that has just been raised properly."""

    READY = "ready"
    """Every applicable check satisfied. Still not a view on the merits."""


#: Precedence when several apply. BARRED first: it is the only outcome that
#: changes what someone should do today.
OUTCOME_PRECEDENCE: Tuple[Outcome, ...] = (
    Outcome.BARRED,
    Outcome.LAPSED,
    Outcome.NOT_READY,
    Outcome.READY_WITH_QUERIES,
    Outcome.READY,
)

OUTCOME_LABELS: Mapping[Outcome, str] = {
    Outcome.BARRED: "Possible time bar — read the provision",
    Outcome.LAPSED: "Notice of Claim may have lapsed — read the provision",
    Outcome.NOT_READY: "Not yet assessable",
    Outcome.READY_WITH_QUERIES: "Assessable, with queries",
    Outcome.READY: "Assessable",
}

#: Shown wherever an outcome is shown. The wording is part of the design, not
#: decoration: the prototype produced a verdict and people acted on it.
OUTCOME_CAVEAT = (
    "Screening reads what has been recorded about this claim. It is not a view "
    "on whether the claim succeeds, and a claim that passes every check may "
    "still fail on the contract or the facts."
)


# ---------------------------------------------------------------------------
# Open questions for review
#
# 1. EV5 (instruction identified) assumes a variation claim without an
#    identified instruction is worth querying. On a re-measurement contract
#    that may be wrong often enough to be noise. Keep, drop, or make it
#    conditional on something?
#
# 2. RC1 fails a claim raised today with nothing yet gathered. That is
#    accurate but may train people to ignore the panel on day one. An
#    alternative is INFORMATIONAL until the claim leaves draft status.
#
# 3. CB3 depends on clause detection over the published standard form, which
#    is imperfect on scanned editions. If false "not found" results are
#    common in practice it should be dropped rather than caveated.
#
# 4. Should screening be re-run and stored on each claim change, or computed
#    on demand like evidence-gaps and notice-compliance? On demand is simpler
#    and cannot go stale. Storing it allows "screened on, by" and a register
#    column that does not need every claim recomputed to render.
#
# 5. NC4 returns BARRED on the computed facts. Given the 1987 form is not
#    generally framed as a condition precedent, this will rarely fire on Red
#    Book 1987 projects and more often on 2017 ones. Confirm that is intended
#    rather than a gap in the registered requirements.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Inputs
#
# Everything the checks need, assembled by the caller. Framework-free: the
# service layer reads the ORM, this module reads only this dataclass, so the
# rules are testable without a database and stay runnable on Python 3.9.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScreeningInput:
    """A claim as recorded, plus the computations the checks delegate to.

    A value absent from the record must be absent here — None or empty, never
    a default that reads as recorded. The same discipline as
    ``build_claim_summary``: a screen that quietly supplies a missing date
    reports a compliance it has not established.
    """

    claim_type: str
    today: date

    # Area 1
    edition_code: str = ""
    edition_label: str = ""
    contractual_basis: Tuple[str, ...] = ()
    clauses_found: Tuple[str, ...] = ()
    """Entries of contractual_basis located in the published standard form."""
    knowledge_base_published: bool = False
    has_claimant: bool = False
    has_respondent: bool = False

    # Area 2 — computed elsewhere and passed in; never recomputed here.
    notice_requirements_registered: bool = False
    notice_findings: Tuple[ComplianceFinding, ...] = ()

    # Area 3
    event_date: Optional[date] = None
    awareness_date: Optional[date] = None
    description: str = ""
    instruction_linked: bool = False

    # Area 4
    evidence_count: int = 0
    document_count: int = 0
    required_records: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()
    """(element label, typical document types) for this claim type, from
    ``evidence_gaps.REQUIRED_ELEMENTS``. Passed in rather than imported so this
    module holds no second copy of the element list."""

    # Area 5
    amount_claimed: Optional[Decimal] = None
    currency: str = ""
    time_claimed_days: Optional[int] = None

    # -- The two obligations, kept apart ----------------------------------
    #
    # The standard forms impose two submissions in sequence and they fail
    # differently: a missing Notice of Claim is a notice failure, a missing
    # fully detailed Claim is a failure of the Claim itself. Screening used to
    # run every check over both, which reported the second as the first.

    @property
    def notice_of_claim_findings(self) -> Tuple[ComplianceFinding, ...]:
        """Findings for the first, short-period communication only."""
        return tuple(
            f
            for f in self.notice_findings
            if f.requirement.obligation is Obligation.NOTICE_OF_CLAIM
        )

    @property
    def detailed_claim_findings(self) -> Tuple[ComplianceFinding, ...]:
        """Findings for the fully detailed Claim that follows it."""
        return tuple(
            f
            for f in self.notice_findings
            if f.requirement.obligation is Obligation.DETAILED_CLAIM
        )


@dataclass(frozen=True)
class CheckResult:
    """One check, run."""

    check: ScreeningCheck
    status: CheckStatus
    detail: str = ""
    """What the record shows, in the words a reader needs. Never a bare
    restatement of the status."""

    items: Tuple[str, ...] = ()
    """The specific things named by the check — clauses not found, requirements
    with no notice, records this claim type will need."""

    @property
    def is_outstanding(self) -> bool:
        return self.status in (CheckStatus.INCOMPLETE, CheckStatus.INDETERMINATE)


@dataclass(frozen=True)
class ScreeningReport:
    """The result of screening one claim."""

    claim_type: str
    results: Tuple[CheckResult, ...]

    def by_code(self, code: str) -> CheckResult:
        for result in self.results:
            if result.check.code == code:
                return result
        raise KeyError(code)

    def for_area(self, area: Area) -> Tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.check.area is area)

    def for_stage(self, stage: Stage) -> Tuple[CheckResult, ...]:
        """Every result in the stage, in area order."""
        areas = STAGE_AREAS[stage]
        return tuple(r for area in areas for r in self.for_area(area))

    @property
    def barred(self) -> Tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.status is CheckStatus.BARRED)

    @property
    def lapsed(self) -> Tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.status is CheckStatus.LAPSED)

    @property
    def adverse(self) -> Tuple[CheckResult, ...]:
        """Everything adverse on the contract rather than on the state of the
        file. Both are lifted out of their stage on the screen."""
        return self.barred + self.lapsed

    @property
    def blocking_outstanding(self) -> Tuple[CheckResult, ...]:
        return tuple(
            r
            for r in self.results
            if r.is_outstanding and r.check.weight is Weight.BLOCKING
        )

    @property
    def advisory_outstanding(self) -> Tuple[CheckResult, ...]:
        return tuple(
            r
            for r in self.results
            if r.is_outstanding and r.check.weight is Weight.ADVISORY
        )

    @property
    def outcome(self) -> Outcome:
        if self.barred:
            return Outcome.BARRED
        if self.lapsed:
            return Outcome.LAPSED
        if self.blocking_outstanding:
            return Outcome.NOT_READY
        if self.advisory_outstanding:
            return Outcome.READY_WITH_QUERIES
        return Outcome.READY

    def summary(self) -> str:
        """One line. Counts what is outstanding; never scores the claim."""
        blocking = len(self.blocking_outstanding)
        advisory = len(self.advisory_outstanding)
        counts = "{} blocking and {} advisory item(s) outstanding.".format(
            blocking, advisory
        )
        if self.barred:
            clauses = ", ".join(sorted({i for r in self.barred for i in r.items}))
            return "Possible time bar under {}. {}".format(
                clauses or "a notice provision", counts
            )
        if self.lapsed:
            clauses = ", ".join(sorted({i for r in self.lapsed for i in r.items}))
            return "Notice of Claim may have lapsed under {}. {}".format(
                clauses or "the detailed claim provision", counts
            )
        return counts


# ---------------------------------------------------------------------------
# Evaluation
#
# One function per check, keyed by code. Each returns (status, detail, items)
# and must tolerate missing inputs: dependency gating runs after the evaluator,
# so an evaluator can be reached with the very input it depends on absent.
# ---------------------------------------------------------------------------

_Evaluation = Tuple[CheckStatus, str, Tuple[str, ...]]

#: Marks a detail produced by dependency gating rather than by a check itself.
_UNRESOLVED_PREFIX = "Cannot be determined until "

_SATISFIED: CheckStatus = CheckStatus.SATISFIED
_INCOMPLETE: CheckStatus = CheckStatus.INCOMPLETE
_INDETERMINATE: CheckStatus = CheckStatus.INDETERMINATE
_NOT_APPLICABLE: CheckStatus = CheckStatus.NOT_APPLICABLE


def _cb1(data: ScreeningInput) -> _Evaluation:
    if data.edition_code:
        return _SATISFIED, "Governed by {}.".format(
            data.edition_label or data.edition_code
        ), ()
    return (
        _INDETERMINATE,
        "The project has not declared which conditions of contract govern it, "
        "so no notice period can be computed and no standard-form text can be "
        "cited.",
        (),
    )


def _cb2(data: ScreeningInput) -> _Evaluation:
    clauses = tuple(c.strip() for c in data.contractual_basis if c.strip())
    if clauses:
        return _SATISFIED, "Relies on {}.".format(
            ", ".join("Clause " + c for c in clauses)
        ), clauses
    return (
        _INCOMPLETE,
        "No provision is named, so there is nothing to test the claim against.",
        (),
    )


def _cb3(data: ScreeningInput) -> _Evaluation:
    clauses = tuple(c.strip() for c in data.contractual_basis if c.strip())
    if not clauses:
        return _INCOMPLETE, "No provision is named.", ()
    if not data.knowledge_base_published:
        return (
            _INDETERMINATE,
            "No standard form is published for {}, so the clauses relied on "
            "cannot be looked up.".format(data.edition_label or data.edition_code),
            (),
        )
    missing = tuple(c for c in clauses if c not in data.clauses_found)
    if not missing:
        return _SATISFIED, "Every clause relied on was found in the published text.", ()
    return (
        _INCOMPLETE,
        "{} not found in the published text of {}. Clause detection over a "
        "scanned standard form is imperfect, so this means not found in the "
        "text held here, not absent from the contract.".format(
            ", ".join("Clause " + c for c in missing),
            data.edition_label or data.edition_code,
        ),
        missing,
    )


def _cb4(data: ScreeningInput) -> _Evaluation:
    missing = []
    if not data.has_claimant:
        missing.append("claimant")
    if not data.has_respondent:
        missing.append("respondent")
    if not missing:
        return _SATISFIED, "Both parties are recorded.", ()
    return (
        _INCOMPLETE,
        "No {} is recorded.".format(" or ".join(missing)),
        tuple(missing),
    )


def _nc1(data: ScreeningInput) -> _Evaluation:
    if data.awareness_date is not None:
        return _SATISFIED, "Periods run from {}.".format(
            data.awareness_date.isoformat()
        ), ()
    return (
        _INDETERMINATE,
        "No awareness date is recorded, so no notice period can be computed.",
        (),
    )


def _nc2(data: ScreeningInput) -> _Evaluation:
    if not data.edition_code:
        return (
            _INDETERMINATE,
            "No edition is declared, so no notice requirements can be looked up.",
            (),
        )
    if data.notice_requirements_registered:
        return _SATISFIED, "Notice requirements are registered for {}.".format(
            data.edition_label or data.edition_code
        ), ()
    return (
        _NOT_APPLICABLE,
        "No notice requirements are registered for {}. Periods from another "
        "edition are deliberately not substituted.".format(
            data.edition_label or data.edition_code or "this edition"
        ),
        (),
    )


def _nc3(data: ScreeningInput) -> _Evaluation:
    not_given = tuple(
        f.requirement.clause_number
        for f in data.notice_of_claim_findings
        if f.status is ComplianceStatus.NOT_GIVEN
    )
    if not not_given:
        return _SATISFIED, "A notice is on record for every requirement.", ()
    return (
        _INCOMPLETE,
        "No notice is on record under {}. Absence from the record is not proof "
        "that none was given.".format(
            ", ".join("Clause " + c for c in not_given)
        ),
        not_given,
    )


def _nc4(data: ScreeningInput) -> _Evaluation:
    barred = tuple(
        f.requirement.clause_number for f in data.notice_of_claim_findings if f.is_time_barred
    )
    if not barred:
        return _SATISFIED, "No notice is late under a condition precedent.", ()
    return (
        CheckStatus.BARRED,
        "Notice under {} was given late, and the provision is expressed as a "
        "condition precedent. Read the provision and its stated consequence: "
        "this is a reason to take advice, not a determination.".format(
            ", ".join("Clause " + c for c in barred)
        ),
        barred,
    )


def _nc5(data: ScreeningInput) -> _Evaluation:
    late = tuple(
        "Clause {} — {} day(s) late".format(f.requirement.clause_number, f.days_late)
        for f in data.notice_of_claim_findings
        if f.status is ComplianceStatus.LATE and not f.is_time_barred
    )
    if not late:
        return _SATISFIED, "No notice is late.", ()
    return (
        _INCOMPLETE,
        "Notice was late, under a provision not expressed as a condition "
        "precedent. The claim is not barred, but lateness invites an argument "
        "about prejudice.",
        late,
    )


def _nc6(data: ScreeningInput) -> _Evaluation:
    mismatched = []
    for finding in data.notice_of_claim_findings:
        notice = finding.notice
        required = finding.requirement.recipient
        if notice is None or not required or not notice.recipient:
            continue
        if required.lower() not in notice.recipient.lower():
            mismatched.append(
                "Clause {} names {}; the notice went to {}".format(
                    finding.requirement.clause_number, required, notice.recipient
                )
            )
    if not mismatched:
        return _SATISFIED, "Each notice went to the party the provision names.", ()
    return (
        _INCOMPLETE,
        "A notice went to a party other than the one the provision names.",
        tuple(mismatched),
    )


def _nc7(data: ScreeningInput) -> _Evaluation:
    missing = tuple(
        "Clause {} — {}".format(
            f.requirement.clause_number, f.notice.document_title or "notice"
        )
        for f in data.notice_of_claim_findings
        if f.notice is not None and f.notice.received_date is None
    )
    if not missing:
        return _SATISFIED, "A receipt date is recorded for each notice.", ()
    return (
        _INCOMPLETE,
        "No receipt date is recorded, so the sent date has been used. Most "
        "notice provisions turn on receipt.",
        missing,
    )


def _nc8(data: ScreeningInput) -> _Evaluation:
    unconfirmed = tuple(
        "Clause {} — {}".format(
            f.requirement.clause_number, f.notice.document_title or "notice"
        )
        for f in data.notice_of_claim_findings
        if f.notice is not None and not f.notice.is_confirmed_notice
    )
    if not unconfirmed:
        return _SATISFIED, "Each notice has been confirmed by a person.", ()
    return (
        _INCOMPLETE,
        "A notice relied on has not been confirmed as satisfying its provision. "
        "Whether a letter is a Notice is contested, and an unconfirmed "
        "assertion should not read as compliance.",
        unconfirmed,
    )


def _ev1(data: ScreeningInput) -> _Evaluation:
    if data.event_date is not None:
        return _SATISFIED, "The event is recorded as occurring on {}.".format(
            data.event_date.isoformat()
        ), ()
    return _INCOMPLETE, "No event date is recorded.", ()


def _ev2(data: ScreeningInput) -> _Evaluation:
    if data.description.strip():
        return _SATISFIED, "An account of the event is recorded.", ()
    return (
        _INCOMPLETE,
        "No account of the event is recorded. It is the claimant's own "
        "assertion, not evidence, but a reader assesses everything else "
        "against it.",
        (),
    )


def _ev3(data: ScreeningInput) -> _Evaluation:
    if data.event_date is None or data.awareness_date is None:
        return _INDETERMINATE, "Both dates are needed to compare them.", ()
    if data.awareness_date >= data.event_date:
        return _SATISFIED, "Awareness is recorded on or after the event.", ()
    return (
        _INCOMPLETE,
        "The awareness date ({}) is before the event date ({}). One of them is "
        "wrong, and every notice deadline has been computed from the "
        "earlier.".format(
            data.awareness_date.isoformat(), data.event_date.isoformat()
        ),
        (),
    )


def _ev4(data: ScreeningInput) -> _Evaluation:
    future = []
    if data.event_date is not None and data.event_date > data.today:
        future.append("event date {}".format(data.event_date.isoformat()))
    if data.awareness_date is not None and data.awareness_date > data.today:
        future.append("awareness date {}".format(data.awareness_date.isoformat()))
    if not future:
        return _SATISFIED, "The recorded dates are in the past.", ()
    return (
        _INCOMPLETE,
        "A recorded date is in the future, which produces negative deadlines.",
        tuple(future),
    )


def _ev5(data: ScreeningInput) -> _Evaluation:
    if data.claim_type not in _INSTRUCTED_TYPES:
        return (
            _NOT_APPLICABLE,
            "This claim type does not depend on an instruction.",
            (),
        )
    if data.instruction_linked:
        return _SATISFIED, "An instruction is linked to the claim.", ()
    return (
        _INCOMPLETE,
        "No instruction is linked. A claim of this type stands on a valid "
        "instruction from someone with authority to give it.",
        (),
    )


def _rc1(data: ScreeningInput) -> _Evaluation:
    total = data.evidence_count + data.document_count
    if total:
        return (
            _SATISFIED,
            "{} record(s) identified. Whether they establish the claim is "
            "assessed separately.".format(total),
            (),
        )
    return (
        _INCOMPLETE,
        "No record has been identified yet. Expected on a claim just raised; "
        "it says only that gathering has not begun.",
        (),
    )


def _rc2(data: ScreeningInput) -> _Evaluation:
    items = tuple(
        "{}: {}".format(label, ", ".join(d.replace("_", " ") for d in documents))
        for label, documents in data.required_records
        if documents
    )
    return (
        _SATISFIED,
        "What a claim of this type is normally evidenced by. Worth obtaining "
        "now, while contemporaneous records still exist.",
        items,
    )


def _rc3(data: ScreeningInput) -> _Evaluation:
    return (
        _SATISFIED,
        "Not computed, and deliberately so. Whether a record was made at the "
        "time depends on when it was actually made, which is not held here: a "
        "document date is the date printed on it, and an upload date is when "
        "someone got round to it. Judge contemporaneity by reading the record.",
        (),
    )


def _qr1(data: ScreeningInput) -> _Evaluation:
    has_time = data.time_claimed_days is not None
    has_money = data.amount_claimed is not None
    if has_time or has_money:
        parts = []
        if has_time:
            parts.append("{} day(s)".format(data.time_claimed_days))
        if has_money:
            parts.append("{} {}".format(data.currency or "", data.amount_claimed).strip())
        return _SATISFIED, "Claims {}.".format(" and ".join(parts)), ()

    if data.claim_type in _TIME_TYPES and data.claim_type in _MONEY_TYPES:
        expected = "time, money, or both"
    elif data.claim_type in _TIME_TYPES:
        expected = "an extension of time"
    elif data.claim_type in _MONEY_TYPES:
        expected = "an amount"
    else:
        expected = "time or money"
    return (
        _INCOMPLETE,
        "No relief is stated. A claim of this type normally seeks {}.".format(expected),
        (),
    )


def _qr2(data: ScreeningInput) -> _Evaluation:
    if data.amount_claimed is None:
        return _NOT_APPLICABLE, "No amount is claimed.", ()
    if data.currency.strip():
        return _SATISFIED, "Amount claimed in {}.".format(data.currency), ()
    return (
        _INCOMPLETE,
        "An amount is claimed with no currency, so it cannot be totalled "
        "across the register or stated in a determination.",
        (),
    )


def _qr3(data: ScreeningInput) -> _Evaluation:
    bad = []
    if data.amount_claimed is not None and data.amount_claimed <= 0:
        bad.append("amount claimed is {}".format(data.amount_claimed))
    if data.time_claimed_days is not None and data.time_claimed_days <= 0:
        bad.append("time claimed is {} day(s)".format(data.time_claimed_days))
    if data.amount_claimed is None and data.time_claimed_days is None:
        return _NOT_APPLICABLE, "No relief is stated.", ()
    if not bad:
        return _SATISFIED, "The relief claimed is a positive quantity.", ()
    return _INCOMPLETE, "Zero or negative relief is a data-entry error.", tuple(bad)


def _qr4(data: ScreeningInput) -> _Evaluation:
    queries = []
    if data.amount_claimed is not None and data.claim_type not in _MONEY_TYPES:
        queries.append("money is claimed on a claim type that does not normally seek it")
    if data.time_claimed_days is not None and data.claim_type not in _TIME_TYPES:
        queries.append("time is claimed on a claim type that does not normally seek it")
    if data.amount_claimed is None and data.time_claimed_days is None:
        return _NOT_APPLICABLE, "No relief is stated.", ()
    if not queries:
        return _SATISFIED, "The relief matches what this claim type seeks.", ()
    return (
        _INCOMPLETE,
        "Not an error, but it usually means the claim type is wrong or a "
        "second claim is hiding inside this one.",
        tuple(queries),
    )


def _qr5(data: ScreeningInput) -> _Evaluation:
    return (
        _SATISFIED,
        "Not computed: no field holds a basis of calculation. Record it as a "
        "claim issue of category quantum, with the build-up attached as "
        "evidence.",
        (),
    )


def _clauses(findings: Tuple[ComplianceFinding, ...]) -> str:
    """"Clause 20.2.4", or a list of them."""
    return ", ".join(
        "Clause " + c
        for c in sorted({f.requirement.clause_number for f in findings})
    )


def _dc1(data: ScreeningInput) -> _Evaluation:
    findings = data.detailed_claim_findings
    if not findings:
        return (
            _NOT_APPLICABLE,
            "No fully detailed Claim requirement is registered for {}.".format(
                data.edition_label or data.edition_code or "this edition"
            ),
            (),
        )

    missing = tuple(f for f in findings if f.status is ComplianceStatus.NOT_GIVEN)
    if not missing:
        return _SATISFIED, "A fully detailed Claim is on record under {}.".format(
            _clauses(findings)
        ), ()

    # Not yet due is not the same as late, and saying so is the difference
    # between a deadline a person can work to and a failure they cannot undo.
    items = []
    overdue = False
    for finding in missing:
        clause = "Clause " + finding.requirement.clause_number
        if finding.deadline is None:
            items.append("{} — period not computed".format(clause))
        elif finding.deadline < data.today:
            overdue = True
            items.append(
                "{} — was due {}".format(clause, finding.deadline.isoformat())
            )
        else:
            items.append("{} — due {}".format(clause, finding.deadline.isoformat()))

    return (
        _INCOMPLETE,
        (
            "No fully detailed Claim is on record and the period has expired. "
            "Absence from the record is not proof that none was submitted."
            if overdue
            else "No fully detailed Claim is on record yet. The period has not "
            "expired, so this is work outstanding rather than a failure."
        ),
        tuple(items),
    )


def _dc2(data: ScreeningInput) -> _Evaluation:
    findings = data.detailed_claim_findings
    if not findings:
        return (
            _NOT_APPLICABLE,
            "No fully detailed Claim requirement is registered for {}.".format(
                data.edition_label or data.edition_code or "this edition"
            ),
            (),
        )

    late = tuple(
        "Clause {} — {} day(s) late".format(f.requirement.clause_number, f.days_late)
        for f in findings
        if f.status is ComplianceStatus.LATE
    )
    if not late:
        return _SATISFIED, "The fully detailed Claim was submitted in time.", ()
    return (
        _INCOMPLETE,
        "The fully detailed Claim was submitted outside its period. These "
        "provisions are not conditions precedent, so this does not bar the "
        "claim, but check whether a longer period was proposed and agreed.",
        late,
    )


def _dc3(data: ScreeningInput) -> _Evaluation:
    # Only where the contract states a lapse consequence. The 1999 and 1987
    # forms do not, and inventing one for them would be the kind of
    # cross-edition substitution ADR 0004 exists to prevent.
    findings = tuple(
        f for f in data.detailed_claim_findings if f.requirement.lapse_consequence
    )
    if not findings:
        return (
            _NOT_APPLICABLE,
            "{} states no lapse consequence for a late statement of "
            "contractual basis.".format(
                data.edition_label or data.edition_code or "This edition"
            ),
            (),
        )

    expired = tuple(
        f for f in findings if f.deadline is not None and f.deadline < data.today
    )
    if not expired:
        due = sorted(f.deadline for f in findings if f.deadline is not None)
        return _SATISFIED, (
            "The period for the statement of contractual basis has not expired{}.".format(
                " — it runs to " + due[0].isoformat() if due else ""
            )
        ), ()

    on_record = tuple(
        f for f in expired if f.status is not ComplianceStatus.NOT_GIVEN
    )
    if on_record:
        if data.contractual_basis:
            return _SATISFIED, (
                "A fully detailed Claim is on record and a contractual basis is "
                "recorded against the claim."
            ), ()
        return (
            _INCOMPLETE,
            "A fully detailed Claim is on record but no contractual basis is "
            "recorded against the claim. Whether the submission contained the "
            "statement required by sub-paragraph (b) cannot be read from the "
            "record, and it is that statement the lapse provision turns on.",
            tuple(_clauses(on_record).split(", ")),
        )

    return (
        CheckStatus.LAPSED,
        "The period under {} expired on {} with no fully detailed Claim on "
        "record. On these facts the Notice of Claim is deemed to have lapsed "
        "and is no longer a valid Notice. The Engineer must give Notice of "
        "that within 14 days; if none was given, the Notice of Claim is deemed "
        "valid after all. This is not a determination.".format(
            _clauses(expired),
            min(f.deadline for f in expired if f.deadline is not None).isoformat(),
        ),
        tuple(f.requirement.clause_number for f in expired),
    )


_EVALUATORS = {
    "CB1": _cb1, "CB2": _cb2, "CB3": _cb3, "CB4": _cb4,
    "NC1": _nc1, "NC2": _nc2, "NC3": _nc3, "NC4": _nc4,
    "NC5": _nc5, "NC6": _nc6, "NC7": _nc7, "NC8": _nc8,
    "EV1": _ev1, "EV2": _ev2, "EV3": _ev3, "EV4": _ev4, "EV5": _ev5,
    "RC1": _rc1, "RC2": _rc2, "RC3": _rc3,
    "QR1": _qr1, "QR2": _qr2, "QR3": _qr3, "QR4": _qr4, "QR5": _qr5,
    "DC1": _dc1, "DC2": _dc2, "DC3": _dc3,
}


def screen_claim(data: ScreeningInput) -> ScreeningReport:
    """Run every applicable check against a claim as recorded.

    Checks run in declared order. A check whose dependency is unresolved is
    INDETERMINATE rather than failed, and one whose dependency does not apply
    does not apply either — so a missing awareness date produces one honest
    "cannot be determined" rather than a column of failures that all have the
    same cause.

    Args:
        data: The claim as recorded, with the computations the checks delegate
            to already performed.

    Returns:
        One result per check, including those that do not apply. A check that
        silently vanishes reads as a screen that forgot to look.
    """
    results: List[CheckResult] = []
    by_code: dict = {}

    for check in CHECKS:
        if check.applies_to and data.claim_type not in check.applies_to:
            result = CheckResult(
                check=check,
                status=CheckStatus.NOT_APPLICABLE,
                detail="Does not apply to a claim of this type.",
            )
            results.append(result)
            by_code[check.code] = result
            continue

        status, detail, items = _EVALUATORS[check.code](data)

        # Dependency gating. Applied after the evaluator so a check that is
        # genuinely inapplicable says so rather than blaming a dependency.
        if status is not CheckStatus.NOT_APPLICABLE:
            for code in check.depends_on:
                dependency = by_code.get(code)
                if dependency is None:
                    continue
                if dependency.status is CheckStatus.NOT_APPLICABLE:
                    status, items = CheckStatus.NOT_APPLICABLE, ()
                    detail = dependency.detail
                    break
                if dependency.is_outstanding:
                    status, items = CheckStatus.INDETERMINATE, ()
                    # Point at the root cause. Re-wrapping an already-wrapped
                    # message produces "cannot be determined until X, which
                    # cannot be determined until Y", which buries the one fact
                    # the reader needs.
                    detail = (
                        dependency.detail
                        if dependency.detail.startswith(_UNRESOLVED_PREFIX)
                        else "{}{} is resolved: {}".format(
                            _UNRESOLVED_PREFIX, dependency.check.code, dependency.detail
                        )
                    )
                    break

        result = CheckResult(check=check, status=status, detail=detail, items=items)
        results.append(result)
        by_code[check.code] = result

    return ScreeningReport(claim_type=data.claim_type, results=tuple(results))
