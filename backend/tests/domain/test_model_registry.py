"""Tests for the model registry and hardware-profile selection.

The property under test: no model name is ever assumed. Selection is resolved
from configuration, profile and what the runtime actually reports.
"""
from __future__ import annotations

import pytest

from claimiq.ai.domain.model_registry import (
    BUILTIN_RECOMMENDATIONS,
    DEFAULT_MODEL_REGISTRY,
    HardwareProfile,
    ModelRecommendation,
    ModelRegistry,
    _base_name,
)
from claimiq.ai.domain.providers import ModelCapability, ModelSpec
from claimiq.core.domain.errors import ModelNotConfiguredError, ValidationError


def spec(name: str, provider: str = "ollama") -> ModelSpec:
    return ModelSpec(name=name, provider=provider)


# ---------------------------------------------------------------------------
# Profile-aware recommendation
# ---------------------------------------------------------------------------


def test_cpu_profile_excludes_gpu_only_models() -> None:
    names = [s.name for s in DEFAULT_MODEL_REGISTRY.recommendations_for("llm", HardwareProfile.CPU)]
    assert "qwen2.5:3b-instruct" in names
    assert "qwen2.5:32b-instruct" not in names


def test_stronger_profile_includes_weaker_models() -> None:
    """A capable host can still run the small model."""
    names = [
        s.name
        for s in DEFAULT_MODEL_REGISTRY.recommendations_for("llm", HardwareProfile.GPU_HIGH)
    ]
    assert "qwen2.5:3b-instruct" in names
    assert "qwen2.5:32b-instruct" in names


def test_recommendations_are_ordered_by_preference() -> None:
    names = [
        s.name
        for s in DEFAULT_MODEL_REGISTRY.recommendations_for("llm", HardwareProfile.GPU_HIGH)
    ]
    assert names.index("qwen2.5:3b-instruct") < names.index("qwen2.5:32b-instruct")


def test_include_stronger_shows_what_an_upgrade_would_buy() -> None:
    visible = DEFAULT_MODEL_REGISTRY.recommendations_for(
        "llm", HardwareProfile.CPU, include_stronger=True
    )
    assert any(s.name == "qwen2.5:32b-instruct" for s in visible)


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DEFAULT_MODEL_REGISTRY.recommendations_for("llm", "quantum")


def test_unknown_role_is_rejected_at_definition() -> None:
    with pytest.raises(ValidationError):
        ModelRecommendation(spec=spec("x"), role="oracle", min_profile=HardwareProfile.CPU)


# ---------------------------------------------------------------------------
# Reconciliation with what is actually installed
# ---------------------------------------------------------------------------


def test_reconcile_separates_available_from_missing() -> None:
    availability = DEFAULT_MODEL_REGISTRY.reconcile(
        "llm", HardwareProfile.GPU_MID, [spec("qwen2.5:7b-instruct")]
    )
    assert [s.name for s in availability.available] == ["qwen2.5:7b-instruct"]
    assert "qwen2.5:14b-instruct" in [s.name for s in availability.missing]
    assert availability.has_usable_model is True


def test_empty_runtime_reports_nothing_usable() -> None:
    availability = DEFAULT_MODEL_REGISTRY.reconcile("llm", HardwareProfile.CPU, [])
    assert availability.has_usable_model is False
    assert availability.available == []


