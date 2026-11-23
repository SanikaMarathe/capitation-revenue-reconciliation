"""Ground truth: what the engine and agents should conclude, written for the evals."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from data_gen import rates, spec
from data_gen.members import Population
from data_gen.remittance import StateSide

ZERO = Decimal("0.00")


@dataclass
class MemberMonthTruth:
    member_id: str
    coverage_month: date
    enrolled: bool
    region: str
    category: str
    booked_at_close: Decimal
    final_expected: Decimal
    state_regular_paid: Decimal
    state_retro_paid: Decimal
    state_recouped: Decimal
    state_net_paid: Decimal
    codes: list[str]
    notes: list[str]

    @property
    def retro_delta(self) -> Decimal:
        return self.final_expected - self.booked_at_close

    @property
    def final_variance(self) -> Decimal:
        return self.state_net_paid - self.final_expected


def build(pop: Population, st: StateSide) -> list[MemberMonthTruth]:
    paid: dict[tuple[str, date], dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for line in st.lines:
        paid[(line.member_id, line.coverage_month)][line.line_type] += line.amount

    keys = sorted(set(pop.truth) | set(paid))
    out: list[MemberMonthTruth] = []
    all_versions = rates.VERSION_ORDER
    for key in keys:
        mid, month = key
        codes = set(st.labels.get(key, set()))
        notes = list(st.notes.get(key, []))
        t = pop.truth.get(key)
        if t is not None:
            close = spec.close_date(month)
            rate_at_close = rates.rate(t.region, t.category, month, rates.known_at(close))
            final = rates.rate(t.region, t.category, month, all_versions)
            known = t.known_date <= close
            booked = rate_at_close if known else ZERO
            if not known:
                codes.add("RETRO_ENROLLMENT")
                notes.append(f"span received {t.known_date} after {month:%b} close {close}")
            if final != rate_at_close:
                codes.add("RETRO_RATE_CHANGE")
                notes.append(f"rate {rate_at_close} at close -> {final} after v3")
            region, category = t.region, t.category
        else:
            booked = final = ZERO
            line_cells = {(l.region, l.category) for l in st.lines if (l.member_id, l.coverage_month) == key}
            region, category = sorted(line_cells)[0]
        p = paid.get(key, {})
        reg, retro, recoup = p.get("regular", ZERO), p.get("retro_adjustment", ZERO), p.get("recoupment", ZERO)
        out.append(MemberMonthTruth(
            member_id=mid, coverage_month=month, enrolled=t is not None, region=region, category=category,
            booked_at_close=booked, final_expected=final, state_regular_paid=reg, state_retro_paid=retro,
            state_recouped=recoup, state_net_paid=reg + retro + recoup,
            codes=sorted(codes), notes=notes,
        ))
    return out
