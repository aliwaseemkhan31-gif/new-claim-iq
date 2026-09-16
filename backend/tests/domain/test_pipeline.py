"""Tests for the ingestion pipeline state machine.

The three properties that justify modelling the pipeline as data rather than a
function: resumability, idempotency, and inspectability.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from claimiq.core.domain.errors import ProcessingError, ValidationError
from claimiq.ingestion.domain.pipeline import (
    CONDITIONAL_STAGES,
    STAGE_ORDER,
    PipelineState,
    Stage,
    StageStatus,
    stages_after,
    validate_stage_order,
)

VERSION = "11111111-2222-3333-4444-555555555555"


def state(**kwargs) -> PipelineState:
    return PipelineState(document_version_id=VERSION, **kwargs)


def run_to(pipeline: PipelineState, target: Stage) -> None:
    """Advance the pipeline through every applicable stage up to and including target."""
    for stage in STAGE_ORDER:
        if not pipeline.is_required(stage):
            pipeline.skip(stage, reason="not required")
        else:
            pipeline.start(stage)
            pipeline.complete(stage)
        if stage is target:
            return


# ---------------------------------------------------------------------------
# Definition integrity
# ---------------------------------------------------------------------------


def test_stage_order_is_internally_consistent() -> None:
    """Catches a stage added to the enum but never wired into the order."""
    assert validate_stage_order() == []


def test_every_stage_appears_exactly_once() -> None:
    assert len(STAGE_ORDER) == len(set(STAGE_ORDER)) == len(list(Stage))


def test_validation_runs_before_anything_touches_the_file() -> None:
    assert STAGE_ORDER[0] is Stage.VALIDATE


def test_ocr_follows_analysis() -> None:
    """OCR is conditional on what analysis found, so it cannot precede it."""
    assert STAGE_ORDER.index(Stage.ANALYSE) < STAGE_ORDER.index(Stage.OCR)


def test_chunking_follows_clause_detection() -> None:
    """Chunk boundaries depend on detected clauses."""
    assert STAGE_ORDER.index(Stage.DETECT_CLAUSES) < STAGE_ORDER.index(Stage.CHUNK)


def test_embedding_follows_chunking() -> None:
    assert STAGE_ORDER.index(Stage.CHUNK) < STAGE_ORDER.index(Stage.EMBED)


def test_stages_after_excludes_itself() -> None:
    following = stages_after(Stage.CHUNK)
    assert Stage.CHUNK not in following
    assert Stage.EMBED in following


# ---------------------------------------------------------------------------
# Conditional stages
# ---------------------------------------------------------------------------


def test_ocr_is_required_before_analysis_has_decided() -> None:
    """Assuming no OCR before looking is how scanned appendices end up blank."""
    pipeline = state()
    pipeline.requires_ocr = True
    assert pipeline.is_required(Stage.OCR) is True


def test_ocr_not_required_for_a_digital_document() -> None:
    pipeline = state(requires_ocr=False)
    assert pipeline.is_required(Stage.OCR) is False
    assert Stage.OCR not in pipeline.applicable_stages


def test_skipped_is_distinct_from_completed() -> None:
    """Skipped OCR means no scanned pages; completed means zero pages OCR'd."""
    pipeline = state(requires_ocr=False)
    pipeline.start(Stage.VALIDATE)
    pipeline.complete(Stage.VALIDATE)
    pipeline.start(Stage.ANALYSE)
    pipeline.complete(Stage.ANALYSE)
    pipeline.start(Stage.EXTRACT_TEXT)
    pipeline.complete(Stage.EXTRACT_TEXT)
    pipeline.skip(Stage.OCR, reason="no scanned pages")

    outcome = pipeline.outcome(Stage.OCR)
    assert outcome.status is StageStatus.SKIPPED
    assert outcome.is_terminal is True
    assert outcome.metrics["skip_reason"] == "no scanned pages"


def test_non_conditional_stage_cannot_be_skipped() -> None:
    with pytest.raises(ValidationError):
        state().skip(Stage.CHUNK)


def test_conditional_stages_are_ocr_tables_and_embedding() -> None:
    assert CONDITIONAL_STAGES == {Stage.OCR, Stage.EXTRACT_TABLES, Stage.EMBED}


def test_embedding_is_skipped_when_no_provider_is_configured() -> None:
    """A deployment with no embedding model genuinely cannot embed.

    The honest outcome is a document that is extracted, structured and
    lexically searchable, with vector retrieval reported unavailable — not a
    hard failure, and not a silent success that leaves the document invisible
    to vector search while claiming to be fully processed.
    """
    pipeline = state(requires_ocr=False, requires_table_extraction=False)
    assert pipeline.embedding_available is False
    assert Stage.EMBED not in pipeline.applicable_stages

    run_to(pipeline, Stage.VALIDATE_QUALITY)
    assert pipeline.is_complete is True
    assert pipeline.is_retrievable is True, "lexical retrieval works"
    assert pipeline.is_vector_searchable is False, "vector retrieval does not"


