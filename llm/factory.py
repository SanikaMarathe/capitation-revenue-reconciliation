"""Builds a fully wired LLMClient from config (provider, privacy gateway, cache, meter)."""

from __future__ import annotations

from common.config import Settings
from llm.cache import ResponseCache
from llm.client import LLMClient
from llm.meter import Meter
from llm.mock_provider import MockProvider
from llm.provider import Provider
from privacy.gateway import PrivacyGateway
from privacy.pseudonymise import Pseudonymiser
from privacy.scanner import PromptScanner


def build_provider(settings: Settings, name: str | None = None) -> Provider:
    """`name` (e.g. from a --provider flag) overrides config.yaml."""
    name = name or settings.llm["provider"]
    if name == "mock":
        return MockProvider()
    if name == "anthropic":
        from llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(refusal_fallbacks=bool(settings.llm.get("refusal_fallbacks", True)))
    raise ValueError(f"unknown llm.provider '{name}'")


def build_gateway(settings: Settings, conn=None) -> PrivacyGateway:
    names: set[str] = set()
    dob_by_member: dict[str, str] = {}
    if conn is not None:
        # The scanner is armed with the real identifier values so it can block them.
        # storage.phi is imported here (wiring), never from engine/ or agents/.
        from storage.phi import load_identifier_values

        names, dob_by_member, _ids = load_identifier_values(conn)
    return PrivacyGateway(
        allow_lists=settings.allow_lists,
        pseudonymiser=Pseudonymiser(settings.pseudonym_secret, settings.raw["privacy"]["member_token_prefix"]),
        scanner=PromptScanner(names=names, dob_by_member=dob_by_member),
        egress_log=settings.path("egress_log"),
    )


COST_MODE_OVERRIDES = {"investigate_complex": "claude-haiku-5-5", "memo": "claude-haiku-5-5"}


def build_client(settings: Settings, conn=None, provider: Provider | None = None,
                 cache_mode: str | None = None, cost_mode: bool = False, max_spend_usd=None) -> LLMClient:
    """cost_mode: complex investigations and memos use Haiku unless a configured-model answer is cached.
    max_spend_usd: hard cap on new spend; beyond it, calls are refused and items go to a human."""
    return LLMClient(
        settings=settings,
        provider=provider or build_provider(settings),
        gateway=build_gateway(settings, conn),
        cache=ResponseCache(settings.path("llm_cache_dir")),
        meter=Meter(settings.llm.get("pricing_usd_per_mtok"), conn=conn),
        cache_mode=cache_mode,
        model_overrides=COST_MODE_OVERRIDES if cost_mode else None,
        max_spend_usd=max_spend_usd,
    )
