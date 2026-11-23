"""Adversarial evals: inject known-bad AI output and measure what the deterministic gates catch.

Reported honestly, including the case the gates are NOT designed to catch (two cells swapped
together with their references), which is why a human reviews the diff before approval.
"""

from __future__ import annotations

import random
from datetime import date, datetime
from decimal import Decimal

from agents import groundedness
from agents.doc_parse import parse
from agents.evidence import build_pack
from engine.pricing import RateBook
from engine.rate_validation import validate
from schemas.enums import VersionKind
from schemas.variance import Finding
from storage import repo


def _corrupt_extractions(conn, docs: dict[str, str], doc_dir) -> list[dict]:
    rng = random.Random(7)
    results = []
    for v, filename in docs.items():
        doc = parse(doc_dir / filename)
        rows = [r for r in repo.rate_rows(conn) if r.version_id == v]
        version = next(x for x in repo.rate_versions(conn) if x.version_id == v)
        book = RateBook([], [])                    # judge each corruption on its own
        retro = v == "v3"

        def check(bad_rows, name, expect_caught=True):
            rep = validate(bad_rows, kind=version.kind, is_retroactive=retro, published=version.published_date,
                           confidence=0.95, refs=doc.refs, book=book, as_of=datetime(2026, 12, 31))
            caught = not rep.approvable
            results.append({"doc": v, "injection": name, "caught": caught, "expected_to_catch": expect_caught})

        i = rng.randrange(len(rows))
        check([r if j != i else r.model_copy(update={"pmpm": r.pmpm * 10}) for j, r in enumerate(rows)],
              "rate misread x10 (e.g. $400 -> $4,000)")
        check([r if j != i else r.model_copy(update={"pmpm": r.pmpm + Decimal("1.00")}) for j, r in enumerate(rows)],
              "rate off by $1")
        check([r if j != i else r.model_copy(update={"effective_from": r.effective_from.replace(day=15)})
               for j, r in enumerate(rows)], "mid-month effective date")
        check([r if j != i else r.model_copy(update={"source_ref": "p9:t9:r9:c9"}) for j, r in enumerate(rows)],
              "invented source reference")
        if version.kind == VersionKind.FULL:
            check(rows[:-1], "dropped a rate cell")
            a, b = rows[0], rows[1]
            swapped = [a.model_copy(update={"pmpm": b.pmpm, "source_ref": b.source_ref}),
                       b.model_copy(update={"pmpm": a.pmpm, "source_ref": a.source_ref})] + rows[2:]
            check(swapped, "two cells swapped WITH their references", expect_caught=False)
    return results


def _corrupt_findings(conn, outcomes, limit: int = 40) -> list[dict]:
    results = []
    for o in [o for o in outcomes if o.finding is not None and o.grounded][:limit]:
        pack = build_pack(conn, o.group)
        base = o.finding.model_dump(mode="json")
        injections = {
            "invented evidence id": {**base, "evidence_refs": ["RL9999999"]},
            "invented dollar amount in prose": {**base, "explanation": base["explanation"] + " Net effect $9,876.54."},
            "invented cited amount": {**base, "amounts_cited": [{"label": "x", "amount": "9876.54"}]},
            "cause contradicting facts": {**base, "possible_causes":
                ["MISSING_FROM_REMIT"] if pack["features"]["has_remit"] else ["DUPLICATE_PAYMENT"]},
        }
        for name, bad_dict in injections.items():
            bad = Finding.model_validate(bad_dict)
            results.append({"injection": name, "caught": not groundedness.check(bad, pack).grounded,
                            "expected_to_catch": True})
    return results


def run(conn, outcomes, docs, doc_dir) -> dict:
    ex = _corrupt_extractions(conn, docs, doc_dir)
    fi = _corrupt_findings(conn, outcomes)

    def summarise(rows):
        by = {}
        for r in rows:
            d = by.setdefault(r["injection"], {"n": 0, "caught": 0, "expected_to_catch": r["expected_to_catch"]})
            d["n"] += 1
            d["caught"] += r["caught"]
        return by

    designed = [r for r in ex + fi if r["expected_to_catch"]]
    return {
        "extraction": summarise(ex),
        "findings": summarise(fi),
        "designed_catch_rate_pct": round(100 * sum(r["caught"] for r in designed) / len(designed), 1),
    }
