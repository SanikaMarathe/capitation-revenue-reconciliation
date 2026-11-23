from __future__ import annotations

from datetime import date

from schemas.base import Money, Record, classified
from schemas.enums import Category, DataClass, LineType, Region

C2 = DataClass.C2_PHI
C3 = DataClass.C3_FINANCIAL


class Remit(Record):
    remit_id: str = classified(C3)
    payment_date: date = classified(C3)
    total_amount: Money = classified(C3)
    line_count: int = classified(C3)


class RemittanceLine(Record):
    line_id: str = classified(C3)
    remit_id: str = classified(C3)
    payment_date: date = classified(C3)
    coverage_month: date = classified(C2)
    member_id: str = classified(C2)
    region: Region = classified(C2)
    category: Category = classified(C2)
    amount: Money = classified(C3)
    line_type: LineType = classified(C3)
    reference: str = classified(C3, default="")   # e.g. reversed line, or rate revision


class BankDeposit(Record):
    deposit_id: str = classified(C3)
    deposit_date: date = classified(C3)
    amount: Money = classified(C3)
    payer: str = classified(C3)
    reference: str = classified(C3, default="")   # remit_id when the bank carries it
