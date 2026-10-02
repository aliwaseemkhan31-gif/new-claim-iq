"""Scoring measured models into a recommendation per role.

The question an administrator actually has is not "which model is best" but
"which model should this installation use for each job it does". Those are
different answers, because the jobs have different shapes:

* **Answering** is interactive. Somebody is watching a spinner. A 32B model
  that is right more often is still the wrong choice if every question costs
  four minutes.
* **Drafting a claim from a photograph** runs once per claim and its output is
  retyped by hand when it is wrong. Here quality wins: measured on a real
  submission, 3B read 4 fields of 14 and 7B read 7. Slower is fine.
* **Embedding** is bulk work on a background queue, but its output width is
  fixed by the database column. Dimensions are a hard gate, not a preference.

So this module scores the same measurements three different ways, and states
why in words the UI can show. Every recommendation carries its reasoning —
"use this one" without a reason invites an administrator to override it on a
hunch.

Pure stdlib; no Django import, no I/O. See ADR 0001.
"""
from __future__ import annotations

from dataclasses import dataclass, field

ROLE_LLM = "llm"
ROLE_DRAFTING = "drafting_llm"
ROLE_EMBEDDING = "embedding"

SELECTABLE_ROLES = (ROLE_LLM, ROLE_DRAFTING, ROLE_EMBEDDING)

ROLE_LABELS = {
    ROLE_LLM: "Answering and claim analysis",
    ROLE_DRAFTING: "Drafting a claim from a document",
    ROLE_EMBEDDING: "Document embedding and search",
}

#: Shape of the work each role does, in tokens. Used to turn a throughput
#: measurement into the figure an administrator cares about: how long will it
#: take. The prompt figures come from the real prompts — eight retrieved
#: passages for answering, 12,000 characters of document for drafting.
WORKLOAD_PROMPT_TOKENS = {ROLE_LLM: 3200, ROLE_DRAFTING: 3600}
WORKLOAD_OUTPUT_TOKENS = {ROLE_LLM: 600, ROLE_DRAFTING: 800}

#: Above this, an interactive answer has stopped being interactive. Not a
#: refusal — a heavy penalty, because on a CPU-only installation every option
#: is over it and the administrator still needs the least bad one.
INTERACTIVE_BUDGET_SECONDS = 90.0

#: A drafting pass longer than this will hit the request timeout before it
#: finishes, so the model is not a candidate however good it is.
DRAFTING_BUDGET_SECONDS = 600.0

#: Parameter count to a quality score. Interpolated between these points
#: rather than computed, because the relationship is not a formula — it is
#: what these model families are observed to manage on contract text.
_QUALITY_POINTS: tuple[tuple[float, float], ...] = (
    (1.0, 15.0),
    (3.0, 40.0),
    (7.0, 65.0),
    (14.0, 85.0),
    (32.0, 95.0),
    (70.0, 100.0),
)


