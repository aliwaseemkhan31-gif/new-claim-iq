"""Processing job persistence.

A job is an entity, not a Celery task id. The prototype ran OCR and inference
inline in the request, so a long ingest had no representation at all: no
status, no progress, nothing to retry, and a blocked HTTP worker for the
duration.
"""
from __future__ import annotations

from django.db import models
from django.utils import timezone

from claimiq.core.models import BaseModel
from claimiq.documents.models import DocumentVersion
from claimiq.ingestion.domain.pipeline import PipelineState, Stage


class JobStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class JobKind(models.TextChoices):
    INGEST = "ingest", "Document ingestion"
    REPROCESS = "reprocess", "Reprocess document"
    REEMBED = "reembed", "Regenerate embeddings"


class ProcessingJob(BaseModel):
    """One run of the ingestion pipeline over a document version.

    ``state`` holds the serialised :class:`PipelineState`. Everything about
    where the job got to lives there; the columns alongside it exist so the
    common queries (list running jobs, find failures) are indexed rather than
    requiring a JSONB scan.
    """

    document_version = models.ForeignKey(
        DocumentVersion, on_delete=models.CASCADE, related_name="jobs"
    )
    kind = models.CharField(max_length=16, choices=JobKind.choices, default=JobKind.INGEST)
    status = models.CharField(
        max_length=16, choices=JobStatus.choices, default=JobStatus.QUEUED, db_index=True
    )

    current_stage = models.CharField(max_length=32, blank=True, db_index=True)
    progress_percent = models.PositiveSmallIntegerField(default=0)

    state = models.JSONField(
        default=dict,
        blank=True,
        help_text="Serialised PipelineState. The authoritative record of progress.",
    )

    error_code = models.CharField(max_length=64, blank=True, db_index=True)
    error_message = models.TextField(
        blank=True,
        help_text=(
            "Operator-facing failure reason. Surfaced in the UI as a job "
            "failure; never returned as an answer or fed to a model."
        ),
    )

    celery_task_id = models.CharField(max_length=255, blank=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    cancel_requested = models.BooleanField(
        default=False,
        help_text=(
            "Cooperative cancellation. A running stage checks this between "
            "units of work; there is no way to interrupt a stage mid-page, and "
            "killing the worker would leave the state machine inconsistent."
        ),
    )

    class Meta:
        db_table = "ingestion_processing_job"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["document_version", "-created_at"]),
        ]
        constraints = [
            # One active job per version. Two workers ingesting the same
            # document would duplicate every chunk.
            models.UniqueConstraint(
                fields=["document_version"],
                condition=models.Q(status__in=["queued", "running"]),
                name="uniq_active_job_per_version",
            )
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.document_version_id} ({self.status})"

    # -- state bridge ----------------------------------------------------

    def load_state(self) -> PipelineState:
        """Rebuild the domain state machine from the stored JSON."""
        if not self.state:
            return PipelineState(document_version_id=str(self.document_version_id))
        return PipelineState.from_dict(self.state)

    def save_state(self, pipeline: PipelineState, *, commit: bool = True) -> None:
        """Persist the state machine and mirror its summary onto the columns."""
        self.state = pipeline.to_dict()
        self.progress_percent = pipeline.progress_percent

        running = pipeline.running_stage or pipeline.next_stage()
        self.current_stage = running.value if running else ""

        failed = pipeline.failed_stage
        if pipeline.cancelled:
            self.status = JobStatus.CANCELLED
            self.finished_at = self.finished_at or timezone.now()
        elif failed is not None:
            outcome = pipeline.outcome(failed)
            self.status = JobStatus.FAILED
            self.error_code = outcome.error_code[:64]
            self.error_message = outcome.error_message
            self.finished_at = timezone.now()
        elif pipeline.is_complete:
            self.status = JobStatus.COMPLETED
            self.error_code = ""
            self.error_message = ""
            self.finished_at = timezone.now()
        else:
            self.status = JobStatus.RUNNING
            self.started_at = self.started_at or timezone.now()

        if commit:
            self.save(
                update_fields=[
                    "state",
                    "progress_percent",
                    "current_stage",
                    "status",
                    "error_code",
                    "error_message",
                    "started_at",
                    "finished_at",
                    "updated_at",
                ]
            )

    @property
    def is_terminal(self) -> bool:
        return self.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.finished_at or timezone.now()
        return (end - self.started_at).total_seconds()


class StageLog(BaseModel):
    """An append-only record of each stage execution.

    Separate from ``ProcessingJob.state``, which holds only the *current*
    state. When a stage is retried three times, the state shows the final
    outcome; this shows all three attempts and how long each took. That is what
    makes "why is ingestion slow on this deployment" answerable.
    """

    job = models.ForeignKey(ProcessingJob, on_delete=models.CASCADE, related_name="logs")
    stage = models.CharField(max_length=32, db_index=True)
    attempt = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=16)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    metrics = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "ingestion_stage_log"
        ordering = ["created_at"]
        indexes = [models.Index(fields=["job", "created_at"])]

    def __str__(self) -> str:
        return f"{self.stage} attempt {self.attempt}: {self.status}"


def stage_choices() -> list[tuple[str, str]]:
    """Stage values for admin filters, derived from the domain enum."""
    return [(s.value, s.value.replace("_", " ").title()) for s in Stage]
