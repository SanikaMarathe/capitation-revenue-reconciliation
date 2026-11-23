"""Records tokens, cost and latency for every LLM call (including cache hits at $0)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from schemas.audit import LLMCall

_MTOK = Decimal(1_000_000)


class Meter:
    def __init__(self, pricing_usd_per_mtok: dict[str, list[float]] | None = None, conn=None) -> None:
        # Pricing comes from config as numbers; convert via str to keep Decimal exact.
        self._pricing = {m: (Decimal(str(p[0])), Decimal(str(p[1]))) for m, p in (pricing_usd_per_mtok or {}).items()}
        self._conn = conn
        self.calls: list[LLMCall] = []

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> Decimal:
        if model.startswith("mock:"):
            return Decimal("0")          # mock calls are free; never report them as real spend
        base = model
        if base not in self._pricing:
            return Decimal("0")
        p_in, p_out = self._pricing[base]
        return (p_in * input_tokens + p_out * output_tokens) / _MTOK

    def record(self, *, agent: str, task: str, model: str, prompt_hash: str, cache_hit: bool,
               input_tokens: int, output_tokens: int, latency_ms: int, schema_valid: bool) -> LLMCall:
        call = LLMCall(
            call_id=uuid.uuid4().hex,
            agent=agent,
            task=task,
            model=model,
            prompt_hash=prompt_hash,
            cache_hit=cache_hit,
            input_tokens=0 if cache_hit else input_tokens,
            output_tokens=0 if cache_hit else output_tokens,
            latency_ms=0 if cache_hit else latency_ms,
            cost_usd=Decimal("0") if cache_hit else self.cost(model, input_tokens, output_tokens),
            schema_valid=schema_valid,
            ts=datetime.now(timezone.utc),
        )
        self.calls.append(call)
        if self._conn is not None:
            self._conn.execute(
                "INSERT INTO llm_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [call.call_id, call.agent, call.task, call.model, call.prompt_hash, call.cache_hit,
                 call.input_tokens, call.output_tokens, call.latency_ms, call.cost_usd,
                 call.schema_valid, call.ts],
            )
        return call
