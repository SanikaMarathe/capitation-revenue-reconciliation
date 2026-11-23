"""Members, enrollment spans and the TRUTH calendar.

The truth calendar (who is covered in which rate cell each month) is built first,
directly from each member's life events. Enrollment spans are then written FROM it.
The engine later has to rebuild the calendar from spans alone, so ground truth and
engine follow independent paths.

Proration rule: first-of-month. A member counts for month M iff a span covers day 1 of M.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from data_gen import spec


@dataclass
class MemberRec:
    member_id: str
    first_name: str
    last_name: str
    dob: date


@dataclass
class Span:
    span_id: str
    member_id: str
    start_date: date
    end_date: date | None
    region: str
    category: str
    received_date: date
    source: str = "state_834"


@dataclass
class TruthMonth:
    region: str
    category: str
    span_id: str
    known_date: date      # when the plan learned of this coverage (span received_date)


@dataclass
class Population:
    members: list[MemberRec] = field(default_factory=list)
    spans: list[Span] = field(default_factory=list)
    truth: dict[tuple[str, date], TruthMonth] = field(default_factory=dict)
    # Labels for the generator's own bookkeeping (used to seed state-side errors)
    aging_out: dict[str, date] = field(default_factory=dict)      # member -> first month as Adult
    region_moves: dict[str, date] = field(default_factory=dict)   # member -> first month in new region
    terminated: dict[str, date] = field(default_factory=dict)     # member -> last covered month
    retro_enrolled: dict[str, list[date]] = field(default_factory=dict)  # member -> months unknown at close


def _first_of_next_month(d: date) -> date:
    return date(d.year + d.month // 12, d.month % 12 + 1, 1)


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _rand_date(rng: random.Random, lo: date, hi: date) -> date:
    return lo + timedelta(days=rng.randint(0, (hi - lo).days))


def _dob_for(rng: random.Random, category: str) -> date:
    ref = date(2026, 1, 1)
    if category == "Child":
        age = rng.randint(0, 17)
    elif category == "Disabled":
        age = rng.randint(5, 80)
    else:
        age = rng.randint(19, 64)
    return _rand_date(rng, date(ref.year - age - 1, 1, 2), date(ref.year - age - 1, 12, 28))


def build_population(rng: random.Random) -> Population:
    pop = Population()
    span_seq = 0

    def new_span(**kw) -> Span:
        nonlocal span_seq
        span_seq += 1
        return Span(span_id=f"SP{span_seq:06d}", **kw)

    child_ids: list[str] = []
    profiles: dict[str, dict] = {}
    for i in range(1, spec.N_MEMBERS + 1):
        mid = f"M{i:06d}"
        region = rng.choices(spec.REGIONS, spec.REGION_WEIGHTS)[0]
        category = rng.choices(spec.CATEGORIES, spec.CATEGORY_WEIGHTS)[0]
        dob = _dob_for(rng, category)
        pop.members.append(MemberRec(mid, rng.choice(spec.FIRST_NAMES), rng.choice(spec.LAST_NAMES), dob))

        if rng.random() < spec.N_MID_PERIOD_JOINERS:
            # Mid-month join -> first covered month is the following month.
            start = _rand_date(rng, date(2026, 1, 2), date(2026, 5, 27))
            if start.day == 1:
                start = start + timedelta(days=1)
            joiner = True
        else:
            start = _rand_date(rng, date(2018, 1, 1), date(2025, 12, 1))
            joiner = False
        end = None
        first_month = _month_start(start) if start.day == 1 else _first_of_next_month(start)
        if rng.random() < spec.N_TERMINATIONS:
            term_month = rng.choice([m for m in spec.MONTHS if m >= first_month] or [spec.MONTHS[-1]])
            end = date(term_month.year, term_month.month, rng.randint(2, 27))   # mid-month term keeps the month
        profiles[mid] = {"region": region, "category": category, "start": start, "end": end,
                         "joiner": joiner, "first_month": first_month}
        if category == "Child" and not joiner and end is None:
            child_ids.append(mid)

    # Aging out: some children turn 19 during Jan-Apr; category becomes Adult from the next month.
    for mid in rng.sample(child_ids, spec.N_AGING_OUT):
        birthday_month = rng.choice([1, 2, 3, 4])
        bday = date(2026, birthday_month, rng.randint(2, 27))
        idx = int(mid[1:]) - 1
        pop.members[idx].dob = date(bday.year - 19, bday.month, bday.day)
        pop.aging_out[mid] = _first_of_next_month(bday)

    # Region moves for a few established, non-terminating, non-aging members.
    movable = [m for m, p in profiles.items() if not p["joiner"] and p["end"] is None and m not in pop.aging_out]
    for mid in rng.sample(movable, int(spec.N_MEMBERS * spec.N_REGION_MOVES)):
        pop.region_moves[mid] = rng.choice(spec.MONTHS[1:])

    # Retro enrollment: joiners whose span reaches the plan only after their first month(s) closed.
    retro_pool = [m for m, p in profiles.items()
                  if p["joiner"] and p["end"] is None and p["first_month"] in (date(2026, 2, 1), date(2026, 3, 1))]
    retro_ids = set(rng.sample(retro_pool, min(spec.N_RETRO_ENROLLMENT, len(retro_pool))))

    for mid, p in profiles.items():
        segments: list[tuple[date, date | None, str, str]] = []
        region, category = p["region"], p["category"]
        change = None
        if mid in pop.aging_out:
            change = (pop.aging_out[mid], region, "Adult")
        elif mid in pop.region_moves:
            new_region = rng.choice([r for r in spec.REGIONS if r != region])
            change = (pop.region_moves[mid], new_region, category)
        if change:
            cdate, new_region, new_cat = change
            segments.append((p["start"], cdate - timedelta(days=1), region, category))
            segments.append((cdate, p["end"], new_region, new_cat))
        else:
            segments.append((p["start"], p["end"], region, category))

        for seg_start, seg_end, seg_region, seg_cat in segments:
            if mid in retro_ids:
                late_close = spec.close_date(_first_of_next_month(p["first_month"]))
                received = late_close + timedelta(days=5)
            elif seg_start < date(2026, 1, 1):
                received = min(seg_start + timedelta(days=rng.randint(0, 20)), date(2025, 12, 20))
            else:
                received = seg_start - timedelta(days=rng.randint(5, 25))
            pop.spans.append(new_span(member_id=mid, start_date=seg_start, end_date=seg_end,
                                      region=seg_region, category=seg_cat, received_date=received))
        if p["end"] is not None:
            pop.terminated[mid] = _month_start(p["end"])

    # Truth calendar straight from the spans' coverage of day 1 (first-of-month rule).
    for sp in pop.spans:
        for month in spec.MONTHS:
            if sp.start_date <= month and (sp.end_date is None or sp.end_date >= month):
                key = (sp.member_id, month)
                assert key not in pop.truth, f"overlapping coverage for {key}"
                pop.truth[key] = TruthMonth(sp.region, sp.category, sp.span_id, sp.received_date)

    for mid in retro_ids:
        months = sorted(m for (mm, m), t in pop.truth.items() if mm == mid and t.known_date > spec.close_date(m))
        if months:
            pop.retro_enrolled[mid] = months
    return pop
