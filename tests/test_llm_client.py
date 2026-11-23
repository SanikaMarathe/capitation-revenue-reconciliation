"""LLM plumbing on MockProvider: validation, retry, cache, replay, metering, re-identification."""

from __future__ import annotations

import pytest

from llm.cache import ResponseCache
from llm.client import LLMClient
from llm.meter import Meter
from llm.mock_provider import MockProvider
from llm.prompts import Prompt
from llm.provider import CacheMiss, LLMValidationError, to_api_schema
from schemas.variance import Finding

PROMPT = Prompt(name="test", version="1", text="You explain variances.")
PAYLOAD = {"variance": {"member_id": "M000123", "delta": "-50.00"}}


def good_finding(request):
    # Mock reads the tokenised member reference straight from the prompt, like a real model would.
    token = next(w.strip('",') for w in request.user.split() if w.strip('",').startswith("MBR-"))
    return {
        "possible_causes": ["STALE_RATE"],
        "explanation": f"Member {token} was paid at the old rate.",
        "evidence_refs": [f"RL-1:{token}"],
        "amounts_cited": [{"label": "delta", "amount": "-50.00"}],
        "confidence": 0.9,
        "recommended_action": "dispute_rate_with_state",
    }


def make_client(settings, gateway, tmp_path, responder, cache_mode="read_write"):
    provider = MockProvider({"investigate_simple": responder})
    client = LLMClient(settings=settings, provider=provider, gateway=gateway,
                       cache=ResponseCache(tmp_path / "cache"), meter=Meter({"claude-haiku-5-5": [0.10, 0.50]}),
                       cache_mode=cache_mode)
    return client, provider


def call(client):
    return client.call(agent="investigator", task="investigate_simple", prompt=PROMPT,
                       payload=PAYLOAD, output_model=Finding)


def test_valid_output_is_parsed_and_reidentified(settings, gateway, tmp_path):
    client, provider = make_client(settings, gateway, tmp_path, good_finding)
    result = call(client)
    assert result.output.possible_causes[0].value == "STALE_RATE"
    # The model only ever saw a token; our side gets the real ID back.
    assert "M000123" not in provider.calls[0].user
    assert "M000123" in result.output.explanation
    assert result.output.evidence_refs == ["RL-1:M000123"]


def test_second_call_is_a_cache_hit(settings, gateway, tmp_path):
    client, provider = make_client(settings, gateway, tmp_path, good_finding)
    call(client)
    second = call(client)
    assert second.cache_hit is True
    assert len(provider.calls) == 1


def test_replay_mode_never_calls_provider(settings, gateway, tmp_path):
    client, _ = make_client(settings, gateway, tmp_path, good_finding)
    call(client)   # record
    replay, provider = make_client(settings, gateway, tmp_path, good_finding, cache_mode="replay")
    assert call(replay).cache_hit is True
    assert provider.calls == []


def test_cache_never_crosses_providers(settings, gateway, tmp_path):
    client, _ = make_client(settings, gateway, tmp_path, good_finding)
    call(client)                                    # cached under provider "mock"
    other = MockProvider({"investigate_simple": good_finding})
    other.name = "anthropic"                        # pretend to be the live provider
    live = LLMClient(settings=settings, provider=other, gateway=gateway, cache=ResponseCache(tmp_path / "cache"),
                     meter=Meter(), cache_mode="replay")
    with pytest.raises(CacheMiss):
        call(live)


def test_provider_flag_overrides_config(settings):
    """Regression: --provider anthropic must not silently fall back to config.yaml's mock."""
    from llm.factory import build_provider
    assert settings.llm["provider"] == "mock"
    assert type(build_provider(settings, "anthropic")).__name__ == "AnthropicProvider"
    assert type(build_provider(settings)).__name__ == "MockProvider"


def test_cost_mode_reuses_cached_original_model_answer(settings, gateway, tmp_path):
    client, provider = make_client(settings, gateway, tmp_path, good_finding)
    call(client)                                                   # paid once on the configured model
    cheap = LLMClient(settings=settings, provider=provider, gateway=gateway, cache=ResponseCache(tmp_path / "cache"),
                      meter=Meter(), model_overrides={"investigate_simple": "cheaper-model"})
    assert call(cheap).cache_hit is True                           # not re-bought on the cheaper model
    assert provider.calls[-1].model != "cheaper-model"


def test_cost_mode_uses_cheaper_model_when_nothing_cached(settings, gateway, tmp_path):
    provider = MockProvider({"investigate_simple": good_finding})
    cheap = LLMClient(settings=settings, provider=provider, gateway=gateway, cache=ResponseCache(tmp_path / "c2"),
                      meter=Meter(), model_overrides={"investigate_simple": "cheaper-model"})
    call(cheap)
    assert provider.calls[0].model == "cheaper-model"


def test_spend_cap_refuses_new_calls(settings, gateway, tmp_path):
    from decimal import Decimal
    from llm.provider import BudgetExceeded
    provider = MockProvider({"investigate_simple": good_finding})
    provider.name = "anthropic"
    capped = LLMClient(settings=settings, provider=provider, gateway=gateway, cache=None,
                       meter=Meter({"claude-haiku-5-5": [0.10, 0.50]}), max_spend_usd=Decimal("0"))
    with pytest.raises(BudgetExceeded):
        call(capped)
    assert provider.calls == []


def test_replay_miss_is_an_error(settings, gateway, tmp_path):
    client, _ = make_client(settings, gateway, tmp_path, good_finding, cache_mode="replay")
    with pytest.raises(CacheMiss):
        call(client)


def test_invalid_output_retries_once_then_rejects(settings, gateway, tmp_path):
    client, provider = make_client(settings, gateway, tmp_path, lambda r: {"possible_causes": ["NOT_A_CODE"]})
    with pytest.raises(LLMValidationError):
        call(client)
    assert len(provider.calls) == 2
    assert "failed validation" in provider.calls[1].user


def test_retry_can_recover(settings, gateway, tmp_path):
    answers = iter([{"bad": True}, None])

    def flaky(request):
        a = next(answers)
        return a if a is not None else good_finding(request)

    client, _ = make_client(settings, gateway, tmp_path, flaky)
    result = call(client)
    assert result.attempts == 2


def test_every_call_is_metered(settings, gateway, tmp_path):
    client, _ = make_client(settings, gateway, tmp_path, good_finding)
    call(client)
    call(client)
    meter = client._meter
    assert [c.cache_hit for c in meter.calls] == [False, True]
    assert meter.calls[1].cost_usd == 0
    assert meter.cost("claude-haiku-5-5", 1_000_000, 1_000_000) > 0     # real model is priced
    assert meter.cost("mock:claude-haiku-5-5", 1_000_000, 1_000_000) == 0


def test_cache_contains_no_raw_member_ids(settings, gateway, tmp_path):
    client, _ = make_client(settings, gateway, tmp_path, good_finding)
    call(client)
    records = ResponseCache(tmp_path / "cache").iter_records()
    assert records and all("M000123" not in str(r) for r in records)


def test_api_schema_is_closed_and_strips_unsupported_keywords():
    schema = to_api_schema(Finding)
    text = str(schema)
    assert "'pattern'" not in text and "'minimum'" not in text
    assert schema["additionalProperties"] is False
