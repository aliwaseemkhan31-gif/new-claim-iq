"""Generation against Ollama is always bounded.

A client timeout does not stop Ollama. An unbounded, schema-constrained
generation from a small model looped until it was killed, and every later
request — including a one-word health probe — waited behind it for tens of
minutes. Observed while asking questions over the 294-page N-55 contract.
"""
from __future__ import annotations

from unittest import mock

from claimiq.ai.domain.providers import GenerationRequest
from claimiq.ai.providers.ollama import OllamaLLMProvider


def _sent_options(provider: OllamaLLMProvider, request: GenerationRequest) -> dict:
    with mock.patch.object(
        provider.client, "post", return_value={"response": "ok", "done_reason": "stop"}
    ) as post:
        provider.generate("qwen2.5:3b-instruct", request)
    return post.call_args[0][1]["options"]


def test_a_request_without_a_limit_is_still_bounded() -> None:
    provider = OllamaLLMProvider("http://ollama", max_output_tokens=900)
    options = _sent_options(provider, GenerationRequest(prompt="q"))
    assert options["num_predict"] == 900


def test_the_default_bound_applies_when_none_is_configured() -> None:
    provider = OllamaLLMProvider("http://ollama")
    options = _sent_options(provider, GenerationRequest(prompt="q"))
    assert options["num_predict"] == OllamaLLMProvider.DEFAULT_MAX_OUTPUT_TOKENS


def test_a_request_limit_takes_precedence() -> None:
    provider = OllamaLLMProvider("http://ollama", max_output_tokens=900)
    options = _sent_options(provider, GenerationRequest(prompt="q", max_tokens=64))
    assert options["num_predict"] == 64


def test_context_size_is_sent_when_configured() -> None:
    """Ollama silently drops the start of an over-long prompt otherwise."""
    provider = OllamaLLMProvider("http://ollama", context_tokens=8192)
    assert _sent_options(provider, GenerationRequest(prompt="q"))["num_ctx"] == 8192
    assert "num_ctx" not in _sent_options(OllamaLLMProvider("http://ollama"), GenerationRequest(prompt="q"))
