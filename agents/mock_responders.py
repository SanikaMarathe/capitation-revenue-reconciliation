"""Deterministic stand-ins for the LLM, used by tests and the free offline demo.

IMPORTANT: these prove the plumbing (privacy gateway, schemas, validation, groundedness, cache),
not AI quality. The extraction mock is a heuristic parser for these three documents; the
investigator mock echoes the rule hits. Real accuracy is measured with the live model.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from llm.mock_provider import MockProvider
from llm.provider import ProviderRequest

REGIONS = ["North", "South", "East", "West"]
CATEGORY_WORDS = [("expansion", "Expansion"), ("disabled", "Disabled"), ("abd", "Disabled"),
                  ("child", "Child"), ("adult", "Adult")]
REF_PREFIX = re.compile(r"^\[[^\]]+\]\s?")
CELL = re.compile(r"\[([^\]]+)\]\s?([^|]*)")


def _payload(request: ProviderRequest) -> dict:
    return json.loads(request.user.split("\n\nYour previous output failed validation")[0])


def _category(label: str) -> str | None:
    low = label.lower()
    return next((c for word, c in CATEGORY_WORDS if word in low), None)


def _plain(text: str) -> str:
    return " ".join(REF_PREFIX.sub("", line) for line in text.splitlines())


def _date_words(s: str) -> str:
    return datetime.strptime(s.replace(",", ""), "%B %d %Y").date().isoformat()


def _money2(s: str) -> str:
    return f"{float(s.replace(',', '').replace('$', '')):.2f}"


# ---------------------------------------------------------------- extraction
def extract(request: ProviderRequest) -> dict:
    p = _payload(request)
    text, fmt = p["document_text"], p["document_format"]
    plain = _plain(text)
    return {"pdf": _pdf, "xlsx": _xlsx, "email": _email}[fmt](text, plain)


def _pdf(text: str, plain: str) -> dict:
    rows = [CELL.findall(line) for line in text.splitlines() if line.startswith("[p1:t1:r")]
    header = [_category(v.strip()) for _, v in rows[0]]
    eff = _date_words(re.search(r"effective (\w+ \d{1,2}, \d{4})", plain).group(1))
    published = _date_words(re.search(r"Notice date: (\w+ \d{2}, \d{4})", plain).group(1))
    rates = []
    for row in rows[1:]:
        region = row[0][1].strip()
        for (ref, value), cat in zip(row[1:], header[1:]):
            rates.append({"region": region, "category": cat, "effective_from": eff[:8] + "01", "effective_to": None,
                          "pmpm": _money2(value), "source_ref": ref})
    return {"kind": "full", "is_retroactive": False, "published_date": published, "rates": rates,
            "notes": "Expansion rates subject to federal approval (footnote).", "confidence": 0.95}


def _xlsx(text: str, plain: str) -> dict:
    published = datetime.strptime(re.search(r"Published (\d{2}/\d{2}/\d{4})", plain).group(1), "%m/%d/%Y").date()
    rates = []
    for line in text.splitlines():
        cells = {ref.split("!")[1].rstrip("0123456789"): (ref, v.strip()) for ref, v in CELL.findall(line)}
        if "A" not in cells or cells["A"][1].title() not in REGIONS or "D" not in cells:
            continue
        eff = datetime.strptime(cells["C"][1], "%m/%d/%Y").date()
        rates.append({"region": cells["A"][1].title(), "category": _category(cells["B"][1]),
                      "effective_from": eff.replace(day=1).isoformat(), "effective_to": None,
                      "pmpm": _money2(cells["D"][1]), "source_ref": cells["D"][0]})
    return {"kind": "full", "is_retroactive": False, "published_date": published.isoformat(), "rates": rates,
            "notes": "Rows flagged R changed from the prior period.", "confidence": 0.93}


def _email(text: str, plain: str) -> dict:
    published = datetime.strptime(re.search(r"Date: \w+, (\d{1,2} \w+ \d{4})", plain).group(1), "%d %b %Y").date()
    lines = [(REF_PREFIX.match(l).group(0)[1:-2] if REF_PREFIX.match(l) else "", REF_PREFIX.sub("", l))
             for l in text.splitlines()]
    rates = []
    for sentence in re.split(r"(?<=PMPM)\.", plain):
        m = re.search(r"the (North|South|East|West) (Child|Adult|Disabled|Expansion)\b.*?rate is revised from "
                      r"\$([\d,]+\.\d{2}) to \$([\d,]+\.\d{2})", sentence)
        if not m:
            continue
        period = re.search(r"for the period (\w+ \d{1,2}) through (\w+ \d{1,2}, (\d{4}))", sentence)
        start = re.search(r"retroactively to (\w+ \d{1,2}, \d{4})", sentence)
        if period:
            eff_from = _date_words(f"{period.group(1)}, {period.group(3)}")
            eff_to = _date_words(period.group(2))[:8] + "01"
        else:
            eff_from, eff_to = _date_words(start.group(1)), None
        new = m.group(4)
        ref = next(r for r, body in lines if f"${new}" in body)
        rates.append({"region": m.group(1), "category": m.group(2), "effective_from": eff_from[:8] + "01",
                      "effective_to": eff_to, "pmpm": _money2(new), "source_ref": ref})
    return {"kind": "amendment", "is_retroactive": True, "published_date": published.isoformat(), "rates": rates,
            "notes": "Only changed cells listed; other rates unchanged.", "confidence": 0.9}


# ---------------------------------------------------------------- investigation
ACTIONS = {
    "MISSING_FROM_REMIT": "request_missing_payment",
    "RATE_CELL_MISMATCH": "submit_enrollment_correction",
    "STALE_RATE": "dispute_rate_with_state",
    "UNEXPECTED_PAYMENT": "refund_or_offset",
    "DUPLICATE_PAYMENT": "refund_or_offset",
    "RECOUPMENT": "confirm_recoupment",
    "RETRO_RATE_CHANGE": "request_missing_payment",
    "RETRO_ENROLLMENT": "no_action_timing",
}


def investigate(request: ProviderRequest) -> dict:
    p = _payload(request)
    v, f = p["variance"], p["features"]
    hits = sorted(f["rule_hits"], key=lambda h: h["strength"] != "strong")
    causes = list(dict.fromkeys(h["code"] for h in hits))[:2]
    if not causes:
        return {"possible_causes": ["STALE_RATE"], "explanation": "The evidence does not settle the cause.",
                "evidence_refs": [v["group_id"]], "amounts_cited": [], "confidence": 0.3,
                "recommended_action": "escalate_to_human"}
    reasons = "; ".join(h["reason"] for h in hits)
    who = v["member_id"] + (f" and {v['members_in_group'] - 1} similar members" if v["members_in_group"] > 1 else "")
    explanation = (f"For {who} in {v['coverage_month'][:7]}, the plan expected ${v['expected']} and the state "
                   f"remitted ${v['remitted']} (difference ${v['delta']}). Evidence indicates: {reasons}.")
    refs = [l["id"] for l in p["remittance_lines"]] + [s["id"] for s in p["enrollment_history"]]
    action = "confirm_recoupment" if set(causes) == {"UNEXPECTED_PAYMENT", "RECOUPMENT"} else ACTIONS[causes[0]]
    return {"possible_causes": causes, "explanation": explanation, "evidence_refs": refs or [v["group_id"]],
            "amounts_cited": [{"label": "expected", "amount": v["expected"]},
                              {"label": "remitted", "amount": v["remitted"]},
                              {"label": "delta", "amount": v["delta"]}],
            "confidence": 0.9 if len(causes) == 1 else 0.75, "recommended_action": action}


# ---------------------------------------------------------------- memo
def memo(request: ProviderRequest) -> dict:
    p = _payload(request)
    f = p["facts"]
    cells = ", ".join(f["affected_cells"])
    return {
        "title": "Prior-period capitation adjustment {trigger_ref}",
        "body": (f"This memo supports adjustment {{adjustment_id}}, proposed after a "
                 f"{f['trigger'].replace('_', ' ')} event ({{trigger_ref}}). Repricing the closed periods {{first_period}} to {{last_period}} with the "
                 f"latest approved information changes expected capitation by {{total_delta}} across "
                 f"{{member_months}} member-months in {cells}"
                 + (" ({rate_revision_member_months} from the rate revision and {late_enrollment_member_months} from "
                    "enrollment received after those months closed)" if f.get("includes_late_enrollment") else "")
                 + f". The proposed entry debits Capitation Receivable and "
                 f"credits Capitation Revenue - Prior Period Adjustment, tagged to each original period. "
                 f"After state payments to date, {{remaining_from_state}} remains to be collected from the state."),
    }


def mock_provider() -> MockProvider:
    return MockProvider({
        "rate_extraction": extract,
        "investigate_simple": investigate,
        "investigate_complex": investigate,
        "memo": memo,
    })
