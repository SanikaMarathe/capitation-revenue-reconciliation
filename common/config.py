"""Loads config.yaml and .env into one typed settings object."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent


class TaskModel(BaseModel):
    model: str
    effort: str
    max_tokens: int


class Settings(BaseModel):
    raw: dict[str, Any]
    root: Path

    @property
    def llm(self) -> dict[str, Any]:
        return self.raw["llm"]

    def task_model(self, task: str) -> TaskModel:
        return TaskModel(**self.llm["tasks"][task])

    def path(self, key: str) -> Path:
        return self.root / self.raw["paths"][key]

    @property
    def allow_lists(self) -> dict[str, list[str]]:
        return self.raw["privacy"]["allow_lists"]

    @property
    def pseudonym_secret(self) -> str:
        secret = os.environ.get("PSEUDONYM_SECRET", "")
        if not secret:
            # Deterministic dev default so tests and the offline demo work out of the box.
            # A real deployment must set this (Key Vault in production).
            secret = "dev-only-pseudonym-secret"
        return secret


@lru_cache(maxsize=1)
def get_settings(config_file: str | None = None) -> Settings:
    load_dotenv(ROOT / ".env")
    cfg_path = Path(config_file) if config_file else ROOT / "config.yaml"
    with open(cfg_path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return Settings(raw=raw, root=ROOT)
