"""Prompt files live here as <name>.md, first line 'version: <v>'.

The version is part of the LLM cache key, so editing a prompt and bumping its
version invalidates old cached answers and shows up in eval runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROMPT_DIR = Path(__file__).parent


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    text: str


def load_prompt(name: str) -> Prompt:
    raw = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    first, _, rest = raw.partition("\n")
    if not first.startswith("version:"):
        raise ValueError(f"prompt {name} must start with 'version: <v>'")
    return Prompt(name=name, version=first.split(":", 1)[1].strip(), text=rest.strip())
