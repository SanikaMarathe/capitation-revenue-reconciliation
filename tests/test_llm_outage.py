"""If the model provider is down, the close still completes and the AI work goes to a human.

Transient outages (network, timeout, rate limit, overload, 5xx) become LLMUnavailable after the SDK's own
retries. Configuration errors (bad key, bad request) still fail loudly so they can't hide as 'needs human'.
"""

from __future__ import annotations

import anthropic
import httpx2 as httpx
import pytest

from agents import mock_responders
from llm.anthropic_provider import AnthropicProvider
from llm.mock_provider import MockProvider
from llm.provider import LLMUnavailable, ProviderRequest
from schemas.enums import VarianceStatus
from storage import repo
from workflow import pipeline

_REQ = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
REQUEST = ProviderRequest(task="investigate_simple", model="claude-haiku-5-5", effort="low", max_tokens=100,
                          system="s", user="u", json_schema={"type": "object"})


class _FailingMessages:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def create(self, **_kwargs):
        raise self._exc


class _FailingClient:
    def __init__(self, exc: Exception) -> None:
        self.messages = _FailingMessages(exc)
        self.beta = type("Beta", (), {"messages": _FailingMessages(exc)})()


def _status_error(cls, status: int) -> Exception:
    return cls("error", response=httpx.Response(status, request=_REQ), body=None)


@pytest.mark.parametrize("exc", [
    anthropic.APIConnectionError(request=_REQ),
    anthropic.APITimeoutError(request=_REQ),
    _status_error(anthropic.RateLimitError, 429),
    _status_error(anthropic.InternalServerError, 529),          # overloaded
])
def test_transient_provider_errors_become_llm_unavailable(exc):
    provider = AnthropicProvider(client=_FailingClient(exc))
    with pytest.raises(LLMUnavailable):
        provider.generate(REQUEST)


def test_configuration_errors_still_fail_loudly():
    provider = AnthropicProvider(client=_FailingClient(_status_error(anthropic.AuthenticationError, 401)))
    with pytest.raises(anthropic.AuthenticationError):
        provider.generate(REQUEST)


def test_close_completes_and_variances_go_to_humans_when_investigator_is_down(monkeypatch):
    """Extraction works, then the provider goes down for every investigation call."""
    def down(_request):
        raise LLMUnavailable("simulated outage")

    def outage_provider() -> MockProvider:
        p = mock_responders.mock_provider()
        p.register("investigate_simple", down)
        p.register("investigate_complex", down)
        return p

    monkeypatch.setattr(pipeline, "mock_provider", outage_provider)
    conn, report = pipeline.run(":memory:", provider="mock", cache_mode="off")

    inv = report.investigation
    assert inv["variances"] == 462                                 # deterministic core unaffected
    statuses = {s for (s,) in conn.execute("SELECT DISTINCT status FROM variances").fetchall()}
    assert statuses == {VarianceStatus.NEEDS_HUMAN.value}           # nothing auto-explained, nothing lost
    total = sum((l.delta for l in repo.active_adjustment_lines(conn)), 0)
    assert str(total) == "23931.00"                                # money is identical with the AI down
