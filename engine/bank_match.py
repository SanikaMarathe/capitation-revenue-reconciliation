"""Level 2 reconciliation: state remittances vs. bank deposits.

Conservative by design:
- auto-link ONLY on a unique explicit reference (deposit.reference == remit_id)
- a deposit is used once
- amount/date-window matches are CANDIDATES for a human, never auto-cleared
- a shortfall is reported as observed ("short by $X"), never assumed to be a fee
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from schemas.remittance import BankDeposit, Remit


@dataclass
class RemitBankOutcome:
    remit_id: str
    remit_total: Decimal
    deposit_id: str | None
    deposit_amount: Decimal | None
    linked_by: str | None                  # system | human | None
    flags: list[str] = field(default_factory=list)   # BANK_TIMING, BANK_SHORT, BANK_OVER, UNMATCHED
    candidates: list[str] = field(default_factory=list)

    @property
    def delta(self) -> Decimal | None:
        return None if self.deposit_amount is None else self.deposit_amount - self.remit_total


@dataclass
class BankResult:
    outcomes: list[RemitBankOutcome]
    new_links: list[tuple[str, str]]       # (deposit_id, remit_id) auto-linked this run
    unmatched_deposits: list[str]


def match_bank(remits: list[Remit], deposits: list[BankDeposit], existing_links: dict[str, str],
               window_days: int = 5) -> BankResult:
    dep = {d.deposit_id: d for d in deposits}
    remit_ids = {r.remit_id for r in remits}
    # Ignore saved links that point outside this run's scope (e.g. a later deposit in a historical view).
    existing_links = {d: r for d, r in existing_links.items() if d in dep and r in remit_ids}
    used = set(existing_links)                         # deposit_id -> remit_id already linked
    linked_remit = {r: d for d, r in existing_links.items()}
    new_links: list[tuple[str, str]] = []
    outcomes: list[RemitBankOutcome] = []

    for remit in sorted(remits, key=lambda r: r.remit_id):
        linked_by = "human" if remit.remit_id in linked_remit else None
        deposit_id = linked_remit.get(remit.remit_id)
        candidates: list[str] = []
        if deposit_id is None:
            by_ref = [d for d in deposits if d.reference == remit.remit_id and d.deposit_id not in used]
            if len(by_ref) == 1:
                deposit_id, linked_by = by_ref[0].deposit_id, "system"
                used.add(deposit_id)
                new_links.append((deposit_id, remit.remit_id))
            elif len(by_ref) > 1:
                candidates = [d.deposit_id for d in by_ref]
            else:
                candidates = [d.deposit_id for d in deposits
                              if d.deposit_id not in used and not d.reference and d.amount == remit.total_amount
                              and abs((d.deposit_date - remit.payment_date).days) <= window_days]
        outcome = RemitBankOutcome(remit.remit_id, remit.total_amount, deposit_id,
                                   dep[deposit_id].amount if deposit_id else None, linked_by, candidates=candidates)
        if deposit_id is None:
            outcome.flags.append("UNMATCHED")
        else:
            d = dep[deposit_id]
            if (d.deposit_date.year, d.deposit_date.month) != (remit.payment_date.year, remit.payment_date.month):
                outcome.flags.append("BANK_TIMING")
            if d.amount < remit.total_amount:
                outcome.flags.append("BANK_SHORT")
            elif d.amount > remit.total_amount:
                outcome.flags.append("BANK_OVER")
        outcomes.append(outcome)

    candidate_ids = {c for o in outcomes for c in o.candidates}
    unmatched = sorted(d.deposit_id for d in deposits if d.deposit_id not in used and d.deposit_id not in candidate_ids)
    return BankResult(outcomes, new_links, unmatched)