def test_quantisation_suffix_still_matches_the_model() -> None:
    """A quantised build is a deployment choice, not a different model."""
    availability = DEFAULT_MODEL_REGISTRY.reconcile(
        "llm", HardwareProfile.GPU_MID, [spec("qwen2.5:14b-instruct-q4_K_M")]
    )
    assert [s.name for s in availability.available] == ["qwen2.5:14b-instruct"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("qwen2.5:14b-instruct-q4_K_M", "qwen2.5:14b-instruct"),
        ("qwen2.5:14b-instruct", "qwen2.5:14b-instruct"),
        ("QWEN2.5:7B-Instruct", "qwen2.5:7b-instruct"),
        ("bge-m3", "bge-m3"),
        ("llama3:8b-q8_0", "llama3:8b"),
    ],
)
def test_base_name_normalisation(raw: str, expected: str) -> None:
    assert _base_name(raw) == expected


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_configured_model_wins_when_present() -> None:
    """An administrator's explicit choice is honoured over the recommendation."""
    chosen = DEFAULT_MODEL_REGISTRY.select(
        "llm",
        HardwareProfile.GPU_MID,
        [spec("qwen2.5:7b-instruct"), spec("qwen2.5:14b-instruct")],
        configured="qwen2.5:7b-instruct",
    )
    assert chosen.name == "qwen2.5:7b-instruct"


def test_configured_model_is_honoured_even_if_unrecommended() -> None:
    chosen = DEFAULT_MODEL_REGISTRY.select(
        "llm", HardwareProfile.CPU, [spec("mistral:7b")], configured="mistral:7b"
    )
    assert chosen.name == "mistral:7b"


def test_missing_configured_model_raises_with_actionable_detail() -> None:
    with pytest.raises(ModelNotConfiguredError) as exc:
        DEFAULT_MODEL_REGISTRY.select(
            "llm", HardwareProfile.CPU, [spec("qwen2.5:3b-instruct")], configured="ghost:70b"
        )
    details = exc.value.details
    assert details["configured"] == "ghost:70b"
    assert "qwen2.5:3b-instruct" in details["available"]
    assert "remedy" in details


def test_selection_falls_back_to_best_available_recommendation() -> None:
    chosen = DEFAULT_MODEL_REGISTRY.select(
        "llm",
        HardwareProfile.GPU_HIGH,
        [spec("qwen2.5:14b-instruct"), spec("qwen2.5:3b-instruct")],
    )
    assert chosen.name == "qwen2.5:3b-instruct", "lowest preference value sorts first"


def test_no_usable_model_raises_rather_than_guessing() -> None:
    with pytest.raises(ModelNotConfiguredError) as exc:
        DEFAULT_MODEL_REGISTRY.select("llm", HardwareProfile.CPU, [])
    assert exc.value.details["profile"] == HardwareProfile.CPU
    assert exc.value.details["recommended"]


def test_embedding_and_reranker_roles_resolve_independently() -> None:
    embedding = DEFAULT_MODEL_REGISTRY.select(
        "embedding", HardwareProfile.CPU, [spec("bge-m3")]
    )
    reranker = DEFAULT_MODEL_REGISTRY.select(
        "reranker", HardwareProfile.CPU, [spec("bge-reranker-v2-m3", "sentence-transformers")]
    )
    assert embedding.name == "bge-m3"
    assert reranker.name == "bge-reranker-v2-m3"


# ---------------------------------------------------------------------------
# Hardware warnings
# ---------------------------------------------------------------------------


def test_oversized_model_warns_but_does_not_refuse() -> None:
    big = next(r.spec for r in BUILTIN_RECOMMENDATIONS if r.spec.name == "qwen2.5:32b-instruct")
    warning = DEFAULT_MODEL_REGISTRY.warn_if_oversized(big, HardwareProfile.GPU_ENTRY)
    assert warning is not None
    assert "VRAM" in warning


def test_well_sized_model_produces_no_warning() -> None:
    small = next(r.spec for r in BUILTIN_RECOMMENDATIONS if r.spec.name == "qwen2.5:7b-instruct")
    assert DEFAULT_MODEL_REGISTRY.warn_if_oversized(small, HardwareProfile.GPU_HIGH) is None


def test_gpu_model_on_cpu_warns_about_latency() -> None:
    gpu_model = next(
        r.spec for r in BUILTIN_RECOMMENDATIONS if r.spec.name == "qwen2.5:14b-instruct"
    )
    warning = DEFAULT_MODEL_REGISTRY.warn_if_oversized(gpu_model, HardwareProfile.CPU)
    assert warning is not None and "CPU" in warning


