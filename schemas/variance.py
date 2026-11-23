from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import Field

from schemas.base import LLMOutput, Money, Record, classified
from schemas.enums import (
    DataClass,
    DiscrepancyCode,
    RecommendedAction,
    RuleStrength,
    VarianceLevel,
    VarianceStatus,
)

C2 = DataClass.C2_PHI
C3 = DataClass.C3_FINANCIAL


class Variance(Record):
    variance_id: str = classified(C3)
    run_id: str = classified(C3)
    coverage_month: date = classified(C2)
    member_id: str | None = classified(C2, default=None)
    level: VarianceLevel = classified(C3)
    expected: Money = classified(C3)
    remitted: Money = classified(C3)
    banked: Money | None = classified(C3, default=None)
    delta: Money = classified(C3)
    features: dict[str, Any] = classified(C3, default_factory=dict)
    observed_type: str = classified(C3)
    possible_cause_rule: DiscrepancyCode | None = classified(C3, default=None)
    rule_strength: RuleStrength | None = classified(C3, default=None)
    possible_cause_llm: list[DiscrepancyCode] = classified(C3, default_factory=list)
    llm_confidence: float | None = classified(C3, default=None)
    confirmed_cause: DiscrepancyCode | None = classified(C3, default=None)   # human only
    explanation: str = classified(C3, default="")
    evidence_refs: list[str] = classified(C3, default_factory=list)
    recommended_action: RecommendedAction | None = classified(C3, default=None)
    grounded: bool | None = classified(C3, default=None)
    group_id: str | None = classified(C3, default=None)
    status: VarianceStatus = classified(C3, default=VarianceStatus.OBSERVED)


class VarianceDecision(Record):
    decision_id: str = classified(C3)
    variance_id: str | None = classified(C3, default=None)
    group_id: str | None = classified(C3, default=None)
    decision: str = classified(C3)   # accept | override
    final_category: DiscrepancyCode = classified(C3)
    reviewer: str = classified(C3)
    notes: str = classified(C3, default="")
    ts: datetime = classified(C3)


# ---------- LLM output ----------

class CitedAmount(LLMOutput):
    label: str
    amount: str = Field(pattern=r"^-?\d+\.\d{2}$")


class Finding(LLMOutput):
    possible_causes: list[DiscrepancyCode] = Field(min_length=1, max_length=2)
    explanation: str
    evidence_refs: list[str]
    amounts_cited: list[CitedAmount]
    confidence: float = Field(ge=0.0, le=1.0)
    recommended_action: RecommendedAction
