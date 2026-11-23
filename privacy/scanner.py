"""Last line of defence: scans the final prompt text and blocks it if PHI is found.

Structural exclusion (typed evidence with no C1 fields) is the first line.
This scanner catches anything that slipped through.

Precision note: names are checked population-wide (synthetic names never collide with domain
words). Dates of birth are checked only for the members the payload is about: across thousands
of members, someone's birthday equals almost any date, so a population-wide date check would
block ordinary enrollment and payment dates.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import date

from privacy.pseudonymise import MEMBER_ID_PATTERN

FORBIDDEN_KEYS = ("first_name", "last_name", "dob", "date_of_birth", "birth_date", "ssn")


class PrivacyViolation(Exception):
    """Raised when a payload would send PHI to an external model. The call is blocked."""


def _dob_variants(iso: str) -> set[str]:
    d = date.fromisoformat(iso)
    return {iso, d.strftime("%m/%d/%Y"), d.strftime("%d/%m/%Y")}


class PromptScanner:
    def __init__(
        self,
        names: Iterable[str] = (),
        dobs_iso: Iterable[str] = (),
        dob_by_member: Mapping[str, str] | None = None,
    ) -> None:
        self._names = {n for n in names if len(n) >= 3}
        self._name_patterns = [re.compile(rf"\b{re.escape(n)}\b", re.IGNORECASE) for n in sorted(self._names)]
        self._always_dobs: set[str] = set()          # explicit list (tests, small populations)
        for d in dobs_iso:
            self._always_dobs |= _dob_variants(d)
        self._dob_by_member = {m: _dob_variants(d) for m, d in (dob_by_member or {}).items()}

    def findings(self, text: str, member_ids: Iterable[str] = ()) -> list[str]:
        hits: list[str] = []
        for key in FORBIDDEN_KEYS:
            if re.search(rf'"{key}"\s*:', text):
                hits.append(f"forbidden field '{key}'")
        if MEMBER_ID_PATTERN.search(text):
            hits.append("raw member_id")
        for pat in self._name_patterns:
            if pat.search(text):
                hits.append("member name")
                break
        dobs = set(self._always_dobs)
        for m in member_ids:
            dobs |= self._dob_by_member.get(m, set())
        if any(d in text for d in dobs):
            hits.append("date of birth")
        return hits

    def check(self, text: str, member_ids: Iterable[str] = ()) -> None:
        hits = self.findings(text, member_ids)
        if hits:
            # Don't echo the PHI itself in the error message.
            raise PrivacyViolation("blocked LLM call: " + ", ".join(sorted(set(hits))))
