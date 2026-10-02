"""Model registry and hardware profiles.

Answers two questions the application must not answer by hardcoding:

1. **Which models may be used?** A registry keyed by capability, validated
   against what the local runtime actually has. No model name appears in
   application logic.

2. **Which models *should* be used here?** Deployments range from a CPU-only
   office server to a workstation with a 24 GB GPU. A profile expresses that,
   and selection degrades gracefully rather than failing.

The entries below are **recommendations**, not requirements or bundled assets.
An air-gapped installation has whatever was provisioned onto it; the registry
reconciles recommendations against discovered models and reports the gap.

Pure stdlib; runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from claimiq.ai.domain.providers import ModelCapability, ModelSpec
from claimiq.core.domain.errors import ModelNotConfiguredError, ValidationError


class HardwareProfile:
    CPU = "cpu"
    GPU_ENTRY = "gpu-entry"      # ~8 GB VRAM
    GPU_MID = "gpu-mid"          # ~16 GB VRAM
    GPU_HIGH = "gpu-high"        # 24 GB VRAM and above


ALL_PROFILES = (
    HardwareProfile.CPU,
    HardwareProfile.GPU_ENTRY,
    HardwareProfile.GPU_MID,
    HardwareProfile.GPU_HIGH,
)

#: Ordered weakest to strongest, so a profile can accept anything a weaker
#: profile can run.
PROFILE_RANK: Mapping[str, int] = {
    HardwareProfile.CPU: 0,
    HardwareProfile.GPU_ENTRY: 1,
    HardwareProfile.GPU_MID: 2,
    HardwareProfile.GPU_HIGH: 3,
}

#: Approximate VRAM available per profile, for warning before a bad selection.
PROFILE_VRAM_GB: Mapping[str, float] = {
    HardwareProfile.CPU: 0.0,
    HardwareProfile.GPU_ENTRY: 8.0,
    HardwareProfile.GPU_MID: 16.0,
    HardwareProfile.GPU_HIGH: 24.0,
}


@dataclass(frozen=True)
class ModelRecommendation:
    """A suggested model for a role at a given hardware profile."""

    spec: ModelSpec
    role: str  # "llm" | "embedding" | "reranker"
    min_profile: str
    preference: int = 100
    """Lower sorts first within a role and profile."""

    def __post_init__(self) -> None:
        if self.min_profile not in PROFILE_RANK:
            raise ValidationError(
                f"Unknown hardware profile: {self.min_profile!r}",
                details={"valid": list(ALL_PROFILES)},
            )
        if self.role not in ("llm", "embedding", "reranker"):
            raise ValidationError(f"Unknown model role: {self.role!r}")


def _llm(
    name: str,
    display: str,
    *,
    context: int,
    vram: float,
    min_profile: str,
    preference: int,
    reasoning: bool = False,
    notes: str = "",
) -> ModelRecommendation:
    capabilities = {
        ModelCapability.TEXT_GENERATION,
        ModelCapability.STRUCTURED_OUTPUT,
        ModelCapability.MULTILINGUAL,
    }
    if context >= 32768:
        capabilities.add(ModelCapability.LONG_CONTEXT)
    if reasoning:
        capabilities.add(ModelCapability.REASONING)
    return ModelRecommendation(
        spec=ModelSpec(
            name=name,
            provider="ollama",
            display_name=display,
            capabilities=frozenset(capabilities),
            context_length=context,
            max_output_tokens=4096,
            default_temperature=0.0,
            requires_gpu=min_profile != HardwareProfile.CPU,
            estimated_vram_gb=vram,
            notes=notes,
        ),
        role="llm",
        min_profile=min_profile,
        preference=preference,
    )


#: Qwen 2.5 Instruct is the default family: strong instruction following,
#: reliable JSON under constrained decoding, genuinely multilingual, and
#: available at sizes spanning every profile from CPU to a 24 GB GPU. That
#: last property matters most — one family across all profiles means the
#: prompt suite behaves consistently as a deployment scales up or down.
BUILTIN_RECOMMENDATIONS: tuple[ModelRecommendation, ...] = (
    _llm("qwen2.5:3b-instruct", "Qwen 2.5 3B Instruct",
         context=32768, vram=2.5, min_profile=HardwareProfile.CPU, preference=10,
         notes="Usable on CPU. Weakest at long multi-clause analysis; adequate "
               "for extraction and classification."),
    _llm("qwen2.5:7b-instruct", "Qwen 2.5 7B Instruct",
         context=32768, vram=5.5, min_profile=HardwareProfile.GPU_ENTRY, preference=20,
         notes="Good general default for an entry GPU."),
    _llm("qwen2.5:14b-instruct", "Qwen 2.5 14B Instruct",
         context=32768, vram=10.0, min_profile=HardwareProfile.GPU_MID, preference=30,
         notes="Recommended default where VRAM allows. Materially better at "
               "notice-compliance and causation reasoning than 7B."),
    _llm("qwen2.5:32b-instruct", "Qwen 2.5 32B Instruct",
         context=32768, vram=20.0, min_profile=HardwareProfile.GPU_HIGH, preference=40,
         reasoning=True,
         notes="Best available quality for claim analysis on a single GPU."),

    ModelRecommendation(
        spec=ModelSpec(
            name="bge-m3",
            provider="ollama",
            display_name="BGE-M3",
            capabilities=frozenset(
                {ModelCapability.EMBEDDING, ModelCapability.MULTILINGUAL,
                 ModelCapability.LONG_CONTEXT}
            ),
            context_length=8192,
            embedding_dimensions=1024,
            estimated_vram_gb=2.5,
            notes="Default embedding model. Long-input tolerant and "
                  "multilingual, which matters on projects where "
                  "correspondence is not all in English.",
        ),
        role="embedding",
        min_profile=HardwareProfile.CPU,
        preference=10,
    ),
    ModelRecommendation(
        spec=ModelSpec(
            name="all-minilm-l6-v2",
            provider="sentence-transformers",
            display_name="all-MiniLM-L6-v2",
            capabilities=frozenset({ModelCapability.EMBEDDING}),
            context_length=512,
            embedding_dimensions=384,
            estimated_vram_gb=0.5,
            notes="Small and fast; English-only with a short window. The model "
                  "the legacy prototype used — retained so imported legacy "
                  "embeddings remain comparable, not as a recommended default.",
        ),
        role="embedding",
        min_profile=HardwareProfile.CPU,
        preference=90,
    ),
    ModelRecommendation(
        spec=ModelSpec(
            name="bge-reranker-v2-m3",
            provider="sentence-transformers",
            display_name="BGE Reranker v2 M3",
            capabilities=frozenset(
                {ModelCapability.RERANKING, ModelCapability.MULTILINGUAL}
            ),
            context_length=8192,
            estimated_vram_gb=2.5,
            notes="Cross-encoder reranker paired with BGE-M3.",
        ),
        role="reranker",
        min_profile=HardwareProfile.CPU,
        preference=10,
    ),
)


@dataclass
class ModelAvailability:
    """Reconciliation between recommended and locally present models."""

    role: str
    recommended: list[ModelSpec] = field(default_factory=list)
    available: list[ModelSpec] = field(default_factory=list)
    missing: list[ModelSpec] = field(default_factory=list)

    @property
    def has_usable_model(self) -> bool:
        return bool(self.available)


class ModelRegistry:
    """Resolves which model to use for a role, given profile and availability."""

    def __init__(
        self,
        recommendations: Iterable[ModelRecommendation] = BUILTIN_RECOMMENDATIONS,
    ) -> None:
        self._recommendations = tuple(recommendations)

    def recommendations_for(
        self, role: str, profile: str, *, include_stronger: bool = False
    ) -> tuple[ModelSpec, ...]:
        """Models suggested for ``role`` on ``profile``.

        By default returns only models the profile can actually run. With
        ``include_stronger`` the caller also sees models requiring more capable
        hardware, so an administrator can be shown what an upgrade would buy.
        """
        if profile not in PROFILE_RANK:
            raise ValidationError(
                f"Unknown hardware profile: {profile!r}",
                details={"valid": list(ALL_PROFILES)},
            )
        rank = PROFILE_RANK[profile]
        matches = [
            r
            for r in self._recommendations
            if r.role == role
            and (include_stronger or PROFILE_RANK[r.min_profile] <= rank)
        ]
        matches.sort(key=lambda r: (r.preference, r.spec.name))
        return tuple(r.spec for r in matches)

    def reconcile(
        self, role: str, profile: str, discovered: Sequence[ModelSpec]
    ) -> ModelAvailability:
        """Compare recommendations against what the runtime reports.

        Matching is by canonical name, so ``qwen2.5:14b-instruct-q4_K_M``
        satisfies ``qwen2.5:14b-instruct`` and an installed ``bge-m3:latest``
        satisfies a ``bge-m3`` recommendation. Quantisation and the implicit
        tag are deployment details and should not read as a different model.
        """
        recommended = self.recommendations_for(role, profile)
        discovered_names = {canonical_model_name(spec.name) for spec in discovered}

        available: list[ModelSpec] = []
        missing: list[ModelSpec] = []
        for spec in recommended:
            if canonical_model_name(spec.name) in discovered_names:
                available.append(spec)
            else:
                missing.append(spec)

        return ModelAvailability(
            role=role,
            recommended=list(recommended),
            available=available,
            missing=missing,
        )

    def select(
        self,
        role: str,
        profile: str,
        discovered: Sequence[ModelSpec],
        *,
        configured: str | None = None,
    ) -> ModelSpec:
        """Choose the model to use for ``role``.

        Resolution order:

        1. An explicitly configured model, if the runtime has it. An
           administrator's choice is honoured even when it is not recommended
           for the profile — they may know something the registry does not.
        2. The highest-preference recommendation that is present locally.

        Raises:
            ModelNotConfiguredError: nothing usable is present. The error names
                what was expected and what was found, because "no model
                configured" without that detail is a frustrating dead end.
        """
        discovered_by_base = {
            canonical_model_name(spec.name): spec for spec in discovered
        }

        if configured:
            match = discovered_by_base.get(canonical_model_name(configured))
            if match is not None:
                return match
            raise ModelNotConfiguredError(
                f"The configured {role} model {configured!r} is not available "
                f"in the local runtime.",
                details={
                    "role": role,
                    "configured": configured,
                    "available": sorted(discovered_by_base),
                    "remedy": "Pull the model into the runtime, or select an available one.",
                },
            )

        availability = self.reconcile(role, profile, discovered)
        if availability.available:
            return availability.available[0]

        raise ModelNotConfiguredError(
            f"No usable {role} model is available for hardware profile {profile!r}.",
            details={
                "role": role,
                "profile": profile,
                "recommended": [s.name for s in availability.recommended],
                "available": sorted(discovered_by_base),
                "remedy": (
                    "Provision one of the recommended models into the local "
                    "runtime, then select it in the administration interface."
                ),
            },
        )

    def warn_if_oversized(self, spec: ModelSpec, profile: str) -> str | None:
        """Return a warning if ``spec`` likely exceeds the profile's VRAM.

        A warning rather than a refusal: VRAM estimates are approximate, and
        quantisation, offloading and shared memory all move the real figure.
        The administrator is better placed to judge than this table is.
        """
        if spec.estimated_vram_gb is None:
            return None
        budget = PROFILE_VRAM_GB.get(profile, 0.0)
        if profile == HardwareProfile.CPU:
            if spec.requires_gpu:
                return (
                    f"{spec.label()} is intended for GPU execution. On CPU it "
                    f"will run, but generation may take minutes per response."
                )
            return None
        if spec.estimated_vram_gb > budget:
            return (
                f"{spec.label()} needs roughly {spec.estimated_vram_gb:.1f} GB of "
                f"VRAM; this profile assumes about {budget:.0f} GB. Expect "
                f"offloading to system memory and much slower generation."
            )
        return None


def _base_name(name: str) -> str:
    """Strip a quantisation suffix from an Ollama model tag.

    ``qwen2.5:14b-instruct-q4_K_M`` -> ``qwen2.5:14b-instruct``.
    """
    if ":" not in name:
        return name.lower()
    repo, _, tag = name.partition(":")
    for marker in ("-q4", "-q5", "-q6", "-q8", "-f16", "-fp16"):
        index = tag.lower().find(marker)
        if index != -1:
            tag = tag[:index]
            break
    return f"{repo}:{tag}".lower()


def canonical_model_name(name: str) -> str:
    """Normalise an Ollama name so the same model compares equal to itself.

    On top of :func:`_base_name`, this resolves the implicit tag: Ollama treats
    a bare ``bge-m3`` as ``bge-m3:latest``, and ``/api/tags`` always reports the
    explicit form. Without this, the registry's ``bge-m3`` recommendation reads
    as missing on a runtime that has it installed, and selecting it reads as a
    change of embedding model — which would wrongly tell an administrator that
    every stored vector had been invalidated.
    """
    base = _base_name(name).strip()
    if not base:
        return ""
    return base if ":" in base else f"{base}:latest"


def same_model(left: str, right: str) -> bool:
    """Whether two names refer to the same model, tags and quantisation aside."""
    if not left or not right:
        return False
    return canonical_model_name(left) == canonical_model_name(right)


DEFAULT_MODEL_REGISTRY = ModelRegistry()
