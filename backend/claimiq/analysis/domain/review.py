"""Human review of AI findings.

Implements ADR 0006 at the level of a single finding: a person accepts, rejects
or amends what the AI concluded, and the AI's original statement is never
overwritten. Reviews are an append-only history; the current state is simply
the latest entry.

That separation is the point. If a reviewer edits a finding in place, nobody can
later tell which words were the model's and which were the reviewer's — and the
difference between what the AI concluded and what a professional concluded is
both the most important provenance fact in a claim file and the only honest
measure of the AI's accuracy.

Pure stdlib; runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Sequence

from claimiq.core.domain.errors import ValidationError


class ReviewAction(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    AMEND = "amend"


class ReviewState(str, Enum):
    UNREVIEWED = "unreviewed"
    """Rendered distinctly. An unreviewed finding must never read as settled."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    AMENDED = "amended"


_STATE_FOR_ACTION = {
    ReviewAction.ACCEPT: ReviewState.ACCEPTED,
    ReviewAction.REJECT: ReviewState.REJECTED,
    ReviewAction.AMEND: ReviewState.AMENDED,
}

#: A reason shorter than this is not a reason. "no" and "wrong" tell a later
#: reader nothing about why a professional disagreed with the AI.
MIN_REASON_CHARS = 10


@dataclass(frozen=True)
class ReviewEntry:
    action: ReviewAction
    reviewer_id: str
    reviewed_at: datetime
    reason: str = ""
    amended_statement: str = ""


@dataclass(frozen=True)
class ReviewOutcome:
    state: ReviewState
    original_statement: str
    effective_statement: str
    """What a report should show: the amended text if amended, otherwise the
    original. A rejected finding keeps its original text so it can be shown as
    rejected rather than disappearing."""

    latest: ReviewEntry | None
    history_length: int

    @property
    def is_reviewed(self) -> bool:
        return self.state is not ReviewState.UNREVIEWED


def validate_review(
    action: str,
    *,
    reason: str = "",
    amended_statement: str = "",
    original_statement: str = "",
) -> ReviewAction:
    """Validate a review before it is recorded.

    Accepting needs no reason. Rejecting and amending do: disagreeing with the
    AI is the event whose reasoning a later reader most needs. An amendment must
    actually change the statement — an "amendment" identical to the original is
    an acceptance recorded under the wrong name.

    Raises:
        ValidationError: with a field-level ``details`` entry.
    """
    try:
        parsed = ReviewAction(str(action).strip().lower())
    except ValueError:
        raise ValidationError(
            f"Unknown review action {action!r}.",
            details={"field": "action", "valid": [a.value for a in ReviewAction]},
        ) from None

    reason = (reason or "").strip()
    amended = (amended_statement or "").strip()

    if parsed in (ReviewAction.REJECT, ReviewAction.AMEND) and len(reason) < MIN_REASON_CHARS:
        raise ValidationError(
            f"A reason of at least {MIN_REASON_CHARS} characters is required to "
            f"{parsed.value} a finding.",
            details={"field": "reason"},
        )

    if parsed is ReviewAction.AMEND:
        if not amended:
            raise ValidationError(
                "An amended statement is required to amend a finding.",
                details={"field": "amended_statement"},
            )
        if " ".join(amended.split()) == " ".join((original_statement or "").split()):
            raise ValidationError(
                "The amended statement is identical to the original. Accept the "
                "finding instead.",
                details={"field": "amended_statement"},
            )

    return parsed


def resolve_review(original_statement: str, entries: Sequence[ReviewEntry]) -> ReviewOutcome:
    """Current review state of a finding from its history.

    The latest entry wins; entries with the same timestamp resolve to the one
    recorded later in ``entries``.
    """
    if not entries:
        return ReviewOutcome(
            state=ReviewState.UNREVIEWED,
            original_statement=original_statement,
            effective_statement=original_statement,
            latest=None,
            history_length=0,
        )

    indexed = list(enumerate(entries))
    _, latest = max(indexed, key=lambda item: (item[1].reviewed_at, item[0]))
    state = _STATE_FOR_ACTION[latest.action]
    effective = (
        latest.amended_statement.strip()
        if latest.action is ReviewAction.AMEND and latest.amended_statement.strip()
        else original_statement
    )
    return ReviewOutcome(
        state=state,
        original_statement=original_statement,
        effective_statement=effective,
        latest=latest,
        history_length=len(entries),
    )
