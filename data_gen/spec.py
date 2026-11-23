"""Everything the synthetic world is built from. Change numbers here, not in code.

The state is fictional ("State of Westlake"). All people are invented.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

SEED = 20260101
N_MEMBERS = 2000
MONTHS = [date(2026, m, 1) for m in range(1, 7)]          # Jan..Jun 2026 coverage months

REGIONS = ["North", "South", "East", "West"]
CATEGORIES = ["Child", "Adult", "Disabled", "Expansion"]
REGION_WEIGHTS = [0.30, 0.25, 0.25, 0.20]
CATEGORY_WEIGHTS = [0.45, 0.25, 0.10, 0.20]               # typical Medicaid mix

STATE_NAME = "State of Westlake (fictional)"
AGENCY = "Westlake Department of Health Services"
PAYER = "WESTLAKE DHS MEDICAID"

# ---------------------------------------------------------------- rates (ground truth)
# v1: full table, effective Jan 1 2026, published Dec 2025 (PDF)
RATES_V1 = {
    ("North", "Child"): D("245.00"), ("North", "Adult"): D("400.00"), ("North", "Disabled"): D("1210.00"), ("North", "Expansion"): D("505.00"),
    ("South", "Child"): D("230.00"), ("South", "Adult"): D("385.00"), ("South", "Disabled"): D("1150.00"), ("South", "Expansion"): D("490.00"),
    ("East", "Child"): D("250.00"),  ("East", "Adult"): D("410.00"),  ("East", "Disabled"): D("1240.00"),  ("East", "Expansion"): D("515.00"),
    ("West", "Child"): D("225.00"),  ("West", "Adult"): D("375.00"),  ("West", "Disabled"): D("1125.00"),  ("West", "Expansion"): D("480.00"),
}

# v2: full sheet, effective Apr 1 2026 (prospective), published Mar 15 (Excel). Five cells change.
V2_CHANGES = {
    ("East", "Child"): D("262.50"),
    ("South", "Disabled"): D("1195.00"),
    ("West", "Expansion"): D("498.00"),
    ("North", "Child"): D("252.00"),
    ("South", "Adult"): D("392.50"),
}
RATES_V2 = {**RATES_V1, **V2_CHANGES}

# v3: retroactive amendment by email, published May 20, two cells:
#   North Adult  $400.00 -> $420.00 effective Jan 1 2026, open-ended
#   South Disabled $1,150.00 -> $1,172.00 for Jan 1 - Mar 31 2026 only (v2 already governs from Apr 1)
V3_ROWS = [
    {"region": "North", "category": "Adult", "effective_from": date(2026, 1, 1), "effective_to": None, "pmpm": D("420.00")},
    {"region": "South", "category": "Disabled", "effective_from": date(2026, 1, 1), "effective_to": date(2026, 3, 1), "pmpm": D("1172.00")},
]

V1_EFFECTIVE = date(2026, 1, 1)
V2_EFFECTIVE = date(2026, 4, 1)

PUBLISHED = {"v1": date(2025, 12, 1), "v2": date(2026, 3, 15), "v3": date(2026, 5, 20)}
# When the plan approves each version (knowledge time). Used for ground-truth "booked at close".
APPROVED = {"v1": date(2025, 12, 15), "v2": date(2026, 3, 20), "v3": date(2026, 5, 22)}


def close_date(month: date) -> date:
    """Month M closes on the 10th of M+1."""
    return date(month.year + (month.month // 12), month.month % 12 + 1, 10)


# ---------------------------------------------------------------- payment calendar
REMIT_DAY = 25                    # state pays coverage month M on the 25th of M
BANK_TIMING_MONTHS = [date(2026, 2, 1), date(2026, 5, 1)]   # deposit lands in M+1
BANK_SHORT = {date(2026, 3, 1): D("1250.00")}               # deposit short, no explaining record
NO_REFERENCE_MONTHS = [date(2026, 4, 1)]                    # bank file lacks the remit reference
STATE_RETRO_PAID_MONTHS = [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]   # June remit pays v3 retro for Jan-Mar only

# ---------------------------------------------------------------- seeded discrepancy volumes
N_MID_PERIOD_JOINERS = 0.12       # share of members starting mid-period
N_TERMINATIONS = 0.08
N_REGION_MOVES = 0.03
N_AGING_OUT = 25                  # Child members turning 19 during Jan-Apr
STATE_LAG_SHARE = 0.4             # of aging/moves, state keeps the old cell for one month
N_RETRO_ENROLLMENT = 12
N_MISSING = 60
N_RANDOM_CELL_MISMATCH = 10
STALE_CELL = ("East", "Child")
STALE_MONTH = date(2026, 4, 1)
STALE_SHARE = 0.85                # state still paid the v1 rate for this share of the cell
N_UNEXPECTED = 30                 # paid the month after termination
UNEXPECTED_RECOUPED_SHARE = 0.5   # of those, state recoups next month
N_VALID_RECOUPED = 10             # state wrongly recoups a valid payment
N_DUPLICATE = 25
N_DUPLICATE_ON_STALE = 5          # overlap: duplicate of a stale-rate line

# Synthetic names. Deliberately avoid words used elsewhere in the domain
# (regions, categories, months) so the privacy scanner has no false positives.
FIRST_NAMES = [
    "Asha", "Bilal", "Carmen", "Dmitri", "Elif", "Farah", "Gideon", "Hana", "Ibrahim", "Jonah",
    "Keiko", "Lucia", "Mateo", "Nadia", "Omar", "Priya", "Quentin", "Rosa", "Samir", "Tamsin",
    "Umar", "Vera", "Wendell", "Ximena", "Yusuf", "Zara", "Anika", "Bruno", "Celine", "Darius",
    "Esme", "Felix", "Greta", "Hector", "Ines", "Jasper", "Kofi", "Leila", "Marcus", "Noor",
    "Olga", "Pavel", "Rania", "Stellan", "Talia", "Viktor", "Wanda", "Yara", "Zoltan", "Amara",
]
LAST_NAMES = [
    "Ramirez", "Okafor", "Lindqvist", "Haddad", "Novak", "Takahashi", "Mbeki", "Castellano", "Petrov", "Nguyen",
    "Fitzgerald", "Abernathy", "Kowalski", "Delacroix", "Oyelaran", "Szabo", "Varga", "Henriksen", "Moreau", "Achebe",
    "Bianchi", "Calloway", "Dorsey", "Esposito", "Fairbanks", "Galloway", "Halvorsen", "Iwasaki", "Jablonski", "Karimi",
    "Lombardi", "Mancini", "Nakamura", "Ostrowski", "Pellegrino", "Quiroga", "Rasmussen", "Sorensen", "Tremblay", "Ulrich",
    "Vasquez", "Whitfield", "Yilmaz", "Zielinski", "Amsel", "Brannigan", "Corrigan", "Dragomir", "Eklund", "Ferrante",
]
