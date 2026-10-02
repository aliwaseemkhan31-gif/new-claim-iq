"""Measuring this installation and recommending a model for each role.

What the run does, in order:

1. Ask the runtime what it has and what version it is.
2. Measure the host — processors, memory, any GPU this process can see.
3. Decide which models are candidates: everything installed, plus anything the
   registry recommends for the detected profile that the operator asked to
   download.
4. Download the missing ones, if asked.
5. Run each candidate on a small piece of the work this application actually
   does — a schema-constrained extraction over contract-shaped prose — and
   time it. Read ``/api/ps`` straight afterwards to see how much of the model
   Ollama put on the GPU.
6. Re-measure the hardware profile, now that there is runtime evidence for it.
7. Score and recommend.

The benchmark is a real task rather than a synthetic one because the synthetic
answer is misleading: a model's tokens per second on "write a poem" says
nothing about whether it can hold a JSON schema, and schema-constrained output
is the mechanism every citation in this system rests on (ADR 0005).

Nothing here is called from a request path. The run takes minutes on a GPU and
can take half an hour on a processor; it belongs on the ``ai`` queue.
"""
from __future__ import annotations

import json
import time

from django.utils import timezone

from claimiq.ai.domain.benchmark import (
    ModelProbe,
    parameter_billions,
    recommend,
)
from claimiq.ai.domain.hardware import profile_for
from claimiq.ai.domain.model_registry import (
    DEFAULT_MODEL_REGISTRY,
    canonical_model_name,
    same_model,
)
from claimiq.ai.domain.providers import GenerationRequest
from claimiq.ai.models import BenchmarkStatus, ModelBenchmarkRun
from claimiq.ai.providers.ollama import (
    OllamaClient,
    OllamaEmbeddingProvider,
    OllamaLLMProvider,
    is_embedding_model,
)
from claimiq.ai.services.capacity import probe_host_capacity
from claimiq.ai.services.configuration import (
    resolve_ai_settings,
    stored_embedding_dimensions,
)
from claimiq.core.domain.errors import ClaimIQError, ProviderUnavailableError
from claimiq.core.logging import get_logger

logger = get_logger("ai.benchmark")

#: Per-model ceiling. A model slower than this is not a candidate for anything
#: this application does, so there is no value in waiting out the full
#: generation to find out exactly how slow it is.
BENCHMARK_TIMEOUT_SECONDS = 150

#: Output cap for the benchmark generation. Enough tokens to measure a rate
#: without spending minutes per model.
BENCHMARK_OUTPUT_TOKENS = 192

#: Contract-shaped prose, written for this purpose. Long enough that prompt
#: evaluation is measurable rather than noise, and structured enough that a
#: capable model fills every field and a weak one does not — which is the
#: distinction the score needs to make.
BENCHMARK_DOCUMENT = """
NOTICE OF CLAIM

Reference: CL-2024-018
Date: 14 March 2024
Project: Northern Interchange, Package 3
From: The Contractor
To: The Engineer

1. Pursuant to Sub-Clause 20.1 of the Conditions of Contract, the Contractor
   gives notice of a claim for an extension of the Time for Completion and for
   additional payment arising from the events described below.

2. On 26 February 2024 the Employer's personnel instructed the Contractor to
   suspend all piling operations at Pier 4 pending resolution of a discrepancy
   between the issued reinforcement drawings (Rev. C) and the as-built survey
   of the pile cap. The suspension remained in force until 11 March 2024, a
   period of fourteen days.

3. The Contractor contends that the discrepancy constitutes a change in the
   Employer's Requirements and that the suspension was not attributable to any
   act or default of the Contractor or of any Subcontractor.

4. The piling operation at Pier 4 was on the critical path of the accepted
   programme at the date of suspension. The Contractor therefore claims an
   extension of fourteen days to the Time for Completion, and additional
   payment of 184,500 in the currency of the Contract in respect of standing
   time for plant and supervision.

5. Records supporting this notice, including daily site records, plant returns
   and the correspondence of 26 February 2024, are held by the Contractor and
   will be submitted with the fully detailed claim.
""".strip()

