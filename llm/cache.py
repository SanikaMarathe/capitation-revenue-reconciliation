"""Disk cache of raw LLM responses, keyed by a hash of everything that shapes the answer.

Enables offline replay and reproducible evals. Prompts are already pseudonymised
before they reach the key, so the cache never holds raw member IDs or names.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from llm.provider import ProviderRequest, ProviderResponse


def cache_key(request: ProviderRequest, prompt_version: str, provider: str) -> str:
    # Provider is part of the key so a mock answer can never be replayed as a live model's answer.
    material = json.dumps(
        {
            "provider": provider,
            "model": request.model,
            "effort": request.effort,
            "prompt_version": prompt_version,
            "system": request.system,
            "user": request.user,
            "schema": request.json_schema,
        },
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class ResponseCache:
    def __init__(self, directory: Path) -> None:
        self._dir = directory

    def _file(self, key: str) -> Path:
        return self._dir / key[:2] / f"{key}.json"

    def get(self, key: str) -> ProviderResponse | None:
        path = self._file(key)
        if not path.exists():
            return None
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return ProviderResponse(**data["response"])

    def put(self, key: str, request: ProviderRequest, response: ProviderResponse) -> None:
        path = self._file(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"key": key, "task": request.task, "user": request.user, "response": asdict(response)}
        path.write_text(json.dumps(record, indent=1), encoding="utf-8")

    def iter_records(self) -> list[dict[str, Any]]:
        """Used by the privacy eval to scan everything the cache holds."""
        if not self._dir.exists():
            return []
        return [json.loads(p.read_text(encoding="utf-8")) for p in self._dir.rglob("*.json")]
