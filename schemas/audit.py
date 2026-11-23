from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from schemas.base import Record, classified
from schemas.enums import ActorType, DataClass

C3 = DataClass.C3_FINANCIAL


class AuditEvent(Record):
    """Append-only. Payloads carry IDs and amounts, never C1 fields."""

    event_id: str = classified(C3)
    ts: datetime = classified(C3)
    actor_type: ActorType = classified(C3)
    actor_id: str = classified(C3)
    action: str = classified(C3)
    entity: str = classified(C3)
    entity_id: str = classified(C3)
    payload: dict[str, Any] = classified(C3, default_factory=dict)
    prev_hash: str = classified(C3)
    hash: str = classified(C3)


class LLMCall(Record):
    call_id: str = classified(C3)
    agent: str = classified(C3)
    task: str = classified(C3)
    model: str = classified(C3)
    prompt_hash: str = classified(C3)
    cache_hit: bool = classified(C3)
    input_tokens: int = classified(C3)
    output_tokens: int = classified(C3)
    latency_ms: int = classified(C3)
    cost_usd: Decimal = classified(C3)
    schema_valid: bool = classified(C3)
    ts: datetime = classified(C3)