@dataclass(frozen=True)
class ModelProbe:
    """Everything measured about one model, including the failures.

    A model that errored or timed out is kept rather than dropped: "we tried
    it and it took four minutes" is the most useful thing the run can tell an
    administrator about a model they were considering.
    """

    name: str
    display_name: str = ""
    parameter_billions: float | None = None
    quantization: str = ""
    context_length: int | None = None
    size_bytes: int | None = None

    #: Generation measurements. ``None`` where the probe did not get that far.
    prompt_tokens_per_second: float | None = None
    output_tokens_per_second: float | None = None
    latency_ms: float | None = None
    structured_output_ok: bool | None = None
    fields_read: int | None = None
    """How many of the benchmark document's facts the model read correctly.

    Recorded for the administrator to weigh, never scored. Six fields of one
    document is far too small a sample to rank quality on, and treating it as
    one would be the sort of false precision that gets a weak model selected
    with confidence.
    """

    #: Embedding measurements.
    embedding_dimensions: int | None = None
    embedding_latency_ms: float | None = None

    #: Fraction of the loaded model Ollama placed in video memory. 0.0 means
    #: it ran on the processor; 1.0 means it fitted entirely on the GPU.
    gpu_resident_fraction: float | None = None
    vram_bytes: int | None = None

    is_recommended_by_registry: bool = False
    was_pulled: bool = False
    timed_out: bool = False
    error: str = ""

    @property
    def label(self) -> str:
        return self.display_name or self.name

    @property
    def ran(self) -> bool:
        return not self.error and not self.timed_out

    def estimated_seconds(self, role: str) -> float | None:
        """How long this model would take on ``role``'s real workload."""
        if role == ROLE_EMBEDDING:
            if self.embedding_latency_ms is None:
                return None
            return self.embedding_latency_ms / 1000.0
        if not self.output_tokens_per_second:
            return None
        output = WORKLOAD_OUTPUT_TOKENS.get(role, 600) / self.output_tokens_per_second
        prompt = 0.0
        if self.prompt_tokens_per_second:
            prompt = WORKLOAD_PROMPT_TOKENS.get(role, 3200) / self.prompt_tokens_per_second
        return round(prompt + output, 1)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "parameter_billions": self.parameter_billions,
            "quantization": self.quantization or None,
            "context_length": self.context_length,
            "size_bytes": self.size_bytes,
            "prompt_tokens_per_second": _rounded(self.prompt_tokens_per_second),
            "output_tokens_per_second": _rounded(self.output_tokens_per_second),
            "latency_ms": _rounded(self.latency_ms),
            "structured_output_ok": self.structured_output_ok,
            "fields_read": self.fields_read,
            "embedding_dimensions": self.embedding_dimensions,
            "embedding_latency_ms": _rounded(self.embedding_latency_ms),
            "gpu_resident_fraction": _rounded(self.gpu_resident_fraction, 2),
            "vram_bytes": self.vram_bytes,
            "is_recommended_by_registry": self.is_recommended_by_registry,
            "was_pulled": self.was_pulled,
            "timed_out": self.timed_out,
            "error": self.error or None,
            "estimated_seconds": {
                role: self.estimated_seconds(role)
                for role in SELECTABLE_ROLES
                if self.estimated_seconds(role) is not None
            },
        }


@dataclass(frozen=True)
class RoleCandidate:
    """One model considered for one role, scored and explained."""

    probe: ModelProbe
    role: str
    score: float
    eligible: bool
    rationale: str
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "model": self.probe.name,
            "label": self.probe.label,
            "score": round(self.score, 1),
            "eligible": self.eligible,
            "rationale": self.rationale,
            "warnings": list(self.warnings),
            "estimated_seconds": self.probe.estimated_seconds(self.role),
        }


@dataclass(frozen=True)
class RoleRecommendation:
    role: str
    candidates: tuple[RoleCandidate, ...]

    @property
    def best(self) -> RoleCandidate | None:
        for candidate in self.candidates:
            if candidate.eligible:
                return candidate
        return None

    def as_dict(self) -> dict:
        best = self.best
        return {
            "role": self.role,
            "label": ROLE_LABELS.get(self.role, self.role),
            "recommended": best.probe.name if best else None,
            "rationale": best.rationale if best else self._nothing_eligible(),
            "warnings": list(best.warnings) if best else [],
            "candidates": [c.as_dict() for c in self.candidates],
        }

    def _nothing_eligible(self) -> str:
        if not self.candidates:
            return "No model in the local runtime can do this job."
        reasons = {c.rationale for c in self.candidates}
        return (
            "No model is usable for this job. "
            + " ".join(sorted(reasons)[:3])
        )


def quality_points(parameter_billions: float | None) -> float:
    """Quality score from parameter count, interpolated across known points.

    Parameter count is a crude proxy and is used as one, not as a verdict: it
    sets roughly half the score for answering and most of it for drafting,
    where being right matters more than being quick.
    """
    if not parameter_billions or parameter_billions <= 0:
        return 35.0  # Unknown size: assume mid-small rather than exclude.
    points = _QUALITY_POINTS
    if parameter_billions <= points[0][0]:
        return points[0][1]
    if parameter_billions >= points[-1][0]:
        return points[-1][1]
    for (low_p, low_s), (high_p, high_s) in zip(points, points[1:]):
        if low_p <= parameter_billions <= high_p:
            span = high_p - low_p
            fraction = (parameter_billions - low_p) / span if span else 0.0
            return low_s + fraction * (high_s - low_s)
    return 50.0