BENCHMARK_SYSTEM_PROMPT = (
    "You read construction contract documents and extract facts from them. "
    "Return only what the document states. Where the document does not state "
    "a field, return null for it. Never infer, and never fill a field from "
    "general knowledge."
)

BENCHMARK_PROMPT = (
    "Extract the claim details from the document below.\n\n"
    "Return the reference, the date of the notice, the clause relied on, the "
    "number of days of extension claimed, the amount claimed, and a one-"
    "sentence summary of the cause.\n\n"
    f"DOCUMENT:\n{BENCHMARK_DOCUMENT}\n"
)

#: The schema the benchmark constrains output to. Shaped like the extraction
#: schemas the application really uses — required fields, nullable fields, a
#: number and a date — because a model that handles a flat string object and
#: falls over on this one is a model that will fail in production.
BENCHMARK_SCHEMA = {
    "type": "object",
    "properties": {
        "reference": {"type": ["string", "null"]},
        "notice_date": {"type": ["string", "null"]},
        "clause": {"type": ["string", "null"]},
        "days_claimed": {"type": ["integer", "null"]},
        "amount_claimed": {"type": ["number", "null"]},
        "cause_summary": {"type": ["string", "null"]},
    },
    "required": [
        "reference",
        "notice_date",
        "clause",
        "days_claimed",
        "amount_claimed",
        "cause_summary",
    ],
    "additionalProperties": False,
}

#: Fields a capable model reads off the document above. Used only to record
#: how much of the extraction each model managed — not to score, because six
#: fields of one document is far too small a sample to rank quality on. It is
#: shown to the administrator, who can weigh it as the anecdote it is.
BENCHMARK_EXPECTED_FIELDS = (
    "reference",
    "notice_date",
    "clause",
    "days_claimed",
    "amount_claimed",
)

BENCHMARK_EMBEDDING_TEXTS = (
    "The Contractor shall give notice to the Engineer within 28 days.",
    "Standing time for plant and supervision during the suspension period.",
    "Extension of the Time for Completion under Sub-Clause 8.4.",
    "The Engineer shall proceed to agree or determine the claim.",
)


class BenchmarkCancelled(ClaimIQError):
    code = "benchmark_cancelled"


def run_benchmark(run_id: str) -> ModelBenchmarkRun:
    """Execute the benchmark identified by ``run_id``.

    Raises:
        ModelBenchmarkRun.DoesNotExist: the run was deleted after queueing.
    """
    run = ModelBenchmarkRun.objects.get(pk=run_id)
    if run.is_finished:
        return run

    run.status = BenchmarkStatus.RUNNING
    run.started_at = run.started_at or timezone.now()
    run.progress_percent = 0
    run.save(update_fields=["status", "started_at", "progress_percent", "updated_at"])

    try:
        _execute(run)
    except BenchmarkCancelled:
        _finish(run, BenchmarkStatus.CANCELLED, step="Cancelled")
    except ClaimIQError as exc:
        logger.warning(
            "benchmark.failed", extra={"run_id": str(run.pk), "code": exc.code}
        )
        run.error_code = exc.code
        run.error_message = exc.message
        _finish(run, BenchmarkStatus.FAILED, step="Failed")
    except Exception as exc:  # noqa: BLE001 - the run must never leave a row running
        logger.exception("benchmark.unexpected_failure", extra={"run_id": str(run.pk)})
        run.error_code = "benchmark_error"
        run.error_message = (
            f"The detection stopped unexpectedly ({type(exc).__name__}). The "
            f"models already measured are recorded below."
        )
        _finish(run, BenchmarkStatus.FAILED, step="Failed")
    return run


# -- the run ------------------------------------------------------------------


