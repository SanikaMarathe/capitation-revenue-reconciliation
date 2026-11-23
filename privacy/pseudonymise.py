"""Replaces member IDs with stable keyed tokens before anything reaches an LLM.

member_id is a health plan beneficiary number (one of HIPAA's 18 identifiers),
so the raw value never leaves the system. Tokens are HMAC-SHA256 of the ID with a
secret, so they are stable (cache-friendly, consistent across a prompt) but not
reversible without the secret. The reverse map is held in memory only.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any

MEMBER_ID_PATTERN = re.compile(r"\bM\d{6}\b")   # synthetic member IDs look like M000123


class Pseudonymiser:
    def __init__(self, secret: str, prefix: str = "MBR") -> None:
        self._key = secret.encode("utf-8")
        self._prefix = prefix
        self._reverse: dict[str, str] = {}
        self._token_pattern = re.compile(rf"\b{re.escape(prefix)}-[0-9a-f]{{10}}\b")

    def token(self, member_id: str) -> str:
        digest = hmac.new(self._key, member_id.encode("utf-8"), hashlib.sha256).hexdigest()
        tok = f"{self._prefix}-{digest[:10]}"
        self._reverse[tok] = member_id
        return tok

    def tokenise_text(self, text: str) -> str:
        return MEMBER_ID_PATTERN.sub(lambda m: self.token(m.group(0)), text)

    def tokenise(self, obj: Any) -> Any:
        """Recursively replace member IDs inside any JSON-like structure."""
        if isinstance(obj, str):
            return self.tokenise_text(obj)
        if isinstance(obj, dict):
            return {k: self.tokenise(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.tokenise(v) for v in obj]
        return obj

    def reidentify_text(self, text: str) -> str:
        return self._token_pattern.sub(lambda m: self._reverse.get(m.group(0), m.group(0)), text)

    def reidentify(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.reidentify_text(obj)
        if isinstance(obj, dict):
            return {k: self.reidentify(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.reidentify(v) for v in obj]
        return obj