def speed_points(seconds: float | None, budget: float) -> float:
    """Score how a measured duration sits against a role's budget.

    Full marks well inside the budget, falling away steeply past it rather
    than to zero — on a processor-only installation everything is over budget
    and the ranking still has to be meaningful.
    """
    if seconds is None:
        return 0.0
    if seconds <= budget * 0.25:
        return 100.0
    if seconds <= budget:
        # Linear from 100 down to 55 across the budget.
        fraction = (seconds - budget * 0.25) / (budget * 0.75)
        return 100.0 - fraction * 45.0
    overshoot = seconds / budget
    if overshoot <= 2:
        return 40.0
    if overshoot <= 4:
        return 20.0
    return 5.0


def _score_generation_role(
    probe: ModelProbe, role: str, *, quality_weight: float, budget: float
) -> RoleCandidate:
    warnings: list[str] = []

    if probe.timed_out:
        return RoleCandidate(
            probe,
            role,
            0.0,
            False,
            f"{probe.label} did not finish the benchmark within the time allowed.",
        )
    if probe.error:
        return RoleCandidate(
            probe, role, 0.0, False, f"{probe.label} could not be run: {probe.error}"
        )
    if probe.structured_output_ok is False:
        return RoleCandidate(
            probe,
            role,
            0.0,
            False,
            (
                f"{probe.label} did not return valid output against a JSON "
                f"schema. Every citation this system produces depends on "
                f"schema-constrained output, so a model that cannot honour one "
                f"cannot be used here."
            ),
        )

    seconds = probe.estimated_seconds(role)
    if seconds is None:
        return RoleCandidate(
            probe, role, 0.0, False, f"{probe.label} produced no throughput measurement."
        )
    if role == ROLE_DRAFTING and seconds > DRAFTING_BUDGET_SECONDS:
        return RoleCandidate(
            probe,
            role,
            0.0,
            False,
            (
                f"{probe.label} would take about {_minutes(seconds)} for one "
                f"document, which exceeds the drafting timeout."
            ),
        )

    quality = quality_points(probe.parameter_billions)
    speed = speed_points(seconds, budget)
    score = quality_weight * quality + (1.0 - quality_weight) * speed

    if probe.is_recommended_by_registry:
        # A small thumb on the scale for a model whose prompt behaviour this
        # application's prompt suite was written against.
        score += 4.0

    if probe.gpu_resident_fraction is not None and 0.0 < probe.gpu_resident_fraction < 0.95:
        percent = round(probe.gpu_resident_fraction * 100)
        warnings.append(
            f"Only about {percent}% of this model fitted in video memory; the "
            f"remainder ran on the processor, which is what makes it slow."
        )
    if probe.structured_output_ok is None:
        warnings.append(
            "Schema-constrained output was not verified for this model."
        )
    if seconds > budget:
        warnings.append(
            f"About {_minutes(seconds)} per {'document' if role == ROLE_DRAFTING else 'answer'}, "
            f"against a comfortable budget of {_minutes(budget)}."
        )
    if probe.context_length is not None and probe.context_length < 8192:
        warnings.append(
            f"A {probe.context_length}-token context window is short for this "
            f"work; retrieved passages and the grounding instructions together "
            f"exceed it, and the start of the prompt is dropped silently."
        )

    rationale = (
        f"{probe.label} answers in about {_minutes(seconds)} on this machine"
        f"{_size_phrase(probe)}."
    )
    if role == ROLE_DRAFTING:
        rationale = (
            f"{probe.label} reads a document in about {_minutes(seconds)}"
            f"{_size_phrase(probe)}. Drafting runs once per claim, so the "
            f"heavier model is worth the wait."
        )

    return RoleCandidate(probe, role, score, True, rationale, tuple(warnings))