def _execute(run: ModelBenchmarkRun) -> None:
    ai = resolve_ai_settings()
    base_url = ai["OLLAMA_BASE_URL"]
    client = OllamaClient(base_url, BENCHMARK_TIMEOUT_SECONDS)

    _step(run, "Contacting the model runtime", 2)
    if not client.is_reachable():
        raise ProviderUnavailableError(
            "The model runtime is not reachable, so nothing can be measured.",
            details={
                "base_url": base_url,
                "remedy": "Start the model runtime, then run the detection again.",
            },
        )
    run.runtime_version = client.version()

    _step(run, "Measuring this machine", 5)
    capacity = probe_host_capacity()
    verdict = profile_for(capacity)
    run.host_capacity = capacity.as_dict()
    run.detected_profile = verdict.profile
    run.profile_basis = verdict.basis
    run.profile_explanation = verdict.explanation
    run.save(
        update_fields=[
            "runtime_version",
            "host_capacity",
            "detected_profile",
            "profile_basis",
            "profile_explanation",
            "updated_at",
        ]
    )

    _step(run, "Listing the models already installed", 8)
    installed = _installed_models(client)
    warnings: list[str] = list(capacity.notes)

    pulled: set[str] = set()
    if run.include_pulls:
        pulled = _pull_missing(run, client, installed, verdict.profile, warnings)
        if pulled:
            installed = _installed_models(client)

    generation_names, embedding_names = _candidates(installed)
    if not generation_names and not embedding_names:
        raise ProviderUnavailableError(
            "The model runtime has no models installed, so there is nothing to "
            "measure.",
            details={
                "remedy": (
                    "Provision at least one model into the runtime, or re-run "
                    "the detection with downloading enabled."
                )
            },
        )

    total = len(generation_names) + len(embedding_names)
    done = 0
    probes: list[ModelProbe] = []
    embedding_probes: list[ModelProbe] = []
    recommended_names = _registry_recommended_names(verdict.profile)

    llm_provider = OllamaLLMProvider(
        base_url,
        BENCHMARK_TIMEOUT_SECONDS,
        max_output_tokens=BENCHMARK_OUTPUT_TOKENS,
        context_tokens=ai.get("LLM_CONTEXT_TOKENS"),
    )
    embedding_provider = OllamaEmbeddingProvider(base_url, BENCHMARK_TIMEOUT_SECONDS)

    for name in generation_names:
        _check_cancelled(run)
        _step(
            run,
            f"Measuring {name} ({done + 1} of {total})",
            10 + int(80 * done / max(total, 1)),
        )
        probes.append(
            _probe_generation(
                client,
                llm_provider,
                name,
                installed.get(name, {}),
                is_recommended=name in recommended_names,
                was_pulled=name in pulled,
            )
        )
        done += 1

    required_dimensions = stored_embedding_dimensions()
    for name in embedding_names:
        _check_cancelled(run)
        _step(
            run,
            f"Measuring {name} ({done + 1} of {total})",
            10 + int(80 * done / max(total, 1)),
        )
        embedding_probes.append(
            _probe_embedding(
                embedding_provider,
                name,
                installed.get(name, {}),
                is_recommended=name in recommended_names,
                was_pulled=name in pulled,
            )
        )
        done += 1

    _step(run, "Deciding what this machine should use", 92)

    # The profile is re-decided now that models have actually run: VRAM
    # residency reported by the runtime beats anything the host probe saw.
    best_vram = max(
        (p.vram_bytes or 0 for p in probes + embedding_probes), default=0
    )
    if best_vram:
        capacity = probe_host_capacity(runtime_vram_bytes=best_vram)
        verdict = profile_for(capacity)
        run.host_capacity = capacity.as_dict()
        run.detected_profile = verdict.profile
        run.profile_basis = verdict.basis
        run.profile_explanation = verdict.explanation
    elif any(p.ran for p in probes):
        warnings.append(
            "No model was placed in video memory during the run, so every "
            "model here ran on the processor."
        )

    recommendations = recommend(
        probes,
        embedding_probes=embedding_probes,
        required_embedding_dimensions=required_dimensions,
    )

    current_embedding = ai.get("DEFAULT_EMBEDDING_MODEL") or ""
    best_embedding = recommendations["embedding"].best
    if (
        best_embedding
        and current_embedding
        and not same_model(best_embedding.probe.name, current_embedding)
    ):
        warnings.append(
            f"Changing the embedding model from {current_embedding} to "
            f"{best_embedding.probe.name} invalidates every vector already "
            f"stored. Search results will be wrong until each document has "
            f"been reprocessed, so change it only if you intend to re-ingest."
        )

    run.probes = [p.as_dict() for p in probes + embedding_probes]
    run.recommendations = {role: rec.as_dict() for role, rec in recommendations.items()}
    run.warnings = warnings
    run.save(
        update_fields=[
            "probes",
            "recommendations",
            "warnings",
            "host_capacity",
            "detected_profile",
            "profile_basis",
            "profile_explanation",
            "updated_at",
        ]
    )
    _finish(run, BenchmarkStatus.COMPLETED, step="Finished")


