"""Privacy eval: scan everything that left (or could leave) the system for PHI.

Scanned: every cached LLM request/response, the egress log, and every audit payload.
Looked for: raw member IDs, any member name, and the date of birth of any member a record refers to
(members are found through their pseudonymous tokens). Plus an injection test: payloads seeded with
PHI must be blocked by the gateway 100% of the time.
"""

from __future__ import annotations

import json
import random
import re

from common.config import Settings
from llm.cache import ResponseCache
from privacy.pseudonymise import MEMBER_ID_PATTERN, Pseudonymiser
from privacy.scanner import PrivacyViolation
from storage.phi import load_identifier_values


def _scan_text(text: str, name_pat, token_to_member, dob_by_member) -> list[str]:
    hits = []
    if MEMBER_ID_PATTERN.search(text):
        hits.append("raw_member_id")
    if name_pat and name_pat.search(text):
        hits.append("name")
    for tok in set(re.findall(r"MBR-[0-9a-f]{10}", text)):
        m = token_to_member.get(tok)
        if m and dob_by_member[m] in text:
            hits.append("dob")
            break
    return hits


def run(conn, settings: Settings, gateway) -> dict:
    names, dob_by_member, ids = load_identifier_values(conn)
    name_pat = re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(names)) + r")\b", re.IGNORECASE)
    p = Pseudonymiser(settings.pseudonym_secret, settings.raw["privacy"]["member_token_prefix"])
    token_to_member = {p.token(m): m for m in ids}

    sources = {"llm_cache": [], "egress_log": [], "audit_payloads": []}
    for rec in ResponseCache(settings.path("llm_cache_dir")).iter_records():
        sources["llm_cache"].append(json.dumps(rec))
    egress = settings.path("egress_log")
    if egress.exists():
        sources["egress_log"] = egress.read_text(encoding="utf-8").splitlines()
    sources["audit_payloads"] = [r[0] or "" for r in conn.execute("SELECT payload FROM audit_log").fetchall()]

    scanned = {}
    for name, texts in sources.items():
        leaks = [h for t in texts for h in _scan_text(t, name_pat, token_to_member, dob_by_member)]
        scanned[name] = {"records": len(texts), "leaks": len(leaks), "leak_types": sorted(set(leaks))}

    # Injection test against the live gateway.
    rng = random.Random(11)
    members = sorted(ids)
    blocked = total = 0
    for _ in range(200):
        m = rng.choice(members)
        first, last, dob = conn.execute("SELECT first_name, last_name, dob FROM members WHERE member_id=?", [m]).fetchone()
        injected = rng.choice([
            {"note": f"spoke with {first}"},
            {"note": f"surname {last.upper()}"},
            {"note": f"born {dob.isoformat()}"},
            {"note": f"born {dob:%m/%d/%Y}"},
            {"first_name": first},
        ])
        payload = {"variance": {"member_id": m, "delta": "-12.50", **injected}}
        total += 1
        try:
            gateway.prepare("investigator", payload)
        except PrivacyViolation:
            blocked += 1
    return {
        "scanned": scanned,
        "total_records": sum(s["records"] for s in scanned.values()),
        "total_leaks": sum(s["leaks"] for s in scanned.values()),
        "injection_test": {"payloads": total, "blocked": blocked, "blocked_pct": round(100 * blocked / total, 1)},
    }
