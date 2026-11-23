"""End-to-end run: raw files + rate documents -> extracted, approved rates -> half-year of closes
-> AI investigation of the variances -> AI memos for each adjustment.

    python -m workflow.pipeline                      # mock LLM, free, offline
    python -m workflow.pipeline --provider anthropic # live model; responses cached for replay
    python -m workflow.pipeline --cache-mode replay  # offline replay of a previous live run

Writes the database to data/recon.duckdb for the UI and evals.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from agents import investigator, memo_writer, rate_extractor
from agents.mock_responders import mock_provider
from common.config import get_settings
from data_gen import spec
from engine.retro import settlement, summarise
from llm.factory import build_client, build_provider
from llm.provider import LLMError
from storage import repo
from storage.db import connect
from storage.load_raw import load_raw
from workflow import simulate
from workflow.deterministic_close import DeterministicClose

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
DOCS = {"v1": "v1_rate_notice_CY2026.pdf", "v2": "v2_rate_sheet_eff_2026-04-01.xlsx",
        "v3": "v3_rate_amendment_email.txt"}
END = datetime(2026, 7, 31, 23, 0)


@dataclass
class PipelineReport:
    extractions: dict = field(default_factory=dict)
    investigation: dict = field(default_factory=dict)
    memos: list = field(default_factory=list)
    llm: dict = field(default_factory=dict)
    outcomes: list = field(default_factory=list, repr=False)   # investigator GroupOutcome objects (for evals)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "outcomes"}


def run(db_path: str | Path = ":memory:", provider: str | None = None, cache_mode: str | None = None,
        cost_mode: bool = False, max_spend_usd: Decimal | None = None):
    settings = get_settings()
    if db_path != ":memory:" and Path(db_path).exists():
        Path(db_path).unlink()
    conn = connect(db_path)
    load_raw(conn, RAW)
    name = provider or settings.llm["provider"]
    # Mock gets its deterministic responders; anything else is built from the explicit name, so a
    # --provider flag can never silently fall back to the provider in config.yaml.
    prov = mock_provider() if name == "mock" else build_provider(settings, name)
    client = build_client(settings, conn=conn, provider=prov, cache_mode=cache_mode,
                          cost_mode=cost_mode, max_spend_usd=max_spend_usd)
    report = PipelineReport()

    def receive(version_id: str, at: datetime) -> None:
        res = rate_extractor.extract(conn, client, RAW / "rate_docs" / DOCS[version_id],
                                     received=at.date(), version_id=version_id, as_of=at)
        report.extractions[version_id] = {
            "cells": len(res.rows), "errors": res.report.errors, "flags": res.report.flags,
            "changed_cells": sum(d.changed for d in res.diff), "cache_hit": res.cache_hit,
            "kind": res.notice.kind.value, "is_retroactive": res.notice.is_retroactive,
            "published_date": res.notice.published_date.isoformat(), "confidence": res.notice.confidence,
            "file": DOCS[version_id],
        }
        if not res.report.approvable:
            raise RuntimeError(f"{version_id} extraction has errors: {res.report.errors}")

    adjustments = simulate.run(conn, receive=receive)

    # Investigate the half-year's final variances (as known at END).
    dc = DeterministicClose(conn)
    variances = [v for m in spec.MONTHS for v in dc.reconcile(m, END, run_id="final")]
    updated, outcomes = investigator.investigate(conn, client, variances, only_complex=False)
    repo.insert_variances(conn, updated)
    report.outcomes = outcomes
    report.investigation = {
        "variances": len(variances),
        "groups": len(outcomes),
        "complex_groups": sum(o.task == "investigate_complex" for o in outcomes),
        "grounded_groups": sum(o.grounded for o in outcomes),
        "ungrounded": [{"group": o.group.group_id, "problems": o.problems or [o.error]} for o in outcomes if not o.grounded],
    }

    # Memos for each adjustment (numbers injected by code).
    current = {(e.member_id, e.coverage_month): e.pmpm for m in spec.MONTHS for e in dc.price(m, END).expected}
    lines = repo.remittance_lines(conn)
    for adj_id in adjustments:
        adj = repo.adjustment(conn, adj_id)
        adj_lines = repo.adjustment_lines(conn, adj_id)
        remaining = sum((s.remaining_vs_state for s in settlement(adj_lines, current, lines)), Decimal("0.00"))
        try:
            memo = memo_writer.write_memo(client, adjustment_id=adj_id, trigger=adj["trigger"],
                                          trigger_ref=adj["trigger_ref"], summary=summarise(adj_lines),
                                          remaining_vs_state=remaining,
                                          late_enrollment_member_months=sum(l.old_version_id == "none" for l in adj_lines))
        except LLMError as exc:          # e.g. spend cap reached: a human writes the memo
            report.memos.append({"adjustment": adj_id, "grounded": False, "problems": [f"{type(exc).__name__}: {exc}"]})
            continue
        conn.execute("UPDATE adjustments SET memo=?, memo_grounded=? WHERE adjustment_id=?",
                     [memo.title + "\n\n" + memo.body, memo.grounded, adj_id])
        report.memos.append({"adjustment": adj_id, "grounded": memo.grounded, "problems": memo.problems})

    calls = client._meter.calls
    report.llm = {
        "calls": len(calls),
        "cache_hits": sum(c.cache_hit for c in calls),
        "input_tokens": sum(c.input_tokens for c in calls),
        "output_tokens": sum(c.output_tokens for c in calls),
        "cost_usd": str(sum((c.cost_usd for c in calls), Decimal("0"))),
        "by_task": {t: sum(1 for c in calls if c.task == t) for t in sorted({c.task for c in calls})},
        "models_by_task": {t: dict(sorted({m: sum(1 for c in calls if c.task == t and c.model == m)
                                           for m in {c.model for c in calls if c.task == t}}.items()))
                           for t in sorted({c.task for c in calls})},
        "new_spend_usd": str(client.spent_usd),
        "uncached_equivalent_usd": str(client.uncached_equivalent_usd.quantize(Decimal("0.0001"))),
        "cost_mode": cost_mode,
        "provider": provider or settings.llm["provider"],
    }
    return conn, report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["mock", "anthropic"], default=None)
    ap.add_argument("--cache-mode", choices=["read_write", "replay", "off"], default=None)
    ap.add_argument("--db", default=str(ROOT / "data" / "recon.duckdb"))
    args = ap.parse_args()
    _, report = run(args.db, args.provider, args.cache_mode)
    print(json.dumps(report.as_dict(), indent=1, default=str))


if __name__ == "__main__":
    main()