def test_embedding_runs_when_a_provider_is_available() -> None:
    pipeline = state(
        requires_ocr=False, requires_table_extraction=False, embedding_available=True
    )
    assert Stage.EMBED in pipeline.applicable_stages

    run_to(pipeline, Stage.VALIDATE_QUALITY)
    assert pipeline.is_vector_searchable is True


# ---------------------------------------------------------------------------
# Ordering enforcement
# ---------------------------------------------------------------------------


def test_stages_cannot_start_out_of_order() -> None:
    """A task dispatched out of order is a real failure mode with retries."""
    with pytest.raises(ProcessingError) as exc:
        state().start(Stage.CHUNK)
    assert exc.value.details["blocked_by"] == Stage.VALIDATE.value


def test_start_succeeds_once_predecessors_complete() -> None:
    pipeline = state(requires_ocr=False, requires_table_extraction=False)
    run_to(pipeline, Stage.DETECT_CLAUSES)
    pipeline.skip(Stage.EXTRACT_TABLES, reason="none found")
    pipeline.start(Stage.ASSEMBLE_SECTIONS)
    assert pipeline.status(Stage.ASSEMBLE_SECTIONS) is StageStatus.RUNNING


def test_skipped_predecessor_does_not_block() -> None:
    pipeline = state(requires_ocr=False)
    pipeline.start(Stage.VALIDATE)
    pipeline.complete(Stage.VALIDATE)
    pipeline.start(Stage.ANALYSE)
    pipeline.complete(Stage.ANALYSE)
    pipeline.start(Stage.EXTRACT_TEXT)
    pipeline.complete(Stage.EXTRACT_TEXT)
    pipeline.start(Stage.CLASSIFY_PAGES)  # OCR skipped implicitly by not being required
    assert pipeline.status(Stage.CLASSIFY_PAGES) is StageStatus.RUNNING


def test_inapplicable_stage_cannot_be_started() -> None:
    pipeline = state(requires_ocr=False)
    pipeline.start(Stage.VALIDATE)
    pipeline.complete(Stage.VALIDATE)
    pipeline.start(Stage.ANALYSE)
    pipeline.complete(Stage.ANALYSE)
    pipeline.start(Stage.EXTRACT_TEXT)
    pipeline.complete(Stage.EXTRACT_TEXT)
    with pytest.raises(ProcessingError):
        pipeline.start(Stage.OCR)


# ---------------------------------------------------------------------------
# Idempotency — acks_late means tasks are redelivered
# ---------------------------------------------------------------------------


def test_completing_twice_is_a_noop() -> None:
    """A redelivered task legitimately re-reports success."""
    pipeline = state()
    pipeline.start(Stage.VALIDATE)
    first = pipeline.complete(Stage.VALIDATE, metrics={"checks": 6})
    completed_at = first.completed_at

    second = pipeline.complete(Stage.VALIDATE, metrics={"checks": 999})
    assert second.completed_at == completed_at
    assert second.metrics["checks"] == 6, "the second report must not overwrite the first"


def test_next_stage_skips_already_completed_work() -> None:
    pipeline = state(requires_ocr=False)
    pipeline.start(Stage.VALIDATE)
    pipeline.complete(Stage.VALIDATE)
    assert pipeline.next_stage() is Stage.ANALYSE


def test_attempts_are_counted_across_retries() -> None:
    pipeline = state()
    pipeline.start(Stage.VALIDATE)
    pipeline.fail(Stage.VALIDATE, error_code="x", error_message="y")
    pipeline.reset_for_retry(Stage.VALIDATE)
    pipeline.start(Stage.VALIDATE)
    assert pipeline.outcome(Stage.VALIDATE).attempts == 2


# ---------------------------------------------------------------------------
# Resumability
# ---------------------------------------------------------------------------


def test_resume_point_is_the_stage_that_was_running_when_the_worker_died() -> None:
    pipeline = state(requires_ocr=True)
    pipeline.start(Stage.VALIDATE)
    pipeline.complete(Stage.VALIDATE)
    pipeline.start(Stage.ANALYSE)
    pipeline.complete(Stage.ANALYSE)
    pipeline.start(Stage.EXTRACT_TEXT)
    pipeline.complete(Stage.EXTRACT_TEXT)
    pipeline.start(Stage.OCR)  # worker dies here

    assert pipeline.resume_point() is Stage.OCR


