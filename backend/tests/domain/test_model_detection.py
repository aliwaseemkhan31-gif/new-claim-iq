"""Tests for hardware classification and model scoring.

The properties under test:

* A profile is never asserted more confidently than the evidence allows, and
  runtime evidence beats a host probe — because in the containerised
  deployment the host probe is wrong.
* A model that cannot honour a JSON schema is never recommended, whatever its
  throughput. Every citation rests on schema-constrained output.
* An embedding model of the wrong vector width is refused, not ranked.
* The same measurements rank differently for interactive answering and for
  drafting, because the jobs have different shapes.
"""
from __future__ import annotations

import pytest

from claimiq.ai.domain.benchmark import (
    ROLE_DRAFTING,
    ROLE_EMBEDDING,
    ROLE_LLM,
    ModelProbe,
    parameter_billions,
    quality_points,
    recommend,
    speed_points,
)
from claimiq.ai.domain.hardware import GpuDevice, HostCapacity, profile_for
from claimiq.ai.domain.model_registry import HardwareProfile


def llm_probe(
    name: str,
    *,
    params: float | None = 7.0,
    output_tps: float | None = 40.0,
    prompt_tps: float | None = 300.0,
    schema: bool | None = True,
    gpu: float | None = 1.0,
    context: int | None = 32768,
    error: str = "",
    timed_out: bool = False,
    recommended: bool = False,
) -> ModelProbe:
    return ModelProbe(
        name=name,
        parameter_billions=params,
        output_tokens_per_second=output_tps,
        prompt_tokens_per_second=prompt_tps,
        structured_output_ok=schema,
        gpu_resident_fraction=gpu,
        context_length=context,
        error=error,
        timed_out=timed_out,
        is_recommended_by_registry=recommended,
    )


def embedding_probe(name: str, dimensions: int, latency_ms: float = 400.0) -> ModelProbe:
    return ModelProbe(
        name=name, embedding_dimensions=dimensions, embedding_latency_ms=latency_ms
    )


# ---------------------------------------------------------------------------
# Hardware classification
# ---------------------------------------------------------------------------


def test_no_gpu_is_cpu_profile():
    verdict = profile_for(HostCapacity(cpu_count=8, total_ram_gb=16.0))
    assert verdict.profile == HardwareProfile.CPU
    assert "processor" in verdict.explanation


@pytest.mark.parametrize(
    ("vram", "expected"),
    [
        (4.0, HardwareProfile.CPU),       # below the entry floor
        (8.0, HardwareProfile.GPU_ENTRY),
        (16.0, HardwareProfile.GPU_MID),
        (24.0, HardwareProfile.GPU_HIGH),
        (48.0, HardwareProfile.GPU_HIGH),
    ],
)
def test_probed_vram_maps_to_profile(vram, expected):
    capacity = HostCapacity(gpus=(GpuDevice(name="Test GPU", total_vram_gb=vram),))
    assert profile_for(capacity).profile == expected


def test_reported_vram_below_nominal_still_reaches_its_profile():
    """A 24 GB card reports less than 24 GB and must still read as gpu-high."""
    capacity = HostCapacity(gpus=(GpuDevice("RTX 4090", 23.6, used_vram_gb=0.9),))
    assert profile_for(capacity).profile == HardwareProfile.GPU_HIGH


def test_runtime_observation_overrides_a_blind_host_probe():
    """The containerised case: Django sees no GPU, Ollama has one."""
    capacity = HostCapacity(cpu_count=16, total_ram_gb=64.0, runtime_total_vram_gb=16.0)
    verdict = profile_for(capacity)
    assert verdict.profile == HardwareProfile.GPU_MID
    assert verdict.basis == "runtime"


def test_probe_basis_is_recorded_when_only_the_host_was_seen():
    capacity = HostCapacity(gpus=(GpuDevice("GPU", 16.0),))
    assert profile_for(capacity).basis == "probe"


def test_occupied_vram_never_lowers_a_measured_device():
    """A 2.2 GB model resident on a 16 GB card does not make it a 2 GB card.

    Observed against a real runtime: occupied VRAM was taken as capacity and a
    GPU installation was classified processor-only on the strength of having
    run a small model.
    """
    capacity = HostCapacity(
        gpus=(GpuDevice("RTX 4060 Ti", 16.0),), runtime_vram_gb=2.2
    )
    assert capacity.effective_vram_gb == 16.0
    assert profile_for(capacity).profile == HardwareProfile.GPU_MID


