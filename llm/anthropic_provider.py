"""Anthropic (Claude) provider using the official SDK and structured outputs."""

from __future__ import annotations

import time
from typing import Any

from llm.provider import LLMRefusal, LLMTruncated, LLMUnavailable, Provider, ProviderRequest, ProviderResponse

# Models that accept the server-side refusal fallback (Haiku has none).
_FALLBACK_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2")


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, refusal_fallbacks: bool = True, client: Any = None) -> None:
        import anthropic  # imported lazily so tests and offline replay never need it

        self._client = client or anthropic.Anthropic()
        self._fallbacks = refusal_fallbacks

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
            "output_config": {
                "effort": request.effort,
                "format": {"type": "json_schema", "schema": request.json_schema},
            },
        }
        start = time.perf_counter()
        try:
            if self._fallbacks and request.model in _FALLBACK_MODELS:
                response = self._client.beta.messages.create(
                    **kwargs,
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
            else:
                response = self._client.messages.create(**kwargs)
        except Exception as exc:
            # The SDK has already retried with backoff. A transient outage becomes LLMUnavailable so the
            # item goes to a human; configuration errors (bad key, bad request) still fail loudly.
            if _is_transient(exc):
                raise LLMUnavailable(f"{request.task}: provider unavailable ({type(exc).__name__})") from exc
            raise
        latency_ms = int((time.perf_counter() - start) * 1000)

        if response.stop_reason == "refusal":
            raise LLMRefusal(f"model declined ({getattr(response, 'stop_details', None)})")
        if response.stop_reason == "max_tokens":
            raise LLMTruncated("output hit max_tokens")

        text = next((b.text for b in response.content if b.type == "text"), "")
        return ProviderResponse(
            text=text,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=latency_ms,
        )


def _is_transient(exc: Exception) -> bool:
    import anthropic

    if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError, anthropic.RateLimitError,
                        anthropic.InternalServerError)):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(exc, anthropic.APIStatusError) and status is not None and (status == 429 or status >= 500)
