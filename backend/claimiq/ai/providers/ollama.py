"""Ollama provider implementations.

Speaks Ollama's HTTP API directly with ``httpx`` rather than through LangChain.
The prototype used ``langchain_ollama`` for a single ``invoke`` call and
``langchain_text_splitters`` for one splitter — a large dependency for two
functions, and one that obscured what was actually sent to the model. Here the
request bodies are visible, which matters because structured output and
deterministic seeding are the mechanisms grounding depends on (ADR 0005).

Nothing here reaches outside the deployment. ``base_url`` points at the local
Ollama container.
"""
from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

import httpx

from claimiq.ai.domain.providers import (
    EmbeddingProvider,
    EmbeddingResult,
    GenerationRequest,
    GenerationResult,
    LLMProvider,
    ModelCapability,
    ModelSpec,
)
from claimiq.core.domain.errors import (
    ModelNotConfiguredError,
    ProviderUnavailableError,
    StructuredOutputError,
)
from claimiq.core.logging import get_logger

logger = get_logger("ai.ollama")

#: Model families known to honour a JSON schema under constrained decoding.
#: Checked by name because Ollama's API does not advertise the capability.
_STRUCTURED_OUTPUT_FAMILIES = (
    "qwen2.5", "qwen3", "llama3.1", "llama3.2", "llama3.3",
    "mistral-nemo", "mistral-small", "phi4", "gemma2", "gemma3", "command-r",
)


class OllamaClient:
    """Thin HTTP client shared by the Ollama-backed providers."""

    def __init__(self, base_url: str, timeout_seconds: int = 300) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=self.timeout)

    def get(self, path: str) -> dict[str, Any]:
        try:
            with self._client() as client:
                response = client.get(path)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                "The local model runtime is not reachable.",
                details={"path": path, "reason": type(exc).__name__},
            ) from exc

    def post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            with self._client() as client:
                response = client.post(path, json=dict(payload))
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError(
                "The local model runtime did not respond within the timeout. "
                "On CPU-only hardware, generation can exceed the configured "
                "limit; raise OLLAMA_TIMEOUT_SECONDS or select a smaller model.",
                details={"path": path, "timeout_seconds": self.timeout},
            ) from exc
        except httpx.HTTPStatusError as exc:
            # Ollama returns 404 for an unpulled model. That is a configuration
            # problem with an actionable remedy, not a transport failure.
            if exc.response.status_code == 404:
                raise ModelNotConfiguredError(
                    "The requested model is not present in the local runtime.",
                    details={
                        "path": path,
                        "model": payload.get("model"),
                        "remedy": "Pull the model into Ollama, then select it.",
                    },
                ) from exc
            raise ProviderUnavailableError(
                "The local model runtime returned an error.",
                details={"path": path, "status": exc.response.status_code},
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                "The local model runtime is not reachable.",
                details={"path": path, "reason": type(exc).__name__},
            ) from exc

    def is_reachable(self) -> bool:
        try:
            with self._client() as client:
                client.timeout = httpx.Timeout(5.0)
                return client.get("/api/tags").status_code == 200
        except httpx.HTTPError:
            return False

    def tags(self) -> list[dict[str, Any]]:
        payload = self.get("/api/tags")
        models = payload.get("models")
        return models if isinstance(models, list) else []


def _spec_from_tag(tag: Mapping[str, Any]) -> ModelSpec:
    """Build a ModelSpec from an Ollama /api/tags entry.

    Ollama reports family and parameter size but not context length or
    capabilities, so those are inferred conservatively. The registry's
    curated specs are preferred where a model matches one; this is the
    fallback for anything an administrator pulled themselves.
    """
    name = str(tag.get("name") or tag.get("model") or "")
    details = tag.get("details") or {}
    family = str(details.get("family") or "")
    parameter_size = str(details.get("parameter_size") or "")

    capabilities = {ModelCapability.TEXT_GENERATION}
    lowered = name.lower()
    if any(marker in lowered for marker in _STRUCTURED_OUTPUT_FAMILIES):
        capabilities.add(ModelCapability.STRUCTURED_OUTPUT)
    if "embed" in lowered or "bge" in lowered or family == "bert":
        capabilities = {ModelCapability.EMBEDDING}

    return ModelSpec(
        name=name,
        provider="ollama",
        display_name=name,
        capabilities=frozenset(capabilities),
        notes=f"Discovered in the local runtime. {family} {parameter_size}".strip(),
    )