def _score_embedding(probe: ModelProbe, required_dimensions: int) -> RoleCandidate:
    if probe.error:
        return RoleCandidate(
            probe, ROLE_EMBEDDING, 0.0, False,
            f"{probe.label} could not be run: {probe.error}",
        )
    if probe.embedding_dimensions is None:
        return RoleCandidate(
            probe, ROLE_EMBEDDING, 0.0, False,
            f"{probe.label} returned no vectors, so its width is unknown.",
        )
    if probe.embedding_dimensions != required_dimensions:
        return RoleCandidate(
            probe, ROLE_EMBEDDING, 0.0, False,
            (
                f"{probe.label} produces {probe.embedding_dimensions}-dimension "
                f"vectors; the database column stores {required_dimensions}. "
                f"Changing width needs a migration and a full re-embed, so this "
                f"model cannot simply be selected."
            ),
        )

    seconds = probe.estimated_seconds(ROLE_EMBEDDING) or 0.0
    # Embedding is bulk background work: throughput is the whole story, and
    # quality differences between models of the right width are not something
    # this benchmark can measure honestly.
    score = speed_points(seconds, 2.0)
    if probe.is_recommended_by_registry:
        score += 10.0
    return RoleCandidate(
        probe,
        ROLE_EMBEDDING,
        score,
        True,
        (
            f"{probe.label} produces vectors of the right width "
            f"({probe.embedding_dimensions}) and embedded the test batch in "
            f"{seconds:.1f} s."
        ),
    )


def recommend(
    probes: list[ModelProbe],
    *,
    embedding_probes: list[ModelProbe] | None = None,
    required_embedding_dimensions: int,
    interactive_budget_seconds: float = INTERACTIVE_BUDGET_SECONDS,
) -> dict[str, RoleRecommendation]:
    """Rank ``probes`` for each selectable role.

    Args:
        probes: Generation models that were measured.
        embedding_probes: Embedding models that were measured.
        required_embedding_dimensions: Width of the stored vector column. A
            hard gate, not a preference.
        interactive_budget_seconds: How long an answer may take before it
            stops feeling interactive.
    """
    recommendations: dict[str, RoleRecommendation] = {}

    answering = sorted(
        (
            _score_generation_role(
                p, ROLE_LLM, quality_weight=0.45, budget=interactive_budget_seconds
            )
            for p in probes
        ),
        key=lambda c: (not c.eligible, -c.score, c.probe.name),
    )
    recommendations[ROLE_LLM] = RoleRecommendation(ROLE_LLM, tuple(answering))

    drafting = sorted(
        (
            _score_generation_role(
                p, ROLE_DRAFTING, quality_weight=0.75, budget=DRAFTING_BUDGET_SECONDS / 3
            )
            for p in probes
        ),
        key=lambda c: (not c.eligible, -c.score, c.probe.name),
    )
    recommendations[ROLE_DRAFTING] = RoleRecommendation(ROLE_DRAFTING, tuple(drafting))

    embeddings = sorted(
        (
            _score_embedding(p, required_embedding_dimensions)
            for p in (embedding_probes or [])
        ),
        key=lambda c: (not c.eligible, -c.score, c.probe.name),
    )
    recommendations[ROLE_EMBEDDING] = RoleRecommendation(ROLE_EMBEDDING, tuple(embeddings))

    return recommendations


def parameter_billions(parameter_size: str) -> float | None:
    """Parse Ollama's ``parameter_size`` string, e.g. ``"7.6B"`` -> ``7.6``.

    Returns None rather than guessing when the string is not recognised: a
    wrong parameter count silently skews every quality score in the run.
    """
    text = (parameter_size or "").strip().upper()
    if not text:
        return None
    multiplier = 1.0
    if text.endswith("B"):
        text = text[:-1]
    elif text.endswith("M"):
        text = text[:-1]
        multiplier = 0.001
    try:
        return round(float(text) * multiplier, 2)
    except ValueError:
        return None


def _rounded(value: float | None, places: int = 1) -> float | None:
    return None if value is None else round(value, places)


def _minutes(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f} s"
    return f"{seconds / 60:.1f} min"


def _size_phrase(probe: ModelProbe) -> str:
    if not probe.parameter_billions:
        return ""
    gpu = ""
    if probe.gpu_resident_fraction is not None:
        gpu = " on the GPU" if probe.gpu_resident_fraction >= 0.95 else " on the processor"
    return f", at {probe.parameter_billions:g}B parameters{gpu}"
