"""Privacy gateway: every LLM payload passes through here (ARCHITECTURE.md section 9).

Steps: 1) allow-list the payload's fields for this agent
       2) pseudonymise member IDs
       3) serialise deterministically
       4) scan the final text; block on any hit
       5) append what was sent (tokenised) to the egress log
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from privacy.pseudonymise import MEMBER_ID_PATTERN, Pseudonymiser
from privacy.scanner import PrivacyViolation, PromptScanner


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):          # Enums
        return value.value
    return str(value)                    # Decimal -> exact string


class PrivacyGateway:
    def __init__(
        self,
        allow_lists: dict[str, list[str]],
        pseudonymiser: Pseudonymiser,
        scanner: PromptScanner,
        egress_log: Path | None = None,
    ) -> None:
        self._allow = {agent: set(fields) for agent, fields in allow_lists.items()}
        self.pseudonymiser = pseudonymiser
        self.scanner = scanner
        self._egress_log = egress_log

    def prepare(self, agent: str, payload: dict[str, Any]) -> str:
        if agent not in self._allow:
            raise PrivacyViolation(f"no allow-list configured for agent '{agent}'")
        extra = set(payload) - self._allow[agent]
        if extra:
            raise PrivacyViolation(f"fields not allowed for {agent}: {sorted(extra)}")

        raw_json = json.dumps(payload, default=_json_default)
        subjects = set(MEMBER_ID_PATTERN.findall(raw_json))      # members this payload is about
        tokenised = self.pseudonymiser.tokenise(json.loads(raw_json))
        text = json.dumps(tokenised, sort_keys=True, indent=1, ensure_ascii=False)
        self.scanner.check(text, subjects)
        self._last_subjects = subjects
        self._log_egress(agent, text)
        return text

    def check_retry_text(self, text: str) -> None:
        """Re-scan a retry prompt (original payload + validation error) against the same subjects."""
        self.scanner.check(text, getattr(self, "_last_subjects", set()))

    def reidentify(self, obj: Any) -> Any:
        return self.pseudonymiser.reidentify(obj)

    def _log_egress(self, agent: str, text: str) -> None:
        if self._egress_log is None:
            return
        self._egress_log.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent": agent,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "payload": text,   # already tokenised and scanned
        }
        with open(self._egress_log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
