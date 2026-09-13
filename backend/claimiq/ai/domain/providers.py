"""AI provider interfaces.

Every AI capability sits behind an interface defined here. No service, task or
domain module imports Ollama, sentence-transformers, or docTR directly.

Two reasons:

1. **The administrator chooses the model, not the developer.** The prototype
   hardcoded ``OLLAMA_MODEL = "mistral"`` (`../backend/rag.py:16`), so changing
   model meant editing source. Here the active model is resolved from
   configuration at call time.

2. **It keeps the extraction path open (ADR 0003).** Moving inference to a
   separate service later means writing an implementation of these interfaces
   that speaks HTTP. Nothing above the interface changes.

The interfaces are ABCs rather than Protocols so that shared validation lives in
one place and a partial implementation fails loudly at instantiation.

Pure stdlib; runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence


class ModelCapability(str, Enum):
    """What a model can do. Declared rather than assumed.

    Requesting structured output from a model that cannot honour a schema
    produces prose that fails validation and burns a retry. Checking the
    capability first turns that into an immediate, explicable error.
    """

    TEXT_GENERATION = "text_generation"
    STRUCTURED_OUTPUT = "structured_output"
    """Constrained decoding against a JSON schema."""
    LONG_CONTEXT = "long_context"
    REASONING = "reasoning"
    EMBEDDING = "embedding"
    RERANKING = "reranking"
    MULTILINGUAL = "multilingual"
    VISION = "vision"


@dataclass(frozen=True)
class ModelSpec:
    """Declaration of a model's identity and limits.

    Attributes:
        name: Provider-specific identifier, e.g. ``qwen2.5:14b-instruct``.
        provider: Provider key, e.g. ``ollama``.
        display_name: Name shown to administrators.
        capabilities: What the model supports.
        context_length: Maximum input tokens.
        max_output_tokens: Maximum tokens to generate.
        embedding_dimensions: Output width, for embedding models.
        default_temperature: 0.0 for analysis work — contractual answers should
            be reproducible, and sampling variance is not a feature here.
        requires_gpu: True when CPU inference is impractically slow.
        estimated_vram_gb: Rough VRAM requirement, used to warn an
            administrator before they select a model the host cannot run.
    """

    name: str
    provider: str
    display_name: str = ""
    capabilities: frozenset[ModelCapability] = frozenset()
    context_length: int = 8192
    max_output_tokens: int = 2048
    embedding_dimensions: int | None = None
    default_temperature: float = 0.0
    requires_gpu: bool = False
    estimated_vram_gb: float | None = None
    notes: str = ""

    def supports(self, capability: ModelCapability) -> bool:
        return capability in self.capabilities

    def label(self) -> str:
        return self.display_name or self.name


@dataclass(frozen=True)
class GenerationRequest:
    """A request to generate text."""

    prompt: str
    system_prompt: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    stop_sequences: Sequence[str] = ()
    json_schema: Mapping[str, Any] | None = None
    """When set, the provider must constrain output to this schema."""
    seed: int | None = None
    """Fixed seed where supported, so an analysis can be reproduced."""


@dataclass(frozen=True)
class GenerationResult:
    """A generated response with the metadata AI observability records."""

    text: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: float = 0.0
    finish_reason: str = ""
    raw_metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def was_truncated(self) -> bool:
        """True when generation stopped at the token limit.

        Worth surfacing: a truncated analysis looks complete but has silently
        lost its conclusion.
        """
        return self.finish_reason in ("length", "max_tokens")


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: Sequence[Sequence[float]]
    model: str
    dimensions: int
    latency_ms: float = 0.0


@dataclass(frozen=True)
class RerankResult:
    """Relevance scores aligned to the input document order."""

    scores: Sequence[float]
    model: str
    latency_ms: float = 0.0


@dataclass(frozen=True)
class ExtractedPage:
    """One page of text produced by an OCR or extraction provider."""

    page_number: int
    text: str
    confidence: float | None = None
    width: float | None = None
    height: float | None = None
    blocks: Sequence[Mapping[str, Any]] = ()
    """Optional layout blocks with bounding boxes, when the engine provides them."""


class AIProvider(ABC):
    """Common behaviour for every provider."""

    #: Stable provider key, e.g. ``ollama``.
    provider_key: str = ""

    @abstractmethod
    def is_available(self) -> bool:
        """True if the backing runtime is reachable.

        Must not raise. Used by health checks and by the admin interface, both
        of which need a boolean rather than an exception.
        """

    @abstractmethod
    def list_models(self) -> Sequence[ModelSpec]:
        """Models the runtime currently has locally.

        Discovery rather than a static list: an air-gapped deployment has
        whatever was provisioned onto it, which the application cannot know in
        advance.
        """


class LLMProvider(AIProvider):
    """Text generation."""

    @abstractmethod
    def generate(self, model: str, request: GenerationRequest) -> GenerationResult:
        """Generate a response.

        Raises:
            ProviderUnavailableError: the runtime is unreachable.
            ModelNotConfiguredError: the model is not available.
            StructuredOutputError: a schema was requested and could not be met.
        """

    @abstractmethod
    def supports_structured_output(self, model: str) -> bool:
        """True if ``model`` can be constrained to a JSON schema."""


class EmbeddingProvider(AIProvider):
    """Dense vector embedding."""

    @abstractmethod
    def embed(self, model: str, texts: Sequence[str]) -> EmbeddingResult:
        """Embed ``texts``. Order of returned vectors matches the input."""

    @abstractmethod
    def dimensions(self, model: str) -> int:
        """Output width for ``model``.

        Checked against the ``vector`` column width before writing. A mismatch
        must fail loudly: silently storing a differently-sized vector corrupts
        the index in a way that surfaces later as inexplicably poor retrieval.
        """


class RerankerProvider(AIProvider):
    """Cross-encoder reranking."""

    @abstractmethod
    def rerank(self, model: str, query: str, documents: Sequence[str]) -> RerankResult:
        """Score each document's relevance to ``query``.

        Returns scores in input order — the caller pairs them with candidates
        positionally, so the provider must not reorder.
        """


class OCRProvider(AIProvider):
    """Text extraction from scanned documents."""

    @abstractmethod
    def extract(self, file_path: str, *, page_numbers: Sequence[int] | None = None) -> Sequence[ExtractedPage]:
        """Extract text. ``page_numbers`` restricts the pages processed.

        Page-range support is required rather than optional: it makes ingestion
        resumable, so a job that fails at page 400 of 500 restarts from 400.
        """


class NullProviderMixin:
    """Shared behaviour for the fake providers used in tests.

    Exists so that the test settings can guarantee no test ever reaches a real
    model runtime. A real call from a test is a test bug, not a slow test.
    """

    provider_key = "null"

    def is_available(self) -> bool:
        return True

    def list_models(self) -> Sequence[ModelSpec]:
        return ()
