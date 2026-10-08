"""Reading a project's contract for amended notice periods.

The standard form says 28 days; the Particular Conditions may say 14. Where
they differ the Particular Conditions govern, and a notice check that does not
know is checking the wrong contract.

This module reads passages of the project's contract documents and proposes
amendments. It proposes; it does not apply. A period read off a scanned page,
or out of a sentence that mentions two periods, is a reading, and the deadline
it would move is the one most claims turn on. Every suggestion carries the
passage it came from so a person can check it in seconds.

Deliberately not a model call. The question is narrow — which number of days
sits next to which clause — and a deterministic reading can show its working.

Pure stdlib; must run on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from claimiq.claims.domain.notice_compliance import (
    AmendmentAction,
    DayCount,
    NoticeRequirement,
    Obligation,
)

#: Document types whose text *amends* the standard form. Only these are read
#: for a clause they mention but are not filed under: a general-conditions
#: passage citing "Sub-Clause 53.1" in passing is not an amendment of it.
AMENDING_DOCUMENT_TYPES = frozenset(
    {"particular_conditions", "amendment", "addendum", "contract_agreement"}
)

#: Document types read at all. Specifications, drawings and the like are not.
CONTRACT_DOCUMENT_TYPES = AMENDING_DOCUMENT_TYPES | frozenset(
    {"conditions_of_contract", "general_conditions", "schedule"}
)

_NUMBER_WORDS: Dict[str, int] = {
    "seven": 7,
    "ten": 10,
    "fourteen": 14,
    "fifteen": 15,
    "twenty": 20,
    "twenty-one": 21,
    "twenty one": 21,
    "twenty-eight": 28,
    "twenty eight": 28,
    "thirty": 30,
    "forty-two": 42,
    "forty two": 42,
    "forty-five": 45,
    "forty five": 45,
    "fifty-six": 56,
    "fifty six": 56,
    "sixty": 60,
    "seventy": 70,
    "eighty-four": 84,
    "eighty four": 84,
    "ninety": 90,
}

_WORD_ALTERNATION = "|".join(
    sorted((re.escape(w) for w in _NUMBER_WORDS), key=len, reverse=True)
)

#: "14 days", "14 (fourteen) days", "14 calendar days", "14 working days".
_DIGIT_PERIOD = re.compile(
    r"\b(?P<n>\d{1,3})\s*(?:\(\s*[a-z][a-z\- ]*\)\s*)?"
    r"(?P<kind>calendar\s+|working\s+|business\s+)?days?\b",
    re.IGNORECASE,
)

#: "fourteen days", "fourteen (14) days", "fourteen working days".
_WORD_PERIOD = re.compile(
    r"\b(?P<w>" + _WORD_ALTERNATION + r")\s*(?:\(\s*\d{1,3}\s*\)\s*)?"
    r"(?P<kind>calendar\s+|working\s+|business\s+)?days?\b",
    re.IGNORECASE,
)

#: A passage must be about notices or claims to be read for periods at all.
#: Contract documents are full of "within 30 days" about other things.
_TOPIC = re.compile(r"notic|notif|claim|particular|account|submi", re.IGNORECASE)

_DELETION = re.compile(r"\b(?:is|shall be|are)\s+deleted\b|\bdelete\s+(?:sub-?\s*clause|clause)\b", re.IGNORECASE)

#: A page heading that marks the Particular Conditions: "Part II", "Conditions
#: of Particular Application" (1987), "Particular Conditions" (1999, 2017).
PARTICULAR_HEADING = re.compile(
    r"part[\s-]*ii\b|particular\s+(?:conditions|application)|\bCOPA\b", re.IGNORECASE
)


def is_particular_conditions_page(text: str) -> bool:
    return PARTICULAR_HEADING.search(text[:600]) is not None


#: How far back from a candidate period to look for the standard period it
#: replaces ("delete 28 days and substitute 14 days").
_LOOKBACK = 160


@dataclass(frozen=True)
class Passage:
    """A piece of a contract document, as ingestion chunked it."""

    document_id: str
    document_title: str
    document_type: str
    text: str
    clause_number: str = ""
    page: Optional[int] = None
    amending: bool = False
    """Set for a page that sits under a Particular Conditions heading inside a
    document filed as something broader — a whole contract set filed as
    "Conditions of Contract" still has a Part II."""

    @property
    def is_amending(self) -> bool:
        return self.amending or self.document_type in AMENDING_DOCUMENT_TYPES


@dataclass(frozen=True)
class Period:
    days: int
    day_count: DayCount
    position: int


@dataclass(frozen=True)
class Suggestion:
    """A proposed amendment, with the passage that supports it."""

    clause_number: str
    obligation: Obligation
    action: AmendmentAction
    period_days: Optional[int]
    day_count: Optional[DayCount]
    standard_period_days: int
    document_id: str
    document_title: str
    page: Optional[int]
    excerpt: str
    note: str = ""
    late_consequence: Optional[str] = None


def periods_in(text: str) -> List[Period]:
    """Every period of days stated in ``text``, in order of appearance."""
    found: Dict[int, Period] = {}
    for match in _DIGIT_PERIOD.finditer(text):
        days = int(match.group("n"))
        found[match.start()] = Period(days, _kind(match.group("kind")), match.start())
    for match in _WORD_PERIOD.finditer(text):
        days = _NUMBER_WORDS.get(re.sub(r"\s+", " ", match.group("w").lower()))
        if days is None:
            continue
        # A "fourteen (14) days" is one period, not two.
        if not any(abs(p.position - match.start()) < 24 and p.days == days for p in found.values()):
            found[match.start()] = Period(days, _kind(match.group("kind")), match.start())
    return sorted((p for p in found.values() if 0 < p.days <= 365), key=lambda p: p.position)


def _kind(raw: Optional[str]) -> DayCount:
    if raw and raw.strip().lower() in ("working", "business"):
        return DayCount.WORKING
    return DayCount.CALENDAR


def mentions_clause(text: str, clause: str) -> bool:
    """Whether ``text`` cites ``clause``, and not a sub-provision of it."""
    pattern = r"(?:sub-?\s*clause|clause)\s+" + re.escape(clause) + r"(?![\d.]*\d)"
    return re.search(pattern, text, re.IGNORECASE) is not None


def _relevant(passage: Passage, clause: str) -> bool:
    if not _TOPIC.search(passage.text):
        return False
    if passage.clause_number.strip() == clause:
        return True
    return passage.is_amending and mentions_clause(passage.text, clause)


def _tokens(days: int) -> List[str]:
    words = [w for w, n in _NUMBER_WORDS.items() if n == days]
    return [str(days)] + words


def _closest_replaced(
    text: str, period: Period, group: Sequence[NoticeRequirement]
) -> Optional[NoticeRequirement]:
    """The standard requirement whose period is cited just before ``period``.

    "Delete '28 days' and substitute '14 days'": the 14 replaces whichever
    requirement says 28. Closest preceding mention wins.
    """
    window = text[max(0, period.position - _LOOKBACK) : period.position].lower()
    best: Tuple[int, Optional[NoticeRequirement]] = (-1, None)
    for requirement in group:
        for token in _tokens(requirement.period_days):
            for match in re.finditer(r"\b" + re.escape(token) + r"\b", window):
                if match.start() > best[0]:
                    best = (match.start(), requirement)
    return best[1]


def _excerpt(text: str, position: int, width: int = 360) -> str:
    start = max(0, position - width // 2)
    piece = " ".join(text[start : start + width].split())
    return ("…" if start else "") + piece + ("…" if start + width < len(text) else "")


def suggest_amendments(
    standard: Sequence[NoticeRequirement], passages: Sequence[Passage]
) -> List[Suggestion]:
    """Amendments the passages appear to make to ``standard``.

    Only differences are proposed. A contract that restates the standard form
    (as most do, in full) yields nothing, which is the right answer: there is
    nothing to confirm.
    """
    groups: Dict[str, List[NoticeRequirement]] = {}
    for requirement in standard:
        groups.setdefault(requirement.clause_number, []).append(requirement)

    suggestions: Dict[tuple, Suggestion] = {}
    for clause, group in groups.items():
        standard_periods = {r.period_days for r in group}
        for passage in passages:
            if not _relevant(passage, clause):
                continue

            periods = periods_in(passage.text)
            if not periods:
                if passage.is_amending and _DELETION.search(passage.text) and mentions_clause(
                    passage.text, clause
                ):
                    for requirement in group:
                        key = (clause, requirement.obligation)
                        suggestions.setdefault(
                            key,
                            Suggestion(
                                clause_number=clause,
                                obligation=requirement.obligation,
                                action=AmendmentAction.REMOVE,
                                period_days=None,
                                day_count=None,
                                standard_period_days=requirement.period_days,
                                document_id=passage.document_id,
                                document_title=passage.document_title,
                                page=passage.page,
                                excerpt=_excerpt(passage.text, 0),
                                note="The passage appears to delete this provision.",
                            ),
                        )
                continue

            for period in periods:
                same_number = period.days in standard_periods
                if same_number and period.day_count is DayCount.CALENDAR:
                    continue  # restates the standard form

                target = _closest_replaced(passage.text, period, group)
                note = ""
                if target is None:
                    if len(group) == 1:
                        target = group[0]
                    else:
                        target = next(
                            (r for r in group if r.obligation is Obligation.NOTICE_OF_CLAIM),
                            group[0],
                        )
                        note = (
                            f"Clause {clause} imposes more than one period, and "
                            f"which one this changes could not be read from the "
                            f"passage. Check before confirming."
                        )
                if same_number and target.period_days != period.days:
                    continue  # the other requirement's standard period

                key = (clause, target.obligation)
                if key in suggestions:
                    continue
                suggestions[key] = Suggestion(
                    clause_number=clause,
                    obligation=target.obligation,
                    action=AmendmentAction.AMEND,
                    period_days=period.days,
                    day_count=period.day_count,
                    standard_period_days=target.period_days,
                    document_id=passage.document_id,
                    document_title=passage.document_title,
                    page=passage.page,
                    excerpt=_excerpt(passage.text, period.position),
                    note=note,
                )
    _consequence_deletions(standard, passages, suggestions)
    return list(suggestions.values())


def _consequence_deletions(standard, passages, suggestions) -> None:
    """Notice where the contract deletes the clause stating a consequence.

    FIDIC 1987 Sub-Clause 53.4 limits recovery for a late notice to what the
    contemporary records verify. Particular Conditions sometimes delete it
    outright ("This Sub-Clause is deleted in its entirety"), and a check that
    still quotes it states a consequence the contract has removed.
    """
    for requirement in standard:
        clause = requirement.consequence_clause
        if not clause:
            continue
        pattern = re.compile(
            r"\b" + re.escape(clause) + r"(?![\d.]*\d)\b.{0,120}?"
            r"\b(?:is|are|shall\s+be|stands)\s+deleted",
            re.IGNORECASE | re.DOTALL,
        )
        for passage in passages:
            if not passage.is_amending:
                continue
            text = " ".join(passage.text.split())
            match = pattern.search(text)
            if match is None:
                continue
            key = (requirement.clause_number, requirement.obligation, "consequence")
            if key in suggestions:
                break
            suggestions[key] = Suggestion(
                clause_number=requirement.clause_number,
                obligation=requirement.obligation,
                action=AmendmentAction.AMEND,
                period_days=None,
                day_count=None,
                standard_period_days=requirement.period_days,
                document_id=passage.document_id,
                document_title=passage.document_title,
                page=passage.page,
                excerpt=_excerpt(text, match.start()),
                note=(
                    f"Sub-Clause {clause}, which states what follows from a late "
                    f"submission under Clause {requirement.clause_number}, appears "
                    f"to be deleted. The period is unchanged."
                ),
                late_consequence=(
                    f"Sub-Clause {clause} is deleted by this contract's Particular "
                    f"Conditions, so the limitation it states does not apply. "
                    f"Lateness may still be argued under the general law."
                ),
            )
            break
