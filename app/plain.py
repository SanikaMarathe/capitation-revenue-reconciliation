"""Plain-English wording shared by both tabs. Turns codes and audit events into sentences.

Nothing here is hard-coded data: every sentence is built from a real audit event or table row.
"""

from __future__ import annotations

import json
from decimal import Decimal

CAUSE = {
    "MISSING_FROM_REMIT": "State didn't pay for this member",
    "RATE_CELL_MISMATCH": "State paid under a different region or category",
    "STALE_RATE": "State paid an old rate",
    "UNEXPECTED_PAYMENT": "State paid for someone not enrolled",
    "DUPLICATE_PAYMENT": "State paid twice",
    "RECOUPMENT": "State took back an earlier payment",
    "RETRO_ENROLLMENT": "Enrollment arrived late",
    "RETRO_RATE_CHANGE": "Rate changed after the month closed",
    "BANK_TIMING": "Money reached the bank the next month",
    "BANK_SHORT": "Bank received less than the state paid",
    "BANK_OVER": "Bank received more than the state paid",
    "UNMATCHED": "No deposit matched yet",
    "unresolved": "Not explained yet",
}

ACTION = {
    "submit_enrollment_correction": "Send an enrollment correction to the state",
    "dispute_rate_with_state": "Dispute the rate with the state",
    "request_missing_payment": "Ask the state for the missing payment",
    "confirm_recoupment": "Confirm the take-back is valid",
    "refund_or_offset": "Refund or offset the extra payment",
    "no_action_timing": "No action, it's a timing difference",
    "escalate_to_human": "Needs a person to look into it",
}

STEP = {
    "retro_check": "Check closed months for late changes",
    "price": "Work out what we should have earned",
    "reconcile_members": "Compare with what the state paid, member by member",
    "reconcile_bank": "Compare the state's payments with the bank",
    "investigate": "AI explains the differences",
    "ready_for_review": "Ready for people to review",
}

WHO = {"agent": "🤖", "system": "⚙️", "human": "👤"}
WHO_LABEL = {"agent": "AI agent", "system": "System (code)", "human": "Person"}


def money(d) -> str:
    d = Decimal(str(d or 0))
    return f"-${abs(d):,.2f}" if d < 0 else f"${d:,.2f}"


def cause(code: str | None) -> str:
    if not code:
        return CAUSE["unresolved"]
    return " + ".join(CAUSE.get(c.strip(), c.strip()) for c in code.split("+"))


def action(code: str | None) -> str:
    return ACTION.get(code or "", code or "-")


def _payload(p) -> dict:
    if isinstance(p, dict):
        return p
    try:
        return json.loads(p or "{}")
    except (TypeError, ValueError):
        return {}


def event_icon(action_name: str, actor_type: str) -> str:
    return WHO["agent"] if action_name == "close.investigate" else WHO.get(actor_type, "•")


def event_sentence(action_name: str, actor: str, entity_id: str, payload) -> str:
    """One readable sentence for an audit event. Unknown actions fall back to a generic sentence."""
    p = _payload(payload)
    a = action_name
    if a == "rate_version.drafted":
        src = " (answer reused from cache)" if p.get("cache_hit") else ""
        return (f"Rate-reading AI read **{p.get('document')}** and drafted rate version **{entity_id}**: "
                f"{p.get('cells')} rates, {len(p.get('errors', []))} errors, {len(p.get('flags', []))} warnings{src}.")
    if a == "rate_version.approved":
        return f"**{actor}** approved rate version **{entity_id}**. From now on it is used for pricing."
    if a == "rate_version.rejected":
        return f"**{actor}** rejected rate version **{entity_id}**: {p.get('reason')}"
    if a == "close.retro_check":
        adj = p.get("adjustment_proposed")
        return "Checked closed months for late changes: " + ("found a correction to approve." if adj else "nothing to correct.")
    if a == "close.price":
        exc = len(p.get("exceptions", []))
        return (f"Worked out what each of **{p.get('member_months'):,}** members should earn: "
                f"**{money(p.get('expected_total'))}** in total" + (f"; {exc} could not be priced." if exc else "."))
    if a == "close.reconcile_members":
        n, unsure = p.get("variances"), p.get("to_investigator")
        rules = "rules found the reason for all of them" if not unsure else             f"rules found the reason for all but **{unsure}**"
        return f"Compared with what the state paid: **{n}** members differ; {rules}."
    if a == "bank_link.auto":
        return f"Matched bank deposit **{entity_id}** to state payment **{p.get('remit_id')}** by its reference number."
    if a == "bank_link.confirmed":
        return f"**{actor}** confirmed bank deposit **{entity_id}** belongs to state payment **{p.get('remit_id')}**."
    if a == "close.reconcile_bank":
        # Only this month's payment batch; earlier batches were reported in their own close.
        month = entity_id[-7:]
        flags = {k: v for k, v in p.get("flags", {}).items() if k.endswith(month)}
        flag_txt = "; ".join(', '.join(cause(x) for x in v) for v in flags.values()) or "no problems"
        return f"Checked the state's payment against the bank: {flag_txt}."
    if a == "close.investigate":
        if p.get("skipped"):
            return "AI investigation skipped."
        return (f"AI explained the differences in **{p.get('groups')}** groups; code checked each explanation "
                f"against the data and **{p.get('grounded')}** passed.")
    if a == "close.ready_for_review":
        items = p.get("open_items") or []
        return "Ready for review." + (" A correction had to be approved first." if items else "")
    if a == "close.invalidated":
        return f"Close review cancelled: {p.get('reason')}."
    if a == "period.closed":
        return (f"**{actor}** signed off the month and booked **{money(p.get('booked_total'))}**. "
                "It can now only change through an approved correction.")
    if a == "adjustment.proposed":
        return (f"Retro engine proposed correction **{entity_id}** of **{money(p.get('total'))}** over "
                f"{p.get('lines')} member-months (trigger: {p.get('trigger_ref')}).")
    if a == "adjustment.memo_drafted":
        ok = "every number checked ✅" if p.get("grounded") else "number check failed ❌"
        return f"Memo AI drafted the explanation for **{entity_id}** ({ok})."
    if a == "adjustment.approved_and_posted":
        return (f"**{actor}** approved and posted **{entity_id}**: {money(p.get('total'))} "
                f"({p.get('ledger_lines')} ledger lines).")
    if a == "adjustment.rejected":
        return f"**{actor}** rejected correction **{entity_id}**: {p.get('reason')}"
    if a == "adjustment.stale":
        return f"Correction **{entity_id}** was withdrawn: {p.get('reason')}"
    if a in ("variance.confirmed", "variance.overridden"):
        verb = "agreed with" if a == "variance.confirmed" else "changed"
        return f"**{actor}** {verb} the cause: {cause(p.get('final_category'))}."
    if a == "phi.identity_revealed":
        return f"**{actor}** revealed a member's real name (reason: {p.get('reason')}). The name itself is not logged."
    return f"{a} on {entity_id}"
