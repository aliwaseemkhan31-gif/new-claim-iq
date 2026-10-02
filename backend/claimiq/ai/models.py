"""AI question history, and which models this installation uses.

Every question asked through the AI workspace is kept with the exact answer
returned, including failures. A grounded answer that cannot be found again the
next day is of little use in a claims file, and "what did the system tell us
on 3 March" must be answerable.

:class:`ModelConfiguration` and :class:`ModelBenchmarkRun` make the model
choice an administrative act rather than a deployment one. Before them,
changing model meant editing an environment variable and restarting — which in
practice meant nobody changed it, and an installation with a 24 GB GPU went on
running the 3B model the installer happened to set.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from claimiq.core.models import BaseModel


class AIQuestionStatus(models.TextChoices):
    ANSWERED = "answered", "Answered"
    FAILED = "failed", "Failed"


class AIQuestion(BaseModel):
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="ai_questions"
    )
    asked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    question = models.TextField()
    include_knowledge_base = models.BooleanField(default=True)
    edition_code = models.CharField(max_length=64, blank=True)

    status = models.CharField(max_length=16, choices=AIQuestionStatus.choices)
    response = models.JSONField(
        default=dict,
        blank=True,
        help_text="The answer exactly as returned: summary, findings, citations, sources.",
    )
    model = models.CharField(max_length=128, blank=True)
    prompt_identifier = models.CharField(max_length=128, blank=True)
    latency_ms = models.FloatField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        db_table = "ai_question"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["project", "-created_at"])]

    def __str__(self) -> str:
        return self.question[:80]


class ModelConfiguration(BaseModel):
    """The models this installation uses, per role.

    **Installation-wide, not per organization.** There is one model runtime and
    one vector column behind every tenant, so a per-organization choice would
    be a promise the deployment cannot keep. The row records which
    organization and user last changed it, which is an audit fact rather than a
    scoping one.

    A blank field means "fall back to the environment", so an installation that
    has never used the detection behaves exactly as it did before. See
    :func:`claimiq.ai.services.configuration.resolve_ai_settings`.
    """

    SCOPE = "installation"

    class Source(models.TextChoices):
        MANUAL = "manual", "Chosen by an administrator"
        DETECTED = "detected", "Applied from a detection run"

    scope = models.CharField(
        max_length=32,
        default=SCOPE,
        unique=True,
        editable=False,
        help_text="Singleton marker. One configuration per installation.",
    )

    llm_model = models.CharField(
        max_length=128,
        blank=True,
        help_text="Answering and claim analysis. Blank falls back to DEFAULT_LLM_MODEL.",
    )
    drafting_llm_model = models.CharField(
        max_length=128,
        blank=True,
        help_text=(
            "Drafting a claim from a document. Blank falls back to the "
            "answering model, which is the previous behaviour."
        ),
    )
    embedding_model = models.CharField(max_length=128, blank=True)
    reranker_model = models.CharField(max_length=128, blank=True)

    hardware_profile = models.CharField(
        max_length=16,
        blank=True,
        help_text=(
            "Measured profile, which the model registry uses to decide what to "
            "recommend. Blank falls back to HARDWARE_PROFILE."
        ),
    )

    embedding_dimensions = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Vector width the selected embedding model was measured to produce. "
            "Stored as a cross-check: it must equal the stored column width, "
            "and a mismatch means retrieval is comparing vectors that are not "
            "comparable."
        ),
    )

    source = models.CharField(max_length=16, choices=Source.choices, default=Source.MANUAL)
    applied_from = models.ForeignKey(
        "ai.ModelBenchmarkRun",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="applied_configurations",
    )
    applied_by_organization = models.ForeignKey(
        "accounts.Organization",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "ai_model_configuration"

    def __str__(self) -> str:
        return f"{self.llm_model or 'unset'} / {self.embedding_model or 'unset'}"

    @classmethod
    def load(cls) -> ModelConfiguration:
        """The singleton, created empty on first access."""
        configuration, _ = cls.objects.get_or_create(scope=cls.SCOPE)
        return configuration

    def overrides(self) -> dict:
        """Non-blank values, keyed as ``AI_SETTINGS`` keys."""
        mapping = {
            "DEFAULT_LLM_MODEL": self.llm_model,
            "DRAFTING_LLM_MODEL": self.drafting_llm_model,
            "DEFAULT_EMBEDDING_MODEL": self.embedding_model,
            "DEFAULT_RERANKER_MODEL": self.reranker_model,
            "HARDWARE_PROFILE": self.hardware_profile,
        }
        return {key: value for key, value in mapping.items() if value}


class BenchmarkStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class ModelBenchmarkRun(BaseModel):
    """One pass of measuring this machine and the models available on it.

    Kept rather than discarded because the measurements are the justification
    for the selection: "why is this installation on the 7B model" is answered
    by the run that showed the 14B taking four minutes an answer.
    """

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="model_benchmark_runs"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )

    status = models.CharField(
        max_length=16, choices=BenchmarkStatus.choices, default=BenchmarkStatus.QUEUED,
        db_index=True,
    )
    current_step = models.CharField(max_length=160, blank=True)
    progress_percent = models.PositiveSmallIntegerField(default=0)

    include_pulls = models.BooleanField(
        default=False,
        help_text=(
            "Download recommended models that are missing. Requires a route to "
            "the model library, which an air-gapped installation does not have."
        ),
    )
    pull_targets = models.JSONField(
        default=list, blank=True, help_text="Models the operator asked to download."
    )

    runtime_version = models.CharField(max_length=64, blank=True)
    host_capacity = models.JSONField(default=dict, blank=True)
    detected_profile = models.CharField(max_length=16, blank=True)
    profile_basis = models.CharField(max_length=16, blank=True)
    profile_explanation = models.TextField(blank=True)

    probes = models.JSONField(
        default=list,
        blank=True,
        help_text="Per-model measurements, including the models that failed.",
    )
    recommendations = models.JSONField(default=dict, blank=True)
    warnings = models.JSONField(default=list, blank=True)

    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)

    celery_task_id = models.CharField(max_length=255, blank=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    cancel_requested = models.BooleanField(
        default=False,
        help_text=(
            "Cooperative cancellation, checked between models. A generation "
            "already in flight cannot be interrupted, and a download in "
            "progress is left to finish rather than abandoned part-written."
        ),
    )

    #: Constant column, so the partial unique constraint below can be
    #: installation-wide rather than per organization.
    scope_marker = models.CharField(max_length=16, default="installation", editable=False)

    class Meta:
        db_table = "ai_model_benchmark_run"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "-created_at"])]
        constraints = [
            # One run at a time per installation: two benchmarks measuring the
            # same runtime concurrently would queue behind each other inside
            # Ollama and report each other's slowness as their own.
            models.UniqueConstraint(
                fields=["scope_marker"],
                condition=models.Q(status__in=["queued", "running"]),
                name="one_active_model_benchmark",
            )
        ]

    def __str__(self) -> str:
        return f"Model benchmark {self.pk} ({self.status})"

    @property
    def is_finished(self) -> bool:
        return self.status in (
            BenchmarkStatus.COMPLETED,
            BenchmarkStatus.FAILED,
            BenchmarkStatus.CANCELLED,
        )