def test_checkpoint_survives_a_failure_so_ocr_resumes_partway() -> None:
    """The 500-page scan that fails at page 400 must not restart at page 1."""
    pipeline = state(requires_ocr=True)
    run_to(pipeline, Stage.EXTRACT_TEXT)
    pipeline.start(Stage.OCR)
    pipeline.checkpoint(Stage.OCR, last_page=400, total_pages=500)
    pipeline.fail(
        Stage.OCR,
        error_code="ocr_failed",
        error_message="engine crashed",
        checkpoint={"last_page": 400, "total_pages": 500},
    )

    assert pipeline.resume_point() is Stage.OCR
    assert pipeline.outcome(Stage.OCR).checkpoint["last_page"] == 400


def test_completing_a_stage_clears_its_checkpoint() -> None:
    pipeline = state()
    pipeline.start(Stage.VALIDATE)
    pipeline.checkpoint(Stage.VALIDATE, partial=True)
    pipeline.complete(Stage.VALIDATE)
    assert pipeline.outcome(Stage.VALIDATE).checkpoint == {}


def test_reset_from_clears_later_stages_only() -> None:
    """A new embedding model invalidates EMBED onward, not the expensive OCR."""
    pipeline = state(
        requires_ocr=True, requires_table_extraction=True, embedding_available=True
    )
    run_to(pipeline, Stage.INDEX)

    pipeline.reset_from(Stage.EMBED)

    assert pipeline.status(Stage.OCR) is StageStatus.COMPLETED
    assert pipeline.status(Stage.CHUNK) is StageStatus.COMPLETED
    assert pipeline.status(Stage.EMBED) is StageStatus.PENDING
    assert pipeline.status(Stage.INDEX) is StageStatus.PENDING
    assert pipeline.next_stage() is Stage.EMBED


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


def test_failure_blocks_the_pipeline_rather_than_skipping_past_it() -> None:
    """Chunking a document whose extraction failed produces chunks of nothing."""
    pipeline = state(requires_ocr=False)
    pipeline.start(Stage.VALIDATE)
    pipeline.complete(Stage.VALIDATE)
    pipeline.start(Stage.ANALYSE)
    pipeline.fail(Stage.ANALYSE, error_code="extraction_failed", error_message="bad pdf")

    assert pipeline.is_failed is True
    assert pipeline.next_stage() is None
    assert pipeline.failed_stage is Stage.ANALYSE


def test_retry_clears_the_failure() -> None:
    pipeline = state()
    pipeline.start(Stage.VALIDATE)
    pipeline.fail(Stage.VALIDATE, error_code="x", error_message="y")
    pipeline.reset_for_retry(Stage.VALIDATE)

    assert pipeline.is_failed is False
    assert pipeline.next_stage() is Stage.VALIDATE


def test_cannot_retry_a_stage_that_did_not_fail() -> None:
    with pytest.raises(ProcessingError):
        state().reset_for_retry(Stage.VALIDATE)


def test_cancellation_stops_the_pipeline() -> None:
    pipeline = state()
    pipeline.cancel()
    assert pipeline.next_stage() is None
    with pytest.raises(ProcessingError):
        pipeline.start(Stage.VALIDATE)


# ---------------------------------------------------------------------------
# Inspectability
# ---------------------------------------------------------------------------


def test_progress_reflects_applicable_stages_only() -> None:
    """A digital document must not sit at 92% because it skipped OCR."""
    pipeline = state(requires_ocr=False, requires_table_extraction=False)
    run_to(pipeline, Stage.VALIDATE_QUALITY)
    assert pipeline.progress_percent == 100
    assert pipeline.is_complete is True


def test_progress_increases_monotonically() -> None:
    pipeline = state(requires_ocr=False, requires_table_extraction=False)
    seen = [pipeline.progress_percent]
    for stage in pipeline.applicable_stages:
        pipeline.start(stage)
        pipeline.complete(stage)
        seen.append(pipeline.progress_percent)
    assert seen == sorted(seen)
    assert seen[-1] == 100


def test_readable_before_retrievable() -> None:
    """A large document becomes useful before embedding finishes."""
    pipeline = state(requires_ocr=False, requires_table_extraction=False)
    run_to(pipeline, Stage.CLASSIFY_PAGES)
    assert pipeline.is_readable is True
    assert pipeline.is_retrievable is False

    run_to(pipeline, Stage.INDEX)
    assert pipeline.is_retrievable is True