class OllamaLLMProvider(LLMProvider):
    """Text generation against a local Ollama instance."""

    provider_key = "ollama"

    def __init__(self, base_url: str, timeout_seconds: int = 300) -> None:
        self.client = OllamaClient(base_url, timeout_seconds)

    def is_available(self) -> bool:
        return self.client.is_reachable()

    def list_models(self) -> Sequence[ModelSpec]:
        try:
            return tuple(
                spec
                for spec in (_spec_from_tag(tag) for tag in self.client.tags())
                if spec.name and ModelCapability.EMBEDDING not in spec.capabilities
            )
        except ProviderUnavailableError:
            # Discovery must not raise: the admin UI and health checks need a
            # list or an empty list, never an exception.
            return ()

    def supports_structured_output(self, model: str) -> bool:
        lowered = model.lower()
        return any(marker in lowered for marker in _STRUCTURED_OUTPUT_FAMILIES)

    def generate(self, model: str, request: GenerationRequest) -> GenerationResult:
        options: dict[str, Any] = {
            # Contractual analysis should be reproducible. Sampling variance is
            # not a feature here.
            "temperature": request.temperature if request.temperature is not None else 0.0,
        }
        if request.max_tokens is not None:
            options["num_predict"] = request.max_tokens
        if request.stop_sequences:
            options["stop"] = list(request.stop_sequences)
        if request.seed is not None:
            options["seed"] = request.seed

        payload: dict[str, Any] = {
            "model": model,
            "prompt": request.prompt,
            "stream": False,
            "options": options,
        }
        if request.system_prompt:
            payload["system"] = request.system_prompt

        if request.json_schema is not None:
            if not self.supports_structured_output(model):
                raise StructuredOutputError(
                    f"Model {model!r} is not known to support schema-constrained "
                    f"output, and grounding depends on it.",
                    details={
                        "model": model,
                        "remedy": "Select a model that supports structured output.",
                    },
                )
            payload["format"] = dict(request.json_schema)

        started = time.perf_counter()
        response = self.client.post("/api/generate", payload)
        latency_ms = round((time.perf_counter() - started) * 1000, 1)

        text = str(response.get("response") or "")
        if request.json_schema is not None and not text.strip():
            raise StructuredOutputError(
                "The model returned an empty response where structured output "
                "was required.",
                details={"model": model},
            )

        finish_reason = "length" if response.get("done_reason") == "length" else str(
            response.get("done_reason") or ""
        )

        logger.info(
            "ai.generate",
            extra={
                "model": model,
                "latency_ms": latency_ms,
                "prompt_tokens": response.get("prompt_eval_count"),
                "completion_tokens": response.get("eval_count"),
                "structured": request.json_schema is not None,
            },
        )

        return GenerationResult(
            text=text,
            model=model,
            prompt_tokens=response.get("prompt_eval_count"),
            completion_tokens=response.get("eval_count"),
            latency_ms=latency_ms,
            finish_reason=finish_reason,
            raw_metadata={
                "total_duration_ns": response.get("total_duration"),
                "load_duration_ns": response.get("load_duration"),
            },
        )


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Embeddings from a local Ollama instance."""

    provider_key = "ollama"

    def __init__(self, base_url: str, timeout_seconds: int = 300) -> None:
        self.client = OllamaClient(base_url, timeout_seconds)
        self._dimension_cache: dict[str, int] = {}

    def is_available(self) -> bool:
        return self.client.is_reachable()

    def list_models(self) -> Sequence[ModelSpec]:
        try:
            return tuple(
                spec
                for spec in (_spec_from_tag(tag) for tag in self.client.tags())
                if spec.name and ModelCapability.EMBEDDING in spec.capabilities
            )
        except ProviderUnavailableError:
            return ()

    def embed(self, model: str, texts: Sequence[str]) -> EmbeddingResult:
        """Embed ``texts``, preserving input order.

        Empty input returns an empty result rather than calling the runtime —
        a batch that happens to contain no chunks is normal at the tail of a
        document, not an error.
        """
        if not texts:
            return EmbeddingResult(vectors=[], model=model, dimensions=0)

        started = time.perf_counter()
        response = self.client.post(
            "/api/embed", {"model": model, "input": list(texts)}
        )
        latency_ms = round((time.perf_counter() - started) * 1000, 1)

        vectors = response.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise ProviderUnavailableError(
                "The embedding runtime returned an unexpected number of vectors.",
                details={
                    "model": model,
                    "requested": len(texts),
                    "received": len(vectors) if isinstance(vectors, list) else 0,
                },
            )

        dimensions = len(vectors[0]) if vectors and isinstance(vectors[0], list) else 0
        self._dimension_cache[model] = dimensions

        logger.info(
            "ai.embed",
            extra={
                "model": model,
                "count": len(texts),
                "dimensions": dimensions,
                "latency_ms": latency_ms,
            },
        )

        return EmbeddingResult(
            vectors=vectors, model=model, dimensions=dimensions, latency_ms=latency_ms
        )

    def dimensions(self, model: str) -> int:
        """Output width for ``model``, probed once and cached.

        Checked against the vector column width before any write. A mismatch
        must fail loudly: silently storing a differently-sized vector corrupts
        the index in a way that surfaces much later as poor retrieval.
        """
        cached = self._dimension_cache.get(model)
        if cached:
            return cached
        result = self.embed(model, ["dimension probe"])
        return result.dimensions