# -- candidates ---------------------------------------------------------------


def _installed_models(client: OllamaClient) -> dict[str, dict]:
    """Installed models by name, each with the detail ``/api/show`` adds."""
    installed: dict[str, dict] = {}
    for tag in client.tags():
        name = str(tag.get("name") or tag.get("model") or "")
        if not name:
            continue
        details = dict(tag.get("details") or {})
        shown = client.show(name)
        info = shown.get("model_info") or {}
        context_length = None
        for key, value in info.items():
            # Ollama keys this by architecture, e.g. ``qwen2.context_length``.
            if key.endswith(".context_length") and isinstance(value, int):
                context_length = value
                break
        installed[name] = {
            "size_bytes": tag.get("size"),
            "parameter_size": details.get("parameter_size") or "",
            "quantization": details.get("quantization_level") or "",
            "family": details.get("family") or "",
            "context_length": context_length,
            "capabilities": shown.get("capabilities") or [],
        }
    return installed


def _candidates(installed: dict[str, dict]) -> tuple[list[str], list[str]]:
    generation: list[str] = []
    embedding: list[str] = []
    for name, detail in sorted(installed.items()):
        if is_embedding_model(name, detail):
            embedding.append(name)
        else:
            generation.append(name)
    return generation, embedding


def _registry_recommended_names(profile: str) -> set[str]:
    names: set[str] = set()
    for role in ("llm", "embedding", "reranker"):
        for spec in DEFAULT_MODEL_REGISTRY.recommendations_for(role, profile):
            if spec.provider == "ollama":
                names.add(spec.name)
    return names


def _pull_missing(
    run: ModelBenchmarkRun,
    client: OllamaClient,
    installed: dict[str, dict],
    profile: str,
    warnings: list[str],
) -> set[str]:
    """Download the models the operator asked for, reporting progress.

    A failed download is a warning, not a failed run: the point of the
    detection is to recommend from what is available, and one model that could
    not be fetched does not stop the other five being measured.
    """
    requested = [str(m) for m in (run.pull_targets or []) if m]
    if not requested:
        requested = [
            spec.name
            for role in ("llm", "embedding")
            for spec in DEFAULT_MODEL_REGISTRY.recommendations_for(role, profile)
            if spec.provider == "ollama"
        ]

    present = {canonical_model_name(name) for name in installed}
    missing = [
        name for name in requested if canonical_model_name(name) not in present
    ]
    if not missing:
        return set()

    pulled: set[str] = set()
    for index, name in enumerate(missing):
        _check_cancelled(run)
        last_percent = -1

        def on_progress(status: str, completed: int, total: int, _name=name) -> None:
            nonlocal last_percent
            percent = int(100 * completed / total) if total else 0
            # Writing a row for every NDJSON line would be thousands of
            # updates per download.
            if percent == last_percent:
                return
            last_percent = percent
            _step(
                run,
                f"Downloading {_name} — {status or 'in progress'} {percent}%",
                min(9, 2 + int(7 * (index + percent / 100) / len(missing))),
            )

        try:
            client.pull(name, on_progress)
            pulled.add(name)
        except ClaimIQError as exc:
            warnings.append(f"{name} could not be downloaded: {exc.message}")
    return pulled


