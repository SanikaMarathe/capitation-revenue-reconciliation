"""The state's side: remittance (820-like) lines, remit headers and bank deposits.

The state starts from the same truth, then the seeded errors are applied.
Every seeded error is labelled in `labels` for the ground-truth file.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from data_gen import rates, spec
from data_gen.members import Population

RETRO_REFERENCE = "WDHS-AMEND-2026-05-20"


@dataclass
class Line:
    line_id: str
    remit_id: str
    payment_date: date
    coverage_month: date
    member_id: str
    region: str
    category: str
    amount: Decimal
    line_type: str
    reference: str = ""


@dataclass
class StateSide:
    lines: list[Line] = field(default_factory=list)
    remits: list[dict] = field(default_factory=list)
    deposits: list[dict] = field(default_factory=list)
    labels: dict[tuple[str, date], set[str]] = field(default_factory=lambda: defaultdict(set))
    notes: dict[tuple[str, date], list[str]] = field(default_factory=lambda: defaultdict(list))
    bank_labels: list[dict] = field(default_factory=list)


def remit_id_for(month: date) -> str:
    return f"R-{month:%Y-%m}"


def pay_date(month: date) -> date:
    return date(month.year, month.month, spec.REMIT_DAY)


def _next_month(m: date) -> date:
    return date(m.year + m.month // 12, m.month % 12 + 1, 1)


def build_state_side(rng: random.Random, pop: Population) -> StateSide:
    st = StateSide()
    seq = 0

    def add_line(month_paid: date, coverage: date, member: str, region: str, category: str,
                 amount: Decimal, line_type: str, reference: str = "") -> Line:
        nonlocal seq
        seq += 1
        line = Line(f"RL{seq:07d}", remit_id_for(month_paid), pay_date(month_paid), coverage, member,
                    region, category, amount, line_type, reference)
        st.lines.append(line)
        return line

    def label(key: tuple[str, date], code: str, note: str) -> None:
        st.labels[key].add(code)
        st.notes[key].append(note)

    truth_keys = sorted(pop.truth)
    used: set[tuple[str, date]] = set()

    # ---- state's view of the rate cell (with lag after life events)
    state_cell = {k: (t.region, t.category) for k, t in pop.truth.items()}
    for mid, first_adult in pop.aging_out.items():
        key = (mid, first_adult)
        if key in pop.truth and rng.random() < spec.STATE_LAG_SHARE:
            state_cell[key] = (pop.truth[key].region, "Child")
            label(key, "RATE_CELL_MISMATCH", "state still paid Child after member turned 19")
            used.add(key)
    for mid, move_month in pop.region_moves.items():
        key = (mid, move_month)
        prev = (mid, date(move_month.year, move_month.month - 1, 1))
        if key in pop.truth and prev in pop.truth and rng.random() < spec.STATE_LAG_SHARE:
            state_cell[key] = (pop.truth[prev].region, pop.truth[key].category)
            label(key, "RATE_CELL_MISMATCH", "state still paid the old region after a move")
            used.add(key)
    swap = {"Adult": "Expansion", "Expansion": "Adult", "Disabled": "Adult", "Child": "Adult"}
    for key in rng.sample([k for k in truth_keys if k not in used], spec.N_RANDOM_CELL_MISMATCH):
        r, c = state_cell[key]
        state_cell[key] = (r, swap[c])
        label(key, "RATE_CELL_MISMATCH", f"state paid {r} {swap[c]} instead of {r} {c}")
        used.add(key)

    # ---- missing from remit
    missing = set(rng.sample([k for k in truth_keys if k not in used], spec.N_MISSING))
    for key in missing:
        label(key, "MISSING_FROM_REMIT", "enrolled but no payment line")
    used |= missing

    # ---- stale rate: state kept paying the v1 rate for one changed cell in April
    stale_pool = [k for k in truth_keys if k[1] == spec.STALE_MONTH and (pop.truth[k].region, pop.truth[k].category) == spec.STALE_CELL
                  and state_cell[k] == spec.STALE_CELL and k not in used]
    stale = set(rng.sample(stale_pool, int(len(stale_pool) * spec.STALE_SHARE)))
    for key in stale:
        label(key, "STALE_RATE", "paid v1 rate after v2 change")

    def state_amount(key: tuple[str, date]) -> Decimal:
        r, c = state_cell[key]
        month = key[1]
        if key in stale:
            return spec.RATES_V1[(r, c)]
        return rates.rate(r, c, month, rates.state_known_at(pay_date(month)))

    # ---- regular payment lines
    regular: dict[tuple[str, date], Line] = {}
    for key in truth_keys:
        if key in missing:
            continue
        r, c = state_cell[key]
        regular[key] = add_line(key[1], key[1], key[0], r, c, state_amount(key), "regular")

    # ---- unexpected payment: state paid the month after termination
    term_pool = [(m, _next_month(last)) for m, last in pop.terminated.items() if _next_month(last) <= spec.MONTHS[-1]]
    term_pool = [k for k in term_pool if k not in pop.truth]
    unexpected = rng.sample(term_pool, min(spec.N_UNEXPECTED, len(term_pool)))
    recouped_unexpected = set(rng.sample(unexpected, int(len(unexpected) * spec.UNEXPECTED_RECOUPED_SHARE)))
    for key in unexpected:
        prev = (key[0], date(key[1].year, key[1].month - 1, 1))
        r, c = pop.truth[prev].region, pop.truth[prev].category
        state_cell[key] = (r, c)
        line = add_line(key[1], key[1], key[0], r, c, rates.rate(r, c, key[1], rates.state_known_at(pay_date(key[1]))), "regular")
        label(key, "UNEXPECTED_PAYMENT", "paid after termination")
        if key in recouped_unexpected and _next_month(key[1]) <= spec.MONTHS[-1]:
            add_line(_next_month(key[1]), key[1], key[0], r, c, -line.amount, "recoupment", line.line_id)
            label(key, "RECOUPMENT", "state recouped the post-termination payment next month")

    # ---- state wrongly recoups a valid payment
    valid_pool = [k for k in regular if k not in used and k not in stale and k[1] < spec.MONTHS[-1]]
    for key in rng.sample(valid_pool, spec.N_VALID_RECOUPED):
        line = regular[key]
        add_line(_next_month(key[1]), key[1], key[0], line.region, line.category, -line.amount, "recoupment", line.line_id)
        label(key, "RECOUPMENT", "state recouped a valid payment")
        used.add(key)

    # ---- duplicate payments (some overlapping with stale rate)
    dup_on_stale = rng.sample(sorted(stale), spec.N_DUPLICATE_ON_STALE)
    dup_other = rng.sample([k for k in regular if k not in used and k not in stale], spec.N_DUPLICATE - spec.N_DUPLICATE_ON_STALE)
    for key in dup_on_stale + dup_other:
        line = regular[key]
        add_line(key[1], key[1], key[0], line.region, line.category, line.amount, "regular")
        label(key, "DUPLICATE_PAYMENT", "same member-month paid twice")
        used.add(key)

    # ---- state pays the v3 retro difference in June, for Jan-Mar only (April left outstanding)
    june = spec.MONTHS[-1]
    for key, line in regular.items():
        if key[1] not in spec.STATE_RETRO_PAID_MONTHS or key in stale:
            continue
        new = rates.rate(line.region, line.category, key[1], rates.state_known_at(pay_date(june)))
        diff = new - line.amount
        if diff != 0:
            add_line(june, key[1], key[0], line.region, line.category, diff, "retro_adjustment", RETRO_REFERENCE)

    # ---- remit headers
    totals: dict[str, list] = defaultdict(lambda: [Decimal("0"), 0])
    for line in st.lines:
        totals[line.remit_id][0] += line.amount
        totals[line.remit_id][1] += 1
    for month in spec.MONTHS:
        rid = remit_id_for(month)
        st.remits.append({"remit_id": rid, "payment_date": pay_date(month),
                          "total_amount": totals[rid][0], "line_count": totals[rid][1]})

    # ---- bank deposits
    for i, remit in enumerate(st.remits, start=1):
        month = date(remit["payment_date"].year, remit["payment_date"].month, 1)
        dep_date = remit["payment_date"] + timedelta(days=1)
        amount = remit["total_amount"]
        reference = remit["remit_id"]
        if month in spec.BANK_TIMING_MONTHS:
            dep_date = date(month.year, month.month + 1, 2)
            st.bank_labels.append({"remit_id": remit["remit_id"], "deposit_id": f"D{i:04d}", "code": "BANK_TIMING",
                                   "note": "deposit landed the following month"})
        if month in spec.BANK_SHORT:
            amount = amount - spec.BANK_SHORT[month]
            st.bank_labels.append({"remit_id": remit["remit_id"], "deposit_id": f"D{i:04d}", "code": "BANK_SHORT",
                                   "note": f"deposit short by {spec.BANK_SHORT[month]}; no explaining record"})
        if month in spec.NO_REFERENCE_MONTHS:
            reference = ""
            st.bank_labels.append({"remit_id": remit["remit_id"], "deposit_id": f"D{i:04d}", "code": "NO_REFERENCE",
                                   "note": "bank line has no remit reference; amount/date candidate only"})
        st.deposits.append({"deposit_id": f"D{i:04d}", "deposit_date": dep_date, "amount": amount,
                            "payer": spec.PAYER, "reference": reference})
    st.deposits.append({"deposit_id": "D9001", "deposit_date": date(2026, 4, 15), "amount": Decimal("15000.00"),
                        "payer": "WESTLAKE DHS QUALITY INCENTIVE", "reference": ""})
    st.bank_labels.append({"remit_id": "", "deposit_id": "D9001", "code": "UNMATCHED_DEPOSIT",
                           "note": "non-capitation deposit; should stay unmatched"})
    return st
