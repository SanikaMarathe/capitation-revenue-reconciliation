"""Ground-truth rate tables as cell-level rows with effective intervals, and a plain lookup.

This lookup is the generator's own (simple loop over versions in approval order).
The engine implements its own resolution; evals compare the two.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from data_gen import spec

VERSION_ORDER = ["v1", "v2", "v3"]     # also approval order


def version_rows() -> dict[str, list[dict]]:
    v1 = [{"region": r, "category": c, "effective_from": spec.V1_EFFECTIVE, "effective_to": None, "pmpm": p}
          for (r, c), p in spec.RATES_V1.items()]
    v2 = [{"region": r, "category": c, "effective_from": spec.V2_EFFECTIVE, "effective_to": None, "pmpm": p}
          for (r, c), p in spec.RATES_V2.items()]
    return {"v1": v1, "v2": v2, "v3": [dict(r) for r in spec.V3_ROWS]}


def rate(region: str, category: str, month: date, known_versions: list[str]) -> Decimal:
    value: Decimal | None = None
    rows = version_rows()
    for v in VERSION_ORDER:
        if v not in known_versions:
            continue
        for row in rows[v]:
            if (row["region"], row["category"]) != (region, category):
                continue
            if row["effective_from"] <= month and (row["effective_to"] is None or month <= row["effective_to"]):
                value = row["pmpm"]
    if value is None:
        raise ValueError(f"no rate for {region}/{category} {month}")
    return value


def known_at(d: date) -> list[str]:
    return [v for v in VERSION_ORDER if spec.APPROVED[v] <= d]


def state_known_at(d: date) -> list[str]:
    """The state applies a version from its publication date."""
    return [v for v in VERSION_ORDER if spec.PUBLISHED[v] <= d]