# ---------------------------------------------------------------------------
# Catalogue integrity
# ---------------------------------------------------------------------------


def test_every_llm_recommendation_supports_structured_output() -> None:
    """Grounding depends on schema-constrained output (ADR 0005)."""
    for recommendation in BUILTIN_RECOMMENDATIONS:
        if recommendation.role == "llm":
            assert recommendation.spec.supports(ModelCapability.STRUCTURED_OUTPUT)


def test_every_llm_recommendation_defaults_to_zero_temperature() -> None:
    """Contractual analysis should be reproducible; sampling variance is not a feature."""
    for recommendation in BUILTIN_RECOMMENDATIONS:
        if recommendation.role == "llm":
            assert recommendation.spec.default_temperature == 0.0


def test_embedding_models_declare_their_dimensions() -> None:
    for recommendation in BUILTIN_RECOMMENDATIONS:
        if recommendation.role == "embedding":
            assert recommendation.spec.embedding_dimensions is not None


def test_every_profile_has_at_least_one_llm() -> None:
    for profile in (
        HardwareProfile.CPU,
        HardwareProfile.GPU_ENTRY,
        HardwareProfile.GPU_MID,
        HardwareProfile.GPU_HIGH,
    ):
        assert DEFAULT_MODEL_REGISTRY.recommendations_for("llm", profile), profile


def test_custom_registry_can_replace_the_catalogue() -> None:
    registry = ModelRegistry(
        [
            ModelRecommendation(
                spec=spec("bespoke:1b"), role="llm", min_profile=HardwareProfile.CPU
            )
        ]
    )
    chosen = registry.select("llm", HardwareProfile.CPU, [spec("bespoke:1b")])
    assert chosen.name == "bespoke:1b"


# ---------------------------------------------------------------------------
# The implicit tag
# ---------------------------------------------------------------------------
#
# Ollama treats a bare `bge-m3` as `bge-m3:latest` and `/api/tags` always
# reports the explicit form. Observed against a real runtime: the registry's
# `bge-m3` recommendation read as missing on a machine that had it installed,
# and selecting it read as a change of embedding model — which would have told
# an administrator, wrongly, that every stored vector had been invalidated.


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("bge-m3", "bge-m3:latest"),
        ("bge-m3:latest", "bge-m3:latest"),
        ("qwen2.5:14b-instruct-q4_K_M", "qwen2.5:14b-instruct"),
        ("QWEN2.5:7B-Instruct", "qwen2.5:7b-instruct"),
        ("", ""),
    ],
)
def test_canonical_name_resolves_the_implicit_tag(raw: str, expected: str) -> None:
    from claimiq.ai.domain.model_registry import canonical_model_name

    assert canonical_model_name(raw) == expected


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("bge-m3", "bge-m3:latest", True),
        ("qwen2.5:7b-instruct", "qwen2.5:7b-instruct-q4_K_M", True),
        ("qwen2.5:7b-instruct", "qwen2.5:3b-instruct", False),
        ("bge-m3", "", False),
    ],
)
def test_same_model_ignores_tag_and_quantisation(left, right, expected) -> None:
    from claimiq.ai.domain.model_registry import same_model

    assert same_model(left, right) is expected


def test_an_installed_latest_tag_satisfies_a_bare_recommendation() -> None:
    availability = DEFAULT_MODEL_REGISTRY.reconcile(
        "embedding", HardwareProfile.CPU, [spec("bge-m3:latest")]
    )
    assert "bge-m3" in [s.name for s in availability.available]
    assert "bge-m3" not in [s.name for s in availability.missing]


def test_a_bare_configured_name_selects_the_installed_latest_tag() -> None:
    selected = DEFAULT_MODEL_REGISTRY.select(
        "embedding",
        HardwareProfile.CPU,
        [spec("bge-m3:latest")],
        configured="bge-m3",
    )
    assert selected.name == "bge-m3:latest"
