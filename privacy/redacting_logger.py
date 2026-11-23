"""Logging filter that strips names, DOBs and raw member IDs from log lines."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from privacy.pseudonymise import MEMBER_ID_PATTERN


class RedactingFilter(logging.Filter):
    def __init__(self, names: Iterable[str] = (), dobs_iso: Iterable[str] = ()) -> None:
        super().__init__()
        values = sorted({n for n in names if len(n) >= 3} | set(dobs_iso), key=len, reverse=True)
        self._pattern = re.compile("|".join(rf"\b{re.escape(v)}\b" for v in values)) if values else None

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        msg = MEMBER_ID_PATTERN.sub("[MEMBER]", msg)
        if self._pattern is not None:
            msg = self._pattern.sub("[REDACTED]", msg)
        record.msg, record.args = msg, ()
        return True
