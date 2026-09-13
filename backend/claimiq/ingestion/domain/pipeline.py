"""Ingestion pipeline definition and state machine.

The pipeline is modelled as data — an ordered list of stages, each with a
recorded outcome — rather than as a function that runs top to bottom. That
choice buys three properties that a straight-line function cannot offer:

1. **Resumability.** A 500-page scanned contract set can fail at page 400 of
   the OCR stage. Re-running from the beginning wastes hours of GPU time, so
   the state machine resumes from the last completed stage.

2. **Idempotency.** Celery is configured with ``acks_late``, so a worker crash
   re-queues the task. A stage that has already completed must be a no-op on
   the second run, or a retry duplicates every chunk in the document.

3. **Inspectability.** An operator asking "why has this document been
   processing for twenty minutes" gets a stage name and a progress figure,
   not a spinner.

Stage *execution* lives in the service layer, which has the database and the
providers. This module owns only the ordering, the transitions and the rules
about what may run when — so all of that is testable without any of it.

Pure stdlib; runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

from claimiq.core.domain.errors import ProcessingError, ValidationError


class Stage(str, Enum):
    """A pipeline stage. Declaration order is execution order."""

    VALIDATE = "validate"
    """Upload safety. Runs before any parser opens the file."""

    ANALYSE = "analyse"
    """Page count, per-page digital-vs-scanned classification."""

    EXTRACT_TEXT = "extract_text"
    """Digital text extraction for pages that have a text layer."""

    OCR = "ocr"
    """Conditional: only for pages ANALYSE marked as scanned."""

    CLASSIFY_PAGES = "classify_pages"
    """Content pages vs contents/index/front matter."""

    DETECT_CLAUSES = "detect_clauses"
    """Headings, hierarchy, cross-references."""

    EXTRACT_TABLES = "extract_tables"
    """Conditional: only where ANALYSE found table structures."""

    ASSEMBLE_SECTIONS = "assemble_sections"
    """Persist the clause hierarchy as DocumentSection rows."""

    CHUNK = "chunk"
    """Structure-aware chunking."""

    EMBED = "embed"
    """Dense vectors for each chunk."""

    INDEX = "index"
    """Populate tsvector for lexical retrieval."""

    VALIDATE_QUALITY = "validate_quality"
    """Score the extraction and flag low-confidence documents."""


#: Execution order. Explicit rather than relying on enum declaration order,
#: because reordering an enum by accident should not silently reorder the
#: pipeline.
STAGE_ORDER: tuple[Stage, ...] = (
    Stage.VALIDATE,
    Stage.ANALYSE,
    Stage.EXTRACT_TEXT,
    Stage.OCR,
    Stage.CLASSIFY_PAGES,
    Stage.DETECT_CLAUSES,
    Stage.EXTRACT_TABLES,
    Stage.ASSEMBLE_SECTIONS,
    Stage.CHUNK,
    Stage.EMBED,
    Stage.INDEX,
    Stage.VALIDATE_QUALITY,
)

#: Stages that run only when the document requires them, or when the capability
#: they depend on is available in this deployment.
#:
#: EMBED is conditional on an embedding provider being configured and usable.
#: A deployment with no embedding model provisioned genuinely cannot embed, and
#: the honest outcome is a document that is extracted, structured, chunked and
#: lexically searchable, with vector retrieval reported as unavailable — not a
#: hard failure, and not a silent success that leaves the document invisible to
#: vector search while claiming to be fully processed.
CONDITIONAL_STAGES: frozenset[Stage] = frozenset(
    {Stage.OCR, Stage.EXTRACT_TABLES, Stage.EMBED}
)

#: Stages after which the document has enough structure to be read by a user,
#: even though retrieval is not yet available. Surfaced in the UI so a large
#: document becomes useful before embedding finishes.
READABLE_AFTER = Stage.CLASSIFY_PAGES

#: Stages after which retrieval works.
RETRIEVABLE_AFTER = Stage.INDEX


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    """Conditional stage that was not required. Distinct from COMPLETED: a
    skipped OCR stage means the document had no scanned pages, which is
    different from having OCR'd zero pages successfully."""

    FAILED = "failed"


