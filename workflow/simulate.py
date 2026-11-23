"""Replays the half-year in chronological order.

Timeline: v1 received Dec 1, approved Dec 15 -> Jan close (Feb 10) -> ... -> v2 received Mar 15,
approved Mar 20 -> ... -> v3 received May 20, approved May 22 -> ... -> Jun close (Jul 10).
Before each close and after each approval, the retro engine runs over closed months (that is
how late enrollment and retro rates are caught).

`receive` decides how a rate document becomes a draft version: by default the ground-truth
table is inserted (to verify the engine alone); the pipeline passes the AI extractor instead.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

from data_gen import spec
from governance import approvals
from schemas.enums import RateVersionStatus, VersionKind
from schemas.rates import RateRow, RateVersion
from storage import repo
from workflow.deterministic_close import DeterministicClose

CONTROLLER = "controller"
Receiver = Callable[[str, datetime], None]


def insert_ground_truth_version(conn, gt_dir: Path, v: str) -> None:
    meta = json.loads((gt_dir / f"rates_{v}.json").read_text(encoding="utf-8"))
    version = RateVersion(version_id=v, kind=VersionKind(meta["kind"]),
                          published_date=date.fromisoformat(meta["published"]),
                          source_document_id=meta["source_file"], status=RateVersionStatus.DRAFT)
    rows = [RateRow(version_id=v, region=r["region"], category=r["category"],
                    effective_from=date.fromisoformat(r["effective_from"]),
                    effective_to=date.fromisoformat(r["effective_to"]) if r["effective_to"] else None,
                    pmpm=Decimal(r["pmpm"]), source_ref=f"ground_truth:{v}") for r in meta["rates"]]
    repo.insert_rate_version(conn, version, rows)


def insert_ground_truth_rates(conn, gt_dir: Path) -> None:
    for v in ("v1", "v2", "v3"):
        insert_ground_truth_version(conn, gt_dir, v)


def timeline() -> list[tuple[datetime, str, str]]:
    events = [(datetime.combine(spec.PUBLISHED[v], time(10)), "receive", v) for v in ("v1", "v2", "v3")]
    events += [(datetime.combine(spec.APPROVED[v], time(9)), "approve", v) for v in ("v1", "v2", "v3")]
    events += [(datetime.combine(spec.close_date(m), time(17)), "close", f"{m:%Y-%m}") for m in spec.MONTHS]
    return sorted(events)


def run(conn, approve_adjustments: bool = True, receive: Receiver | None = None) -> list[str]:
    dc = DeterministicClose(conn)
    adjustments: list[str] = []
    for at, kind, ref in timeline():
        if kind == "receive":
            if receive is not None:
                receive(ref, at)
            elif not conn.execute("SELECT 1 FROM rate_versions WHERE version_id=?", [ref]).fetchone():
                raise RuntimeError(f"no draft for {ref}: insert ground-truth rates or pass a receiver")
            continue
        if kind == "approve":
            approvals.approve_rate_version(conn, ref, CONTROLLER, at)
            adj = dc.propose_retro(at, "rate_version", ref)
        else:
            adj = dc.propose_retro(at, "retro_enrollment", f"before close {ref}")
        if adj:
            adjustments.append(adj)
            if approve_adjustments:
                approvals.approve_adjustment(conn, adj, CONTROLLER, at)
        if kind == "close":
            dc.close_month(date.fromisoformat(ref + "-01"), at, CONTROLLER)
    return adjustments
