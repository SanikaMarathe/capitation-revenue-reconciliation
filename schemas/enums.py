"""Closed vocabularies. LLM outputs must use these exact values or be rejected."""

from __future__ import annotations

from enum import Enum


class Region(str, Enum):
    NORTH = "North"
    SOUTH = "South"
    EAST = "East"
    WEST = "West"


class Category(str, Enum):
    CHILD = "Child"
    ADULT = "Adult"
    DISABLED = "Disabled"
    EXPANSION = "Expansion"


class DataClass(str, Enum):
    """Privacy classification attached to every schema field (ARCHITECTURE.md section 9)."""

    C1_DIRECT_IDENTIFIER = "C1"   # names, DOB: never leaves the storage layer
    C2_PHI = "C2"                 # member_id, enrollment: pseudonymised before LLM
    C3_FINANCIAL = "C3"           # amounts, references, ledger
    C4_PUBLIC = "C4"              # published rates


class DocFormat(str, Enum):
    PDF = "pdf"
    XLSX = "xlsx"
    EMAIL = "email"


class RateVersionStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"   # display label only; never filters historical lookups


class VersionKind(str, Enum):
    FULL = "full"
    AMENDMENT = "amendment"


class LineType(str, Enum):
    REGULAR = "regular"
    RETRO_ADJUSTMENT = "retro_adjustment"
    RECOUPMENT = "recoupment"


class DiscrepancyCode(str, Enum):
    MISSING_FROM_REMIT = "MISSING_FROM_REMIT"
    RATE_CELL_MISMATCH = "RATE_CELL_MISMATCH"
    STALE_RATE = "STALE_RATE"
    UNEXPECTED_PAYMENT = "UNEXPECTED_PAYMENT"
    DUPLICATE_PAYMENT = "DUPLICATE_PAYMENT"
    RECOUPMENT = "RECOUPMENT"
    RETRO_ENROLLMENT = "RETRO_ENROLLMENT"
    RETRO_RATE_CHANGE = "RETRO_RATE_CHANGE"
    BANK_TIMING = "BANK_TIMING"
    BANK_SHORT = "BANK_SHORT"


class EngineException(str, Enum):
    """Data problems the engine refuses to paper over (never a silent $0)."""

    UNPRICED = "UNPRICED"
    ENROLLMENT_CONFLICT = "ENROLLMENT_CONFLICT"


class RecommendedAction(str, Enum):
    SUBMIT_ENROLLMENT_CORRECTION = "submit_enrollment_correction"
    DISPUTE_RATE_WITH_STATE = "dispute_rate_with_state"
    REQUEST_MISSING_PAYMENT = "request_missing_payment"
    CONFIRM_RECOUPMENT = "confirm_recoupment"
    REFUND_OR_OFFSET = "refund_or_offset"
    NO_ACTION_TIMING = "no_action_timing"
    ESCALATE_TO_HUMAN = "escalate_to_human"


class RuleStrength(str, Enum):
    STRONG = "strong"   # all corroborating fields present
    WEAK = "weak"       # partial signal -> goes to investigator


class VarianceLevel(str, Enum):
    MEMBER = "member"
    REMIT_TO_BANK = "remit_to_bank"


class VarianceStatus(str, Enum):
    OBSERVED = "observed"
    POSSIBLE_CAUSE = "possible_cause"
    NEEDS_HUMAN = "needs_human"
    CONFIRMED = "confirmed"
    OVERRIDDEN = "overridden"


class AdjustmentStatus(str, Enum):
    PROPOSED = "proposed"
    REJECTED = "rejected"
    POSTED = "posted"   # approval and posting are one atomic step
    STALE = "stale"     # computed on a baseline that never posted; must be regenerated, never posted


class PeriodStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class ActorType(str, Enum):
    SYSTEM = "system"
    AGENT = "agent"
    HUMAN = "human"