def test_describe_names_the_current_stage() -> None:
    pipeline = state(requires_ocr=False)
    assert "validate" in pipeline.describe()

    pipeline.start(Stage.VALIDATE)
    assert "running validate" in pipeline.describe()

    pipeline.fail(Stage.VALIDATE, error_code="file_safety_rejected", error_message="exe")
    assert "failed at validate" in pipeline.describe()
    assert "file_safety_rejected" in pipeline.describe()


def test_describe_reports_cancellation() -> None:
    pipeline = state()
    pipeline.cancel()
    assert pipeline.describe() == "cancelled"


def test_duration_is_recorded() -> None:
    pipeline = state()
    start = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 3, 1, 12, 0, 30, tzinfo=timezone.utc)
    pipeline.start(Stage.VALIDATE, now=start)
    pipeline.complete(Stage.VALIDATE, now=end)
    assert pipeline.outcome(Stage.VALIDATE).duration_seconds == 30.0


# ---------------------------------------------------------------------------
# Serialisation — state is persisted on ProcessingJob and rebuilt every run
# ---------------------------------------------------------------------------


def test_state_round_trips_through_json() -> None:
    pipeline = state(requires_ocr=True)
    run_to(pipeline, Stage.EXTRACT_TEXT)
    pipeline.start(Stage.OCR)
    pipeline.checkpoint(Stage.OCR, last_page=120)

    restored = PipelineState.from_dict(pipeline.to_dict())

    assert restored.document_version_id == VERSION
    assert restored.requires_ocr is True
    assert restored.status(Stage.EXTRACT_TEXT) is StageStatus.COMPLETED
    assert restored.status(Stage.OCR) is StageStatus.RUNNING
    assert restored.outcome(Stage.OCR).checkpoint["last_page"] == 120
    assert restored.resume_point() is Stage.OCR


def test_timestamps_survive_the_round_trip() -> None:
    pipeline = state()
    start = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
    pipeline.start(Stage.VALIDATE, now=start)
    restored = PipelineState.from_dict(pipeline.to_dict())
    assert restored.outcome(Stage.VALIDATE).started_at == start


def test_unknown_stage_in_stored_state_is_ignored() -> None:
    """A stage removed in a later release must not make old jobs unreadable."""
    payload = state().to_dict()
    payload["outcomes"]["a_stage_we_deleted"] = {"status": "completed"}
    restored = PipelineState.from_dict(payload)
    assert restored.status(Stage.VALIDATE) is StageStatus.PENDING


def test_corrupt_status_falls_back_to_pending() -> None:
    payload = state().to_dict()
    payload["outcomes"]["validate"]["status"] = "nonsense"
    restored = PipelineState.from_dict(payload)
    assert restored.status(Stage.VALIDATE) is StageStatus.PENDING


def test_empty_payload_produces_a_fresh_state() -> None:
    restored = PipelineState.from_dict({})
    assert restored.next_stage() is Stage.VALIDATE
    assert restored.progress_percent == 0


# ---------------------------------------------------------------------------
# Progress within a stage
#
# Whole-stage progress does not move while a few hundred scanned pages are read,
# which reads as a hung job. The counter the checkpoint already keeps is the
# honest way to show movement, rather than a finer invented percentage.
# ---------------------------------------------------------------------------


def test_stage_detail_is_empty_when_nothing_is_running() -> None:
    assert state().stage_detail() == ""


def test_stage_detail_counts_ocr_pages_against_the_analysed_total() -> None:
    pipeline = state(requires_ocr=True)
    for stage in STAGE_ORDER:
        if stage is Stage.OCR:
            break
        if not pipeline.is_required(stage):
            pipeline.skip(stage, reason="not required")
            continue
        pipeline.start(stage)
        if stage is Stage.ANALYSE:
            pipeline.complete(stage, metrics={"pages_needing_ocr": 294})
        else:
            pipeline.complete(stage)

    pipeline.start(Stage.OCR)
    assert pipeline.stage_detail() == "0 of 294 page(s) read"

    pipeline.checkpoint(Stage.OCR, pages_done=152, last_completed_page=152)
    assert pipeline.stage_detail() == "152 of 294 page(s) read"


def test_stage_detail_omits_the_total_when_the_analysis_did_not_record_one() -> None:
    pipeline = state(requires_ocr=True)
    for stage in STAGE_ORDER:
        if stage is Stage.OCR:
            break
        if not pipeline.is_required(stage):
            pipeline.skip(stage, reason="not required")
            continue
        pipeline.start(stage)
        pipeline.complete(stage)

    pipeline.start(Stage.OCR)
    pipeline.checkpoint(Stage.OCR, pages_done=7)
    assert pipeline.stage_detail() == "7 page(s) read"