# -- measurement --------------------------------------------------------------


def _probe_generation(
    client: OllamaClient,
    provider: OllamaLLMProvider,
    name: str,
    detail: dict,
    *,
    is_recommended: bool,
    was_pulled: bool,
) -> ModelProbe:
    base = {
        "name": name,
        "parameter_billions": parameter_billions(detail.get("parameter_size") or ""),
        "quantization": detail.get("quantization") or "",
        "context_length": detail.get("context_length"),
        "size_bytes": detail.get("size_bytes"),
        "is_recommended_by_registry": is_recommended,
        "was_pulled": was_pulled,
    }

    structured = provider.supports_structured_output(name)
    request = GenerationRequest(
        prompt=BENCHMARK_PROMPT,
        system_prompt=BENCHMARK_SYSTEM_PROMPT,
        temperature=0.0,
        max_tokens=BENCHMARK_OUTPUT_TOKENS,
        json_schema=BENCHMARK_SCHEMA if structured else None,
        seed=1,
    )

    started = time.perf_counter()
    try:
        result = provider.generate(name, request)
    except ClaimIQError as exc:
        elapsed = time.perf_counter() - started
        timed_out = bool(exc.details.get("timeout_seconds")) or elapsed >= (
            BENCHMARK_TIMEOUT_SECONDS - 1
        )
        return ModelProbe(
            **base,
            structured_output_ok=False if not structured else None,
            timed_out=timed_out,
            error="" if timed_out else exc.message,
        )

    prompt_tps, output_tps = _throughput(result)

    schema_ok: bool | None = None
    fields_read: int | None = None
    if structured:
        schema_ok, fields_read = _check_extraction(result.text)

    gpu_fraction, vram_bytes = _gpu_residency(client, name)

    probe = ModelProbe(
        **base,
        prompt_tokens_per_second=prompt_tps,
        output_tokens_per_second=output_tps,
        latency_ms=result.latency_ms,
        structured_output_ok=schema_ok,
        fields_read=fields_read,
        gpu_resident_fraction=gpu_fraction,
        vram_bytes=vram_bytes,
    )
    logger.info(
        "benchmark.model",
        extra={
            "model": name,
            "output_tokens_per_second": output_tps,
            "structured_output_ok": schema_ok,
            "fields_read": fields_read,
            "gpu_resident_fraction": gpu_fraction,
        },
    )
    return probe


def _throughput(result) -> tuple[float | None, float | None]:
    """Prompt-evaluation and generation rates, in tokens per second.

    Taken from Ollama's own ``prompt_eval_duration`` and ``eval_duration``
    rather than from wall-clock time. Three reasons, all of which would
    otherwise distort the ranking:

    * Loading a cold model off disk is counted in wall-clock time but is not
      generation, and it makes a large model look far slower than it is in
      steady use — the 32B pays several seconds the 3B does not.
    * Prompt evaluation is batched and runs several times faster per token
      than generation, so one blended rate understates generation and
      overstates prompt handling.
    * Queueing inside the runtime shows up in wall-clock time too.

    Falls back to apportioning wall-clock time when the durations are absent,
    and returns ``None`` rather than a fabricated figure when there is nothing
    to divide.
    """
    metadata = result.raw_metadata or {}
    prompt_tokens = result.prompt_tokens or 0
    output_tokens = result.completion_tokens or 0

    prompt_ns = metadata.get("prompt_eval_duration_ns") or 0
    eval_ns = metadata.get("eval_duration_ns") or 0

    prompt_tps = (
        round(prompt_tokens / (prompt_ns / 1e9), 1)
        if prompt_tokens and prompt_ns
        else None
    )
    output_tps = (
        round(output_tokens / (eval_ns / 1e9), 1) if output_tokens and eval_ns else None
    )
    if output_tps is not None:
        return prompt_tps, output_tps

    # No separate timings: fall back to wall-clock less model load time, and
    # report one rate for both rather than inventing a split.
    total_seconds = (result.latency_ms or 0.0) / 1000.0
    load_seconds = (metadata.get("load_duration_ns") or 0) / 1e9
    measured = max(total_seconds - load_seconds, 0.001)
    if not output_tokens:
        return prompt_tps, None
    blended = round((prompt_tokens + output_tokens) / measured, 1)
    return prompt_tps or blended, blended


