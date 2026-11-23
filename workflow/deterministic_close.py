"""The deterministic half of the monthly close, wired to the database.

Phase 5's orchestrator wraps this with the AI steps (extraction, investigation, memos)
and the review UI. Nothing here calls an LLM.
"""

from __future__ import annotations

from datetime import date, datetime

from engine import rules
from engine.bank_match import BankResult, match_bank
from engine.pricing import PricingResult, RateBook, price_month
from engine.proration import RULES
from engine.reconcile import MemberMonthRecon, reconcile_members
from engine.retro import RetroLine, compute_retro
from governance import approvals
from schemas.enums import VarianceLevel, VarianceStatus
from schemas.variance import Variance
from storage import repo


class PricingIncomplete(Exception):
    """A month can't be booked while some member-months have no price (UNPRICED / ENROLLMENT_CONFLICT)."""


class DeterministicClose:
    def __init__(self, conn, proration_rule: str = "first_of_month", bank_window_days: int = 5) -> None:
        self.conn = conn
        self.rule = RULES[proration_rule]
        self.window = bank_window_days

    # ---------------- pricing
    def ratebook(self) -> RateBook:
        return RateBook(repo.rate_rows(self.conn), repo.rate_versions(self.conn))

    def price(self, month: date, as_of: datetime, book: RateBook | None = None,
              spans=None) -> PricingResult:
        return price_month(spans or repo.spans_by_member(self.conn), book or self.ratebook(), month, as_of, self.rule)

    # ---------------- level 1
    def reconcile(self, month: date, as_of: datetime, run_id: str, save: bool = False) -> list[Variance]:
        book = self.ratebook()
        expected = self.price(month, as_of, book).expected
        row = self.conn.execute("SELECT closed_at FROM periods WHERE coverage_month=? AND status='closed'", [month]).fetchone()
        closed_at = row[0] if row and row[0] <= as_of else None
        recs = reconcile_members(expected, repo.remittance_lines(self.conn, month), book, month, as_of, closed_at)
        variances = [self._to_variance(r, run_id) for r in recs if r.is_variance]
        if save:
            repo.insert_variances(self.conn, variances)
        return variances

    @staticmethod
    def _to_variance(r: MemberMonthRecon, run_id: str) -> Variance:
        hits = rules.classify(r.features)
        answer, needs_llm = rules.route(hits)
        features = dict(r.features)
        features["rule_hits"] = [{"code": h.code.value, "strength": h.strength.value, "reason": h.reason} for h in hits]
        return Variance(
            variance_id=f"V-{run_id}-{r.member_id}-{r.coverage_month:%Y%m}", run_id=run_id,
            coverage_month=r.coverage_month, member_id=r.member_id, level=VarianceLevel.MEMBER,
            expected=r.expected, remitted=r.remitted, delta=r.delta, features=features,
            observed_type=r.observed_type,
            possible_cause_rule=answer.code if answer else None,
            rule_strength=answer.strength if answer else None,
            evidence_refs=[l.line_id for l in r.lines] + ([r.expected_row.span_id] if r.expected_row else []),
            status=VarianceStatus.NEEDS_HUMAN if needs_llm else VarianceStatus.POSSIBLE_CAUSE,
        )

    # ---------------- level 2
    def reconcile_bank(self, as_of: datetime, record_links: bool = True) -> BankResult:
        remits = [r for r in repo.remits(self.conn) if r.payment_date <= as_of.date()]
        deposits = [d for d in repo.deposits(self.conn) if d.deposit_date <= as_of.date()]
        # Only saved links whose deposit AND remit existed at the cutoff (a historical view must not see later links).
        dep_ids, remit_ids = {d.deposit_id for d in deposits}, {r.remit_id for r in remits}
        links = {d: r for d, r in repo.bank_links(self.conn).items() if d in dep_ids and r in remit_ids}
        result = match_bank(remits, deposits, links, self.window)
        if record_links and result.new_links:
            approvals.record_system_links(self.conn, result.new_links, as_of)
        return result

    # ---------------- close + retro
    def close_month(self, month: date, as_of: datetime, actor: str):
        res = self.price(month, as_of)
        if res.issues:
            # Never book a subtotal that silently drops member-months we couldn't price.
            raise PricingIncomplete(f"{len(res.issues)} member-month(s) can't be priced: "
                                    + ", ".join(f"{i.member_id} {i.code.value}" for i in res.issues[:5]))
        return approvals.close_period(self.conn, month, res.expected, actor, as_of)

    def retro_lines(self, as_of: datetime) -> list[RetroLine]:
        closed = repo.closed_months(self.conn)
        self.last_retro_exceptions: list = []
        if not closed:
            return []
        book, spans = self.ratebook(), repo.spans_by_member(self.conn)
        repriced, unpriceable = [], set()
        for m in closed:
            res = self.price(m, as_of, book, spans)
            repriced += res.expected
            unpriceable |= {(i.member_id, i.coverage_month) for i in res.issues}
            self.last_retro_exceptions += res.issues
        return compute_retro(closed, repo.snapshots(self.conn, closed), repo.active_adjustment_lines(self.conn),
                             repriced, unpriceable)

    def propose_retro(self, as_of: datetime, trigger: str, trigger_ref: str, proposer: str = "retro_engine") -> str | None:
        return approvals.propose_adjustment(self.conn, trigger=trigger, trigger_ref=trigger_ref,
                                            lines=self.retro_lines(as_of), proposer=proposer, at=as_of)