def test_occupied_vram_raises_the_floor_when_the_host_is_blind():
    """The container case: Django sees nothing, Ollama has a model resident."""
    capacity = HostCapacity(cpu_count=8, runtime_vram_gb=12.0)
    assert capacity.effective_vram_gb == 12.0
    verdict = profile_for(capacity)
    assert verdict.profile == HardwareProfile.GPU_ENTRY
    assert verdict.basis == "runtime"


def test_a_runtime_total_is_authoritative_over_the_host():
    capacity = HostCapacity(
        gpus=(GpuDevice("onboard", 2.0),), runtime_total_vram_gb=24.0
    )
    assert capacity.effective_vram_gb == 24.0
    assert profile_for(capacity).profile == HardwareProfile.GPU_HIGH


def test_a_small_gpu_falls_to_cpu_and_says_the_figure_is_a_floor():
    """The real test machine: a 4 GB laptop GPU, below the entry floor."""
    capacity = HostCapacity(cpu_count=12, total_ram_gb=15.6, runtime_vram_gb=2.2)
    verdict = profile_for(capacity)
    assert verdict.profile == HardwareProfile.CPU
    assert "lower bound" in verdict.explanation


@pytest.mark.parametrize(
    ("capacity", "expected"),
    [
        (HostCapacity(), "none"),
        (HostCapacity(gpus=(GpuDevice("g", 8.0),)), "probe"),
        (HostCapacity(gpus=(GpuDevice("g", 8.0),), runtime_vram_gb=2.0), "probe"),
        (HostCapacity(gpus=(GpuDevice("g", 8.0),), runtime_vram_gb=12.0), "runtime"),
        (HostCapacity(runtime_total_vram_gb=8.0), "runtime"),
    ],
)
def test_which_evidence_decided_capacity(capacity, expected):
    assert capacity.vram_evidence == expected


def test_capacity_serialises_without_a_gpu():
    payload = HostCapacity(cpu_count=4, total_ram_gb=8.0, notes=("note",)).as_dict()
    assert payload["gpus"] == []
    assert payload["effective_vram_gb"] is None
    assert payload["notes"] == ["note"]


# ---------------------------------------------------------------------------
# Scoring primitives
# ---------------------------------------------------------------------------


def test_quality_rises_with_parameter_count():
    scores = [quality_points(p) for p in (1.0, 3.0, 7.0, 14.0, 32.0)]
    assert scores == sorted(scores)


def test_quality_of_an_unknown_size_is_assumed_not_excluded():
    assert 0 < quality_points(None) < quality_points(7.0)


def test_speed_falls_away_past_the_budget_without_reaching_zero():
    assert speed_points(5.0, 90.0) == 100.0
    assert speed_points(90.0, 90.0) < 60.0
    # Everything is over budget on a processor-only installation, and the
    # ranking still has to mean something.
    assert speed_points(1000.0, 90.0) > 0


def test_speed_of_an_unmeasured_model_is_zero():
    assert speed_points(None, 90.0) == 0.0


@pytest.mark.parametrize(
    ("text", "expected"),
    [("7.6B", 7.6), ("3B", 3.0), ("360M", 0.36), ("", None), ("unknown", None)],
)
def test_parameter_size_parsing(text, expected):
    assert parameter_billions(text) == expected


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------


def test_a_model_that_fails_the_schema_is_never_recommended():
    """However fast it is. Grounding depends on schema-constrained output."""
    results = recommend(
        [
            llm_probe("fast-but-unconstrained", output_tps=500.0, schema=False),
            llm_probe("slower-but-correct", output_tps=20.0, schema=True),
        ],
        required_embedding_dimensions=1024,
    )
    best = results[ROLE_LLM].best
    assert best is not None
    assert best.probe.name == "slower-but-correct"


def test_the_rejected_model_is_still_reported_with_its_reason():
    results = recommend(
        [llm_probe("unconstrained", schema=False)], required_embedding_dimensions=1024
    )
    candidates = results[ROLE_LLM].candidates
    assert len(candidates) == 1
    assert candidates[0].eligible is False
    assert "JSON schema" in candidates[0].rationale


