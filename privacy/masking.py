"""Role-based display masking for the UI. Identity is masked by default."""

from __future__ import annotations

from datetime import date


def mask_name(first: str, last: str) -> str:
    def _m(s: str) -> str:
        return (s[0] + "*" * (len(s) - 1)) if s else ""

    return f"{_m(first)} {_m(last)}".strip()


def mask_dob(_dob: date | None) -> str:
    return "****-**-**"
