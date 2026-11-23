"""The single entry point agents use to call an LLM.

    payload --privacy gateway--> tokenised text --cache?--> provider --> raw JSON
            --Pydantic validation (one retry with the error)--> re-identify --> typed result

There is deliberately no method that takes free text: every call goes through the gateway.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from common.config import Settings
from llm.cache import ResponseCache, cache_key
from llm.meter import Meter
from llm.prompts import Prompt
from llm.provider import (BudgetExceeded, CacheMiss, LLMValidationError, Provider, ProviderRequest,
                          to_api_schema)
from privacy.gateway import PrivacyGateway

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class CallResult(Generic[T]):
    output: T
    cache_hit: bool
    prompt_hash: str
    attempts: int


class LLMClient:
    def __init__(
        self,
        settings: Settings,
        provider: Provider,
        gateway: PrivacyGateway,
        cache: ResponseCache | None,
        meter: Meter,
        cache_mode: str | None = None,
        model_overrides: dict[str, str] | None = None,
        max_spend_usd: Decimal | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._gateway = gateway
        self._cache = cache
        self._meter = meter
        self._cache_mode = cache_mode or settings.llm.get("cache_mode", "read_write")
        self._max_retries = int(settings.llm.get("max_validation_retries", 1))
        # Cost controls: per-task cheaper model (used only when no answer from the configured model is
        # already cached) and a hard cap on NEW spend for this client's lifetime.
        self._overrides = model_overrides or {}
        self._max_spend = max_spend_usd
        self.spent_usd = Decimal("0")
        # What this run WOULD cost with an empty cache (priced from every response's token counts,
        # cached or not). Separates "replay is free" from "an uncached run costs X".
        self.uncached_equivalent_usd = Decimal("0")

    def call(self, *, agent: str, task: str, prompt: Prompt, payload: dict[str, Any],
             output_model: type[T]) -> CallResult[T]:
        tm = self._settings.task_model(task)
        user_text = self._gateway.prepare(agent, payload)
        schema = to_api_schema(output_model)

        last_error = ""
        for attempt in range(1, self._max_retries + 2):
            text = user_text
            if last_error:
                text = f"{user_text}\n\nYour previous output failed validation:\n{last_error}\nReturn corrected JSON only."
                self._gateway.check_retry_text(text)
            request = ProviderRequest(task=task, model=tm.model, effort=tm.effort, max_tokens=tm.max_tokens,
                                      system=prompt.text, user=text, json_schema=schema)
            key = cache_key(request, prompt.version, self._provider.name)
            if task in self._overrides and not self._cached(key):
                # Nothing paid for yet with the configured model: use the cheaper one.
                request = replace(request, model=self._overrides[task])
                key = cache_key(request, prompt.version, self._provider.name)
            response, hit = self._fetch(request, key)
            self.uncached_equivalent_usd += self._meter.cost(response.model, response.input_tokens, response.output_tokens)
            try:
                parsed = output_model.model_validate_json(response.text)
                valid = True
            except ValidationError as exc:
                parsed, valid = None, False
                last_error = _short_errors(exc)
            self._meter.record(agent=agent, task=task, model=response.model, prompt_hash=key, cache_hit=hit,
                               input_tokens=response.input_tokens, output_tokens=response.output_tokens,
                               latency_ms=response.latency_ms, schema_valid=valid)
            if parsed is not None:
                restored = output_model.model_validate(self._gateway.reidentify(parsed.model_dump(mode="json")))
                return CallResult(output=restored, cache_hit=hit, prompt_hash=key, attempts=attempt)
        raise LLMValidationError(f"{task}: output invalid after {self._max_retries + 1} attempts: {last_error}")

    def _cached(self, key: str) -> bool:
        return self._cache is not None and self._cache_mode != "off" and self._cache.get(key) is not None

    def _fetch(self, request: ProviderRequest, key: str):
        if self._cache is not None and self._cache_mode != "off":
            cached = self._cache.get(key)
            if cached is not None:
                return cached, True
            if self._cache_mode == "replay":
                raise CacheMiss(f"replay mode: no cached response for {request.task} ({key[:12]})")
        if self._max_spend is not None and self.spent_usd >= self._max_spend:
            raise BudgetExceeded(f"spend cap ${self._max_spend} reached; {request.task} not called")
        response = self._provider.generate(request)
        self.spent_usd += self._meter.cost(response.model, response.input_tokens, response.output_tokens)
        if self._cache is not None and self._cache_mode == "read_write":
            self._cache.put(key, request, response)
        return response, False


def _short_errors(exc: ValidationError) -> str:
    # Field paths and messages only; no input values echoed back.
    return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:10])
