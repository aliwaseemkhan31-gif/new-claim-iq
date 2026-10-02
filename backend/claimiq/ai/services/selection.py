"""Starting a detection run, and applying what it found.

Kept separate from :mod:`claimiq.ai.services.benchmarking`, which measures,
and from :mod:`claimiq.ai.services.configuration`, which reads. This module is
the two write paths an administrator triggers.

The gate that matters is in :func:`apply_selection`: an embedding model whose
vectors are a different width from the stored column is refused outright. Not
warned about — refused. The width is fixed by the migration that built the
column, and selecting a mismatched model would mean every subsequent write
either errors or, worse, succeeds against a column that silently truncates,
and retrieval starts comparing vectors that are not comparable. That failure
surfaces weeks later as inexplicably poor search, which is the hardest class
of bug to trace back to its cause.
"""
from __future__ import annotations

import threading
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, close_old_connections, transaction
from django.utils import timezone

from claimiq.ai.domain.benchmark import (
    ROLE_DRAFTING,
    ROLE_EMBEDDING,
    ROLE_LLM,
    SELECTABLE_ROLES,
)
from claimiq.ai.domain.model_registry import same_model
from claimiq.ai.models import BenchmarkStatus, ModelBenchmarkRun, ModelConfiguration
from claimiq.ai.services.configuration import (
    invalidate_configuration_cache,
    resolve_ai_settings,
    stored_embedding_dimensions,
)
from claimiq.core.domain.errors import ConflictError, ValidationError
from claimiq.core.logging import get_logger

logger = get_logger("ai.selection")

ACTIVE_STATUSES = (BenchmarkStatus.QUEUED, BenchmarkStatus.RUNNING)

#: A run writes a row at every step, so silence this long means the worker or
#: thread running it is gone. Generous because a single generation on a
#: processor-only installation can legitimately take minutes with nothing to
#: report in between.
STALE_AFTER = timedelta(minutes=20)

#: Maps a selectable role onto the configuration column it writes.
_ROLE_FIELDS = {
    ROLE_LLM: "llm_model",
    ROLE_DRAFTING: "drafting_llm_model",
    ROLE_EMBEDDING: "embedding_model",
}


def start_detection(
    *, organization_id, user, include_pulls: bool = False, pull_targets=None
) -> ModelBenchmarkRun:
    """Queue a detection run.

    Raises:
        ConflictError: a run is already in progress. Two benchmarks measuring
            the same runtime at once queue behind each other inside Ollama and
            each reports the other's waiting as its own slowness, so the
            numbers would be wrong rather than merely duplicated.
    """
    existing = ModelBenchmarkRun.objects.filter(status__in=ACTIVE_STATUSES).first()
    if existing is not None:
        # A run whose worker died stays `running` for ever, and the unique
        # constraint would then block every future detection with no way out
        # from the interface. Retire it rather than leaving the feature wedged.
        if _is_stale(existing):
            _retire(existing)
        else:
            raise ConflictError(
                "A model detection is already running on this installation.",
                details={"run": str(existing.pk), "status": existing.status},
            )

    try:
        run = ModelBenchmarkRun.objects.create(
            organization_id=organization_id,
            requested_by=user,
            created_by=user,
            include_pulls=bool(include_pulls),
            pull_targets=list(pull_targets or []),
            current_step="Queued",
        )
    except IntegrityError as exc:
        # The partial unique constraint caught a race the query above missed.
        raise ConflictError(
            "A model detection is already running on this installation."
        ) from exc

    _dispatch(run)
    return run


def cancel_detection(run: ModelBenchmarkRun) -> ModelBenchmarkRun:
    """Ask a running detection to stop.

    Cancellation is cooperative: the run checks between models, because a
    generation already in flight cannot be interrupted. Where the run is
    already stale — its worker gone — there is nothing left to cooperate, so it
    is closed immediately instead of being asked politely for ever.
    """
    if run.is_finished:
        return run
    if _is_stale(run):
        _retire(run)
        run.refresh_from_db()
        return run
    ModelBenchmarkRun.objects.filter(pk=run.pk).update(
        cancel_requested=True, updated_at=timezone.now()
    )
    run.refresh_from_db()
    return run


def _is_stale(run: ModelBenchmarkRun, now=None) -> bool:
    if run.is_finished or run.updated_at is None:
        return False
    return (now or timezone.now()) - run.updated_at > STALE_AFTER