@dataclass
class StageOutcome:
    """The recorded result of one stage."""

    stage: Stage
    status: StageStatus = StageStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str = ""
    error_message: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    checkpoint: dict[str, Any] = field(default_factory=dict)
    """Stage-internal progress, e.g. ``{"last_page": 400}``. Lets a long stage
    resume partway rather than restarting."""

    attempts: int = 0

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None or self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()

    @property
    def is_terminal(self) -> bool:
        return self.status in (StageStatus.COMPLETED, StageStatus.SKIPPED)


@dataclass
class PipelineState:
    """The full processing state of one document version.

    Persisted on ``ProcessingJob`` and reconstructed on every task run, so a
    worker picking up a retried task knows exactly what has already been done.
    """

    document_version_id: str
    outcomes: dict[Stage, StageOutcome] = field(default_factory=dict)
    requires_ocr: bool = False
    requires_table_extraction: bool = False
    embedding_available: bool = False
    """Whether an embedding provider is configured and usable in this
    deployment. When False, EMBED is skipped and the document is lexically
    searchable but not vector-searchable."""

    cancelled: bool = False

    def __post_init__(self) -> None:
        for stage in STAGE_ORDER:
            self.outcomes.setdefault(stage, StageOutcome(stage=stage))

    # -- queries ---------------------------------------------------------

    def outcome(self, stage: Stage) -> StageOutcome:
        return self.outcomes[stage]

    def status(self, stage: Stage) -> StageStatus:
        return self.outcomes[stage].status

    def is_required(self, stage: Stage) -> bool:
        """Whether ``stage`` applies to this document.

        Conditional stages depend on what ANALYSE found. Before ANALYSE has
        run, they are treated as required — assuming a document needs no OCR
        before looking at it is how scanned appendices end up blank.
        """
        if stage is Stage.OCR:
            return self.requires_ocr
        if stage is Stage.EXTRACT_TABLES:
            return self.requires_table_extraction
        if stage is Stage.EMBED:
            return self.embedding_available
        return True

    @property
    def applicable_stages(self) -> tuple[Stage, ...]:
        return tuple(s for s in STAGE_ORDER if self.is_required(s))

    @property
    def completed_stages(self) -> tuple[Stage, ...]:
        return tuple(s for s in STAGE_ORDER if self.outcomes[s].is_terminal)

    @property
    def failed_stage(self) -> Stage | None:
        for stage in STAGE_ORDER:
            if self.outcomes[stage].status is StageStatus.FAILED:
                return stage
        return None

    @property
    def running_stage(self) -> Stage | None:
        for stage in STAGE_ORDER:
            if self.outcomes[stage].status is StageStatus.RUNNING:
                return stage
        return None

    @property
    def is_complete(self) -> bool:
        return all(self.outcomes[s].is_terminal for s in self.applicable_stages)

    @property
    def is_failed(self) -> bool:
        return self.failed_stage is not None

    @property
    def is_readable(self) -> bool:
        """True once the document can be shown to a user."""
        return self._reached(READABLE_AFTER)

    @property
    def is_retrievable(self) -> bool:
        """True once the document participates in lexical retrieval."""
        return self._reached(RETRIEVABLE_AFTER)

    @property
    def is_vector_searchable(self) -> bool:
        """True once the document participates in vector retrieval.

        Distinct from :attr:`is_retrievable`. A deployment without an embedding
        provider produces documents that are fully searchable lexically and not
        at all by similarity, and the UI must be able to say so precisely
        rather than implying both work.
        """
        return (
            self.embedding_available
            and self.outcomes[Stage.EMBED].status is StageStatus.COMPLETED
            and self.is_retrievable
        )

    def _reached(self, marker: Stage) -> bool:
        index = STAGE_ORDER.index(marker)
        return all(
            self.outcomes[s].is_terminal
            for s in STAGE_ORDER[: index + 1]
            if self.is_required(s)
        )

    @property
    def progress_percent(self) -> int:
        """Whole-percent progress over applicable stages.

        Coarse by design. A finer figure derived from checkpoints would move
        unevenly — OCR dominates wall-clock time on a scanned set — and a
        progress bar that stalls at 40% for ten minutes is worse than one that
        moves in visible steps.
        """
        applicable = self.applicable_stages
        if not applicable:
            return 0
        done = sum(1 for s in applicable if self.outcomes[s].is_terminal)
        return int(round(100 * done / len(applicable)))

    def next_stage(self) -> Stage | None:
        """The next stage to execute, or None when there is nothing to do.

        Returns None when the pipeline is complete, cancelled, or blocked by a
        failure. A failed pipeline does not silently skip past the failure —
        chunking a document whose text extraction failed produces chunks of
        nothing.
        """
        if self.cancelled or self.is_failed:
            return None
        for stage in STAGE_ORDER:
            if not self.is_required(stage):
                continue
            if self.outcomes[stage].is_terminal:
                continue
            return stage
        return None

    def resume_point(self) -> Stage | None:
        """Where a retry should restart.

        A stage left RUNNING by a crashed worker restarts from itself, using
        its checkpoint if it recorded one. Its partial work is re-done, which
        is safe because every stage is idempotent.
        """
        running = self.running_stage
        if running is not None:
            return running
        failed = self.failed_stage
        if failed is not None:
            return failed
        return self.next_stage()

    # -- transitions -----------------------------------------------------

    def start(self, stage: Stage, *, now: datetime | None = None) -> StageOutcome:
        """Mark ``stage`` as running.

        Raises:
            ProcessingError: if an earlier required stage has not completed.
                Order is enforced here rather than trusted, because a task
                dispatched out of order is a real failure mode with retries.
        """
        self._require_not_cancelled()
        if not self.is_required(stage):
            raise ProcessingError(
                f"Stage {stage.value!r} does not apply to this document.",
                details={"stage": stage.value},
            )

        for earlier in STAGE_ORDER[: STAGE_ORDER.index(stage)]:
            if self.is_required(earlier) and not self.outcomes[earlier].is_terminal:
                raise ProcessingError(
                    f"Cannot start {stage.value!r}: {earlier.value!r} has not completed.",
                    details={"stage": stage.value, "blocked_by": earlier.value},
                )

        outcome = self.outcomes[stage]
        outcome.status = StageStatus.RUNNING
        outcome.started_at = now or _utcnow()
        outcome.completed_at = None
        outcome.error_code = ""
        outcome.error_message = ""
        outcome.attempts += 1
        return outcome

    def complete(
        self,
        stage: Stage,
        *,
        metrics: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> StageOutcome:
        """Mark ``stage`` completed. Idempotent.

        Completing an already-completed stage is a no-op rather than an error:
        with ``acks_late`` a redelivered task legitimately re-reports success,
        and treating that as a failure would fail healthy pipelines.
        """
        outcome = self.outcomes[stage]
        if outcome.status is StageStatus.COMPLETED:
            return outcome
        outcome.status = StageStatus.COMPLETED
        outcome.completed_at = now or _utcnow()
        outcome.error_code = ""
        outcome.error_message = ""
        outcome.checkpoint = {}
        if metrics:
            outcome.metrics.update(metrics)
        return outcome

    def skip(self, stage: Stage, *, reason: str = "", now: datetime | None = None) -> StageOutcome:
        """Mark a conditional stage as not required."""
        if stage not in CONDITIONAL_STAGES:
            raise ValidationError(
                f"Stage {stage.value!r} is not conditional and cannot be skipped.",
                details={"stage": stage.value, "conditional": [s.value for s in CONDITIONAL_STAGES]},
            )
        outcome = self.outcomes[stage]
        outcome.status = StageStatus.SKIPPED
        outcome.completed_at = now or _utcnow()
        if reason:
            outcome.metrics["skip_reason"] = reason
        return outcome

    def fail(
        self,
        stage: Stage,
        *,
        error_code: str,
        error_message: str,
        checkpoint: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> StageOutcome:
        """Record a stage failure.

        ``checkpoint`` preserves how far the stage got, so a retry resumes
        rather than restarting.
        """
        outcome = self.outcomes[stage]
        outcome.status = StageStatus.FAILED
        outcome.completed_at = now or _utcnow()
        outcome.error_code = error_code
        outcome.error_message = error_message
        if checkpoint:
            outcome.checkpoint = dict(checkpoint)
        return outcome

    def checkpoint(self, stage: Stage, **values: Any) -> None:
        """Record intra-stage progress for a long-running stage."""
        self.outcomes[stage].checkpoint.update(values)

    def reset_for_retry(self, stage: Stage) -> None:
        """Clear a failure so the stage can run again."""
        outcome = self.outcomes[stage]
        if outcome.status is not StageStatus.FAILED:
            raise ProcessingError(
                f"Stage {stage.value!r} is not failed and cannot be retried.",
                details={"stage": stage.value, "status": outcome.status.value},
            )
        outcome.status = StageStatus.PENDING
        outcome.error_code = ""
        outcome.error_message = ""
        outcome.completed_at = None

    def reset_from(self, stage: Stage) -> None:
        """Clear ``stage`` and everything after it.

        Used when reprocessing with changed configuration — a new embedding
        model invalidates EMBED and INDEX but not the extracted text, so
        re-running the whole pipeline would waste the expensive OCR stage.
        """
        for later in STAGE_ORDER[STAGE_ORDER.index(stage) :]:
            self.outcomes[later] = StageOutcome(stage=later)

    def cancel(self) -> None:
        self.cancelled = True

    def _require_not_cancelled(self) -> None:
        if self.cancelled:
            raise ProcessingError(
                "The processing job has been cancelled.",
                details={"document_version_id": self.document_version_id},
            )

    # -- serialisation ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise for JSONB storage on ProcessingJob."""
        return {
            "document_version_id": self.document_version_id,
            "requires_ocr": self.requires_ocr,
            "requires_table_extraction": self.requires_table_extraction,
            "embedding_available": self.embedding_available,
            "cancelled": self.cancelled,
            "outcomes": {
                stage.value: {
                    "status": outcome.status.value,
                    "started_at": _iso(outcome.started_at),
                    "completed_at": _iso(outcome.completed_at),
                    "error_code": outcome.error_code,
                    "error_message": outcome.error_message,
                    "metrics": outcome.metrics,
                    "checkpoint": outcome.checkpoint,
                    "attempts": outcome.attempts,
                }
                for stage, outcome in self.outcomes.items()
            },
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PipelineState:
        """Rebuild from stored JSON.

        Unknown stage keys are ignored rather than raising: a stage removed in
        a later release must not make historical jobs unreadable.
        """
        state = cls(
            document_version_id=str(payload.get("document_version_id", "")),
            requires_ocr=bool(payload.get("requires_ocr", False)),
            requires_table_extraction=bool(payload.get("requires_table_extraction", False)),
            embedding_available=bool(payload.get("embedding_available", False)),
            cancelled=bool(payload.get("cancelled", False)),
        )
        stored = payload.get("outcomes") or {}
        for key, raw in stored.items():
            try:
                stage = Stage(key)
            except ValueError:
                continue
            state.outcomes[stage] = StageOutcome(
                stage=stage,
                status=_status(raw.get("status")),
                started_at=_parse(raw.get("started_at")),
                completed_at=_parse(raw.get("completed_at")),
                error_code=str(raw.get("error_code", "")),
                error_message=str(raw.get("error_message", "")),
                metrics=dict(raw.get("metrics") or {}),
                checkpoint=dict(raw.get("checkpoint") or {}),
                attempts=int(raw.get("attempts", 0)),
            )
        return state

    def describe(self) -> str:
        """One-line human summary for logs and the UI."""
        if self.cancelled:
            return "cancelled"
        failed = self.failed_stage
        if failed is not None:
            return f"failed at {failed.value}: {self.outcomes[failed].error_code}"
        if self.is_complete:
            return "complete"
        running = self.running_stage
        if running is not None:
            return f"running {running.value} ({self.progress_percent}%)"
        nxt = self.next_stage()
        return f"pending {nxt.value} ({self.progress_percent}%)" if nxt else "idle"


def stages_after(stage: Stage) -> tuple[Stage, ...]:
    """Stages that follow ``stage``, exclusive."""
    return STAGE_ORDER[STAGE_ORDER.index(stage) + 1 :]


def validate_stage_order(order: Sequence[Stage] = STAGE_ORDER) -> list[str]:
    """Check the declared order for internal consistency.

    Run as a test. Catches a stage added to the enum but not to STAGE_ORDER,
    which would otherwise silently never execute.
    """
    problems: list[str] = []
    declared = set(order)
    for stage in Stage:
        if stage not in declared:
            problems.append(f"Stage {stage.value!r} is missing from STAGE_ORDER")
    if len(order) != len(declared):
        problems.append("STAGE_ORDER contains duplicates")
    for conditional in CONDITIONAL_STAGES:
        if conditional not in declared:
            problems.append(f"Conditional stage {conditional.value!r} is not in STAGE_ORDER")
    return problems


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _status(value: Any) -> StageStatus:
    try:
        return StageStatus(value)
    except ValueError:
        return StageStatus.PENDING
