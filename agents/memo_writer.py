"""Memo Writer Agent: drafts the adjustment memo; code injects and verifies every number.

The LLM writes {placeholders}; code fills them from computed facts. A memo is rejected if it
uses an unknown placeholder or if any number in the filled text didn't come from a placeholder.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from decimal import Decimal

from engine.retro import CellMonthSummary
from llm.client import LLMClient
from llm.prompts import load_prompt
from schemas.ledger import MemoDraft

AGENT = "memo_writer"
TASK = "memo"
NUMBER = re.compile(r"\d")


@dataclass
class MemoResult:
    title: str
    body: str
    grounded: bool
    problems: list[str] = field(default_factory=list)
    cache_hit: bool = False
    prompt_hash: str | None = None


def _money(d: Decimal) -> str:
    return f"-${abs(d):,.2f}" if d < 0 else f"${d:,.2f}"


def build_facts(adjustment_id: str, trigger: str, trigger_ref: str, summary: list[CellMonthSummary],
                remaining_vs_state: Decimal, late_enrollment_member_months: int = 0) -> tuple[dict, dict[str, str]]:
    total = sum((s.delta for s in summary), Decimal("0.00"))
    months = sorted({s.coverage_month for s in summary})
    cells = sorted({f"{s.region} {s.category}" for s in summary})
    placeholders = {
        "adjustment_id": adjustment_id,
        "trigger_ref": trigger_ref,
        "total_delta": _money(total),
        "member_months": str(sum(s.member_months for s in summary)),
        "period_count": str(len(months)),
        "first_period": f"{months[0]:%B %Y}",
        "last_period": f"{months[-1]:%B %Y}",
        "remaining_from_state": _money(remaining_vs_state),
        "late_enrollment_member_months": str(late_enrollment_member_months),
        "rate_revision_member_months": str(sum(s.member_months for s in summary) - late_enrollment_member_months),
    }
    for s in summary:
        placeholders[f"delta_{s.coverage_month:%Y_%m}_{s.region}_{s.category}"] = _money(s.delta)
    facts = {
        "trigger": trigger,          # 'rate_version' or 'retro_enrollment'; the reference is {trigger_ref}
        "affected_cells": cells,
        "includes_late_enrollment": late_enrollment_member_months > 0,
        "affected_months": [f"{m:%B %Y}" for m in months],
        "by_cell_month": [{"month": f"{s.coverage_month:%B %Y}", "cell": f"{s.region} {s.category}",
                           "member_months": s.member_months, "delta": str(s.delta)} for s in summary],
    }
    return facts, placeholders


def _check_part(text: str, placeholders: dict[str, str], part: str, problems: list[str]) -> str:
    try:
        parsed = list(string.Formatter().parse(text))
    except ValueError as exc:
        problems.append(f"{part}: malformed placeholder ({exc})")
        return text
    unknown = {name for _, name, _, _ in parsed if name} - set(placeholders)
    if unknown:
        problems.append(f"{part}: unknown placeholders {sorted(unknown)}")
    # Before filling, the text must contain no numbers of its own (a year written with a month is fine).
    literal = "".join(lit for lit, *_ in parsed)
    raw_numbers = [m for m in re.findall(r"\$?\d[\d,.]*%?", literal) if not re.fullmatch(r"20\d\d", m)]
    if raw_numbers:
        problems.append(f"{part}: wrote numbers itself {raw_numbers[:5]}")
    return text if unknown else text.format_map(placeholders)


def fill_and_check(draft: MemoDraft, placeholders: dict[str, str]) -> MemoResult:
    problems: list[str] = []
    title = _check_part(draft.title, placeholders, "title", problems)
    body = _check_part(draft.body, placeholders, "body", problems)
    return MemoResult(title, body, not problems, problems)


def write_memo(client: LLMClient, *, adjustment_id: str, trigger: str, trigger_ref: str,
               summary: list[CellMonthSummary], remaining_vs_state: Decimal,
               late_enrollment_member_months: int = 0) -> MemoResult:
    facts, placeholders = build_facts(adjustment_id, trigger, trigger_ref, summary, remaining_vs_state,
                                      late_enrollment_member_months)
    res = client.call(agent=AGENT, task=TASK, prompt=load_prompt("memo"),
                      payload={"facts": facts, "placeholders": placeholders}, output_model=MemoDraft)
    out = fill_and_check(res.output, placeholders)
    out.cache_hit = res.cache_hit
    out.prompt_hash = res.prompt_hash
    return out