def _retire(run: ModelBenchmarkRun) -> None:
    """Close a run whose worker is gone, keeping what it had measured."""
    logger.warning("benchmark.retired_stale", extra={"run_id": str(run.pk)})
    ModelBenchmarkRun.objects.filter(pk=run.pk).update(
        status=BenchmarkStatus.FAILED,
        current_step="Abandoned",
        error_code="benchmark_abandoned",
        error_message=(
            "The detection stopped reporting progress and was abandoned. "
            "Anything it had already measured is kept below. Run it again."
        ),
        finished_at=timezone.now(),
        updated_at=timezone.now(),
    )


def _dispatch(run: ModelBenchmarkRun) -> None:
    """Start the run without holding the request open for it.

    Mirrors claim analysis: with a broker the run goes to the ``ai`` queue; in
    local development Celery runs inline, so a daemon thread keeps the request
    fast. A benchmark is minutes on a GPU and can be half an hour on a
    processor — far too long to hold a request.
    """
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        threading.Thread(
            target=_run_in_thread,
            args=(str(run.pk),),
            name=f"model-benchmark-{run.pk}",
            daemon=True,
        ).start()
        return

    from claimiq.ai.tasks import run_model_benchmark

    result = run_model_benchmark.apply_async(args=[str(run.pk)], queue="ai")
    ModelBenchmarkRun.objects.filter(pk=run.pk).update(celery_task_id=result.id or "")


def _run_in_thread(run_id: str) -> None:
    close_old_connections()
    try:
        from claimiq.ai.services.benchmarking import run_benchmark

        run_benchmark(run_id)
    except Exception:  # noqa: BLE001 - a thread has no caller to report to
        logger.exception("benchmark.thread_crashed", extra={"run_id": run_id})
        ModelBenchmarkRun.objects.filter(pk=run_id, status__in=ACTIVE_STATUSES).update(
            status=BenchmarkStatus.FAILED,
            error_code="internal_error",
            error_message=(
                "The detection stopped unexpectedly. It has been logged for "
                "investigation."
            ),
            finished_at=timezone.now(),
        )
    finally:
        close_old_connections()


# -- applying a selection -----------------------------------------------------


@transaction.atomic
def apply_selection(
    selections: dict,
    *,
    user=None,
    organization_id=None,
    run: ModelBenchmarkRun | None = None,
    hardware_profile: str | None = None,
) -> tuple[ModelConfiguration, list[str]]:
    """Write the chosen models into the installation's configuration.

    Args:
        selections: Role to model name. An empty string clears a role, which
            returns it to the environment's value rather than disabling it.
            Roles absent from the mapping are left untouched.
        run: The detection this came from, recorded as the justification.
        hardware_profile: Measured profile to store alongside, so the registry
            recommends against what the machine is rather than what an
            environment variable claims.

    Returns:
        The saved configuration and the warnings an administrator must see —
        chiefly that changing the embedding model invalidates stored vectors.

    Raises:
        ValidationError: an unknown role, a model the runtime does not have,
            or an embedding model of the wrong vector width.
    """
    unknown = set(selections) - set(SELECTABLE_ROLES)
    if unknown:
        raise ValidationError(
            "Unknown model role.",
            details={"unknown": sorted(unknown), "valid": list(SELECTABLE_ROLES)},
        )

    configuration = ModelConfiguration.load()
    warnings: list[str] = []
    previous_embedding = configuration.embedding_model or (
        resolve_ai_settings().get("DEFAULT_EMBEDDING_MODEL") or ""
    )

    installed = _installed_names()
    for role, value in selections.items():
        name = (value or "").strip()
        if name and installed is not None and not any(
            same_model(name, present) for present in installed
        ):
            raise ValidationError(
                f"The model {name!r} is not present in the local runtime.",
                details={
                    "role": role,
                    "model": name,
                    "available": sorted(installed),
                    "remedy": "Run a detection, or provision the model first.",
                },
            )
        if name:
            _check_kind(role, name)
        if role == ROLE_EMBEDDING and name:
            warnings.extend(_check_embedding(name, run, previous_embedding))
        setattr(configuration, _ROLE_FIELDS[role], name)

    if ROLE_EMBEDDING in selections:
        configuration.embedding_dimensions = (
            stored_embedding_dimensions() if selections[ROLE_EMBEDDING] else None
        )

    if hardware_profile:
        configuration.hardware_profile = hardware_profile

    configuration.source = (
        ModelConfiguration.Source.DETECTED if run else ModelConfiguration.Source.MANUAL
    )
    configuration.applied_from = run
    configuration.applied_by_organization_id = organization_id
    configuration.updated_by = user
    configuration.save()

    invalidate_configuration_cache()
    logger.info(
        "ai.configuration.applied",
        extra={
            "llm": configuration.llm_model,
            "drafting_llm": configuration.drafting_llm_model,
            "embedding": configuration.embedding_model,
            "hardware_profile": configuration.hardware_profile,
            "source": configuration.source,
            "run_id": str(run.pk) if run else None,
        },
    )
    return configuration, warnings