def test_answering_prefers_speed_and_drafting_prefers_quality():
    """The same two models, ranked differently for the two jobs."""
    probes = [
        llm_probe("small-fast", params=3.0, output_tps=90.0, prompt_tps=900.0),
        llm_probe("large-slow", params=32.0, output_tps=9.0, prompt_tps=120.0),
    ]
    results = recommend(probes, required_embedding_dimensions=1024)
    assert results[ROLE_LLM].best.probe.name == "small-fast"
    assert results[ROLE_DRAFTING].best.probe.name == "large-slow"


def test_a_timeout_is_recorded_rather_than_dropped():
    results = recommend(
        [llm_probe("molasses", timed_out=True)], required_embedding_dimensions=1024
    )
    candidate = results[ROLE_LLM].candidates[0]
    assert candidate.eligible is False
    assert "did not finish" in candidate.rationale


def test_partial_gpu_residency_is_warned_about():
    results = recommend(
        [llm_probe("spilled", gpu=0.6)], required_embedding_dimensions=1024
    )
    best = results[ROLE_LLM].best
    assert any("video memory" in w for w in best.warnings)


def test_a_short_context_window_is_warned_about():
    results = recommend(
        [llm_probe("cramped", context=4096)], required_embedding_dimensions=1024
    )
    best = results[ROLE_LLM].best
    assert any("context window" in w for w in best.warnings)


def test_a_registry_model_wins_a_tie():
    probes = [
        llm_probe("curated", recommended=True),
        llm_probe("anonymous", recommended=False),
    ]
    results = recommend(probes, required_embedding_dimensions=1024)
    assert results[ROLE_LLM].best.probe.name == "curated"


def test_nothing_eligible_explains_itself():
    results = recommend(
        [llm_probe("broken", error="the runtime refused")],
        required_embedding_dimensions=1024,
    )
    payload = results[ROLE_LLM].as_dict()
    assert payload["recommended"] is None
    assert "No model is usable" in payload["rationale"]


def test_no_candidates_at_all_is_not_an_error():
    results = recommend([], required_embedding_dimensions=1024)
    assert results[ROLE_LLM].best is None
    assert results[ROLE_LLM].as_dict()["recommended"] is None


# ---------------------------------------------------------------------------
# Embedding width is a gate, not a preference
# ---------------------------------------------------------------------------


def test_an_embedding_model_of_the_wrong_width_is_refused():
    results = recommend(
        [],
        embedding_probes=[embedding_probe("all-minilm-l6-v2", 384)],
        required_embedding_dimensions=1024,
    )
    candidate = results[ROLE_EMBEDDING].candidates[0]
    assert candidate.eligible is False
    assert "384" in candidate.rationale and "1024" in candidate.rationale


def test_the_right_width_is_eligible_and_faster_wins():
    results = recommend(
        [],
        embedding_probes=[
            embedding_probe("slow-but-right", 1024, latency_ms=4000.0),
            embedding_probe("quick-and-right", 1024, latency_ms=200.0),
        ],
        required_embedding_dimensions=1024,
    )
    assert results[ROLE_EMBEDDING].best.probe.name == "quick-and-right"


def test_an_embedding_model_that_returned_nothing_is_refused():
    results = recommend(
        [],
        embedding_probes=[ModelProbe(name="silent")],
        required_embedding_dimensions=1024,
    )
    assert results[ROLE_EMBEDDING].candidates[0].eligible is False


# ---------------------------------------------------------------------------
# Workload estimates
# ---------------------------------------------------------------------------


def test_estimated_seconds_includes_prompt_evaluation():
    """Prompt evaluation dominates this application's prompts; ignoring it
    would understate every estimate."""
    probe = llm_probe("x", output_tps=50.0, prompt_tps=500.0)
    with_prompt = probe.estimated_seconds(ROLE_LLM)

    no_prompt = llm_probe("x", output_tps=50.0, prompt_tps=None).estimated_seconds(
        ROLE_LLM
    )
    assert with_prompt > no_prompt


def test_drafting_estimate_exceeds_answering_estimate():
    probe = llm_probe("x")
    assert probe.estimated_seconds(ROLE_DRAFTING) > probe.estimated_seconds(ROLE_LLM)


def test_a_model_with_no_throughput_has_no_estimate():
    assert llm_probe("x", output_tps=None).estimated_seconds(ROLE_LLM) is None
