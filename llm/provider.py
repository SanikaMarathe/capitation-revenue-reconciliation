"""Provider abstraction. Agents never talk to a vendor SDK directly.

Add a provider (e.g. Azure OpenAI) by implementing `Provider.generate`.
"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


class LLMError(Exception):
    pass


class LLMRefusal(LLMError):
    """The model (and any fallback) declined. Routed to a human."""


class LLMTruncated(LLMError):
    """Hit max_tokens before finishing."""


class LLMValidationError(LLMError):
    """Output failed schema validation after the allowed retries. Never silently repaired."""


class BudgetExceeded(LLMError):
    """The run's spend cap is reached; the item goes to a human instead of a model."""


class LLMUnavailable(LLMError):
    """The provider could not be reached (network, timeout, rate limit, overload, 5xx) after the SDK's own
    retries. The close still completes: the affected items go to the human review queue."""


class CacheMiss(LLMError):
    """Replay mode and no cached response: guarantees offline runs never call the network."""


@dataclass(frozen=True)
class ProviderRequest:
    task: str
    model: str
    effort: str
    max_tokens: int
    system: str
    user: str
    json_schema: dict[str, Any]


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class Provider(ABC):
    name: str = "base"

    @abstractmethod
    def generate(self, request: ProviderRequest) -> ProviderResponse: ...


# JSON-schema keywords we strip before sending: the API schema is a shape hint,
# Pydantic validation on our side is the real gate (and enforces these constraints).
_STRIP_KEYS = {"pattern", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
               "minLength", "maxLength", "minItems", "maxItems"}


def to_api_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = copy.deepcopy(model.model_json_schema())

    def _clean(node: Any) -> Any:
        if isinstance(node, dict):
            for k in list(node):
                if k in _STRIP_KEYS:
                    del node[k]
            if node.get("type") == "object":
                node["additionalProperties"] = False
            for v in node.values():
                _clean(v)
        elif isinstance(node, list):
            for v in node:
                _clean(v)
        return node

    return _clean(schema)