def _check_kind(role: str, name: str) -> None:
    """Refuse a model that cannot do the role's job at all.

    An embedding model selected for answering generates nothing, and a
    generation model selected for embedding returns no vectors. Both fail at
    call time, far from the choice that caused them, so they are refused here
    where the remedy is obvious.
    """
    from claimiq.ai.providers.ollama import is_embedding_model

    details = _installed_details().get(name) or {}
    looks_like_embedding = is_embedding_model(name, details)
    wants_embedding = role == ROLE_EMBEDDING

    if looks_like_embedding == wants_embedding:
        return
    raise ValidationError(
        f"{name} is {'an embedding' if looks_like_embedding else 'a generation'} "
        f"model, and this role needs "
        f"{'an embedding' if wants_embedding else 'a generation'} model.",
        details={
            "role": role,
            "model": name,
            "remedy": "Choose a model of the right kind for this role.",
        },
    )


def _installed_details() -> dict:
    """Installed models with the detail needed to classify them."""
    from claimiq.ai.providers.ollama import OllamaClient

    ai = resolve_ai_settings()
    client = OllamaClient(ai["OLLAMA_BASE_URL"], 10)
    if not client.is_reachable():
        return {}
    try:
        return {
            str(tag.get("name") or tag.get("model") or ""): dict(tag.get("details") or {})
            for tag in client.tags()
        }
    except Exception:  # noqa: BLE001 - classification is best effort
        return {}


def _check_embedding(
    name: str, run: ModelBenchmarkRun | None, previous: str
) -> list[str]:
    """Gate an embedding change on vector width, and warn about re-ingestion."""
    required = stored_embedding_dimensions()
    measured = _measured_dimensions(name, run)

    if measured is not None and measured != required:
        raise ValidationError(
            f"{name} produces {measured}-dimension vectors, but this "
            f"installation stores {required}. Selecting it would make stored "
            f"and new vectors incomparable, which shows up as poor search "
            f"rather than as an error.",
            details={
                "role": ROLE_EMBEDDING,
                "model": name,
                "measured_dimensions": measured,
                "required_dimensions": required,
                "remedy": (
                    "Choose a model of the same width, or migrate the vector "
                    "column and re-embed every document."
                ),
            },
        )

    warnings: list[str] = []
    if measured is None:
        warnings.append(
            f"The vector width of {name} has not been measured on this "
            f"installation. Run a detection before relying on it; a width "
            f"other than {required} will fail at the embedding stage."
        )
    if previous and not same_model(previous, name):
        affected = _embedded_chunk_count(previous)
        warnings.append(
            f"Vectors already stored were produced by {previous}. "
            + (
                f"{affected:,} passages must be re-embedded before search is "
                f"correct again — reprocess the affected documents from the "
                f"Documents tab."
                if affected
                else "Reprocess any affected documents before relying on search."
            )
        )
    return warnings


def _measured_dimensions(name: str, run: ModelBenchmarkRun | None) -> int | None:
    """Width measured for ``name``, from a detection run if one is given."""
    runs = [run] if run else list(
        ModelBenchmarkRun.objects.filter(status=BenchmarkStatus.COMPLETED)[:3]
    )
    for candidate in runs:
        if candidate is None:
            continue
        for probe in candidate.probes or []:
            if probe.get("embedding_dimensions") and same_model(
                str(probe.get("name") or ""), name
            ):
                return int(probe["embedding_dimensions"])
    return None


def _embedded_chunk_count(model_name: str) -> int:
    try:
        from claimiq.documents.models import DocumentChunk

        return DocumentChunk.objects.filter(
            embedding__isnull=False, embedding_model=model_name
        ).count()
    except Exception:  # noqa: BLE001 - a count is advisory, never fatal
        return 0


def _installed_names() -> set[str] | None:
    """Models the runtime has, or ``None`` when it could not be asked.

    ``None`` rather than an empty set, so an unreachable runtime does not
    reject every selection. An administrator fixing a broken configuration
    while the runtime is down is exactly when this must still work.
    """
    from claimiq.ai.providers.ollama import OllamaClient

    ai = resolve_ai_settings()
    client = OllamaClient(ai["OLLAMA_BASE_URL"], 10)
    if not client.is_reachable():
        return None
    try:
        names = {
            str(tag.get("name") or tag.get("model") or "") for tag in client.tags()
        }
    except Exception:  # noqa: BLE001
        return None
    return {name for name in names if name} or None
