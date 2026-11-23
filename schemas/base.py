"""Shared building blocks: Money type and privacy-classified fields."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field

from schemas.enums import DataClass

CENT = Decimal("0.01")


def _reject_float(value: Any) -> Any:
    # Floats can't represent cents exactly. Money must arrive as Decimal, int or str.
    if isinstance(value, float):
        raise ValueError("money must not be a float; pass Decimal or str")
    return value


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


Money = Annotated[Decimal, BeforeValidator(_reject_float), AfterValidator(_quantize)]


def classified(data_class: DataClass, **kwargs: Any) -> Any:
    """A pydantic Field tagged with its privacy class (read by privacy.classification)."""
    extra = dict(kwargs.pop("json_schema_extra", {}) or {})
    extra["data_class"] = data_class.value
    return Field(json_schema_extra=extra, **kwargs)


class Record(BaseModel):
    """Base for internal records."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class LLMOutput(BaseModel):
    """Base for anything an LLM returns. Unknown fields are rejected, never ignored."""

    model_config = ConfigDict(extra="forbid")