def _check_extraction(text: str) -> tuple[bool, int | None]:
    """Whether the model honoured the schema, and how much it read.

    The second figure is recorded for the administrator's benefit, not scored:
    one document is far too small a sample to rank quality on, and pretending
    otherwise would be the sort of false precision that gets a bad model
    selected with confidence.
    """
    try:
        payload = json.loads(text)
    except ValueError:
        return False, None
    if not isinstance(payload, dict):
        return False, None
    missing = [key for key in BENCHMARK_SCHEMA["required"] if key not in payload]
    if missing:
        return False, None
    read = sum(
        1
        for field in BENCHMARK_EXPECTED_FIELDS
        if payload.get(field) not in (None, "", [])
    )
    return True, read


def _gpu_residency(client: OllamaClient, name: str) -> tuple[float | None, int | None]:
    """How much of ``name`` Ollama placed in video memory.

    Read from ``/api/ps`` immediately after generation, while the model is
    still loaded. This is the authoritative GPU signal: in the Docker
    deployment Django sees no GPU at all, and the host probe would report a
    processor-only machine for an installation with a 24 GB card.
    """
    for entry in client.running():
        entry_name = str(entry.get("name") or entry.get("model") or "")
        if not same_model(entry_name, name):
            continue
        size = entry.get("size") or 0
        vram = entry.get("size_vram") or 0
        if not size:
            return None, int(vram) or None
        return min(1.0, vram / size), int(vram) or None
    return None, None


def _probe_embedding(
    provider: OllamaEmbeddingProvider,
    name: str,
    detail: dict,
    *,
    is_recommended: bool,
    was_pulled: bool,
) -> ModelProbe:
    base = {
        "name": name,
        "parameter_billions": parameter_billions(detail.get("parameter_size") or ""),
        "quantization": detail.get("quantization") or "",
        "context_length": detail.get("context_length"),
        "size_bytes": detail.get("size_bytes"),
        "is_recommended_by_registry": is_recommended,
        "was_pulled": was_pulled,
    }
    try:
        result = provider.embed(name, list(BENCHMARK_EMBEDDING_TEXTS))
    except ClaimIQError as exc:
        return ModelProbe(**base, error=exc.message)
    return ModelProbe(
        **base,
        embedding_dimensions=result.dimensions,
        embedding_latency_ms=result.latency_ms,
    )


# -- run bookkeeping ----------------------------------------------------------


def _step(run: ModelBenchmarkRun, label: str, percent: int) -> None:
    run.current_step = label[:160]
    run.progress_percent = max(0, min(100, percent))
    run.save(update_fields=["current_step", "progress_percent", "updated_at"])


def _check_cancelled(run: ModelBenchmarkRun) -> None:
    if ModelBenchmarkRun.objects.filter(pk=run.pk, cancel_requested=True).exists():
        raise BenchmarkCancelled("The detection was cancelled.")


def _finish(run: ModelBenchmarkRun, status: str, *, step: str) -> None:
    run.status = status
    run.current_step = step
    run.progress_percent = 100 if status == BenchmarkStatus.COMPLETED else run.progress_percent
    run.finished_at = timezone.now()
    run.save(
        update_fields=[
            "status",
            "current_step",
            "progress_percent",
            "finished_at",
            "error_code",
            "error_message",
            "updated_at",
        ]
    )
