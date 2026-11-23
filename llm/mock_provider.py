"""Deterministic provider for tests and the offline demo. No network, no cost.

Each task registers a responder: a function from the request to a JSON-able answer.
Agents ship their own deterministic responders (e.g. derived from the evidence pack).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from llm.provider import Provider, ProviderRequest, ProviderResponse

Responder = Callable[[ProviderRequest], Any]


class MockProvider(Provider):
    name = "mock"

    def __init__(self, responders: dict[str, Responder] | None = None) -> None:
        self._responders: dict[str, Responder] = dict(responders or {})
        self.calls: list[ProviderRequest] = []

    def register(self, task: str, responder: Responder) -> None:
        self._responders[task] = responder

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        if request.task not in self._responders:
            raise NotImplementedError(f"MockProvider has no responder for task '{request.task}'")
        answer = self._responders[request.task](request)
        text = answer if isinstance(answer, str) else json.dumps(answer)
        return ProviderResponse(
            text=text,
            model=f"mock:{request.model}",
            input_tokens=max(1, len(request.system + request.user) // 4),
            output_tokens=max(1, len(text) // 4),
            latency_ms=0,
        )
