from __future__ import annotations

import inspect
import logging
from datetime import date
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

import schemas.audit
import schemas.enrollment
import schemas.ledger
import schemas.rates
import schemas.remittance
import schemas.variance
from privacy.classification import fields_of_class, unclassified_fields
from privacy.masking import mask_dob, mask_name
from privacy.redacting_logger import RedactingFilter
from schemas.base import LLMOutput, Record
from schemas.enrollment import Member
from schemas.enums import DataClass
from schemas.rates import RateNotice
from schemas.remittance import BankDeposit

MODULES = [schemas.audit, schemas.enrollment, schemas.ledger, schemas.rates, schemas.remittance, schemas.variance]


def _record_models() -> list[type[BaseModel]]:
    out = []
    for mod in MODULES:
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if issubclass(obj, Record) and obj is not Record and obj.__module__ == mod.__name__:
                out.append(obj)
    return out


@pytest.mark.parametrize("model", _record_models(), ids=lambda m: m.__name__)
def test_every_record_field_is_classified(model):
    assert unclassified_fields(model) == set()


def test_direct_identifiers_exist_only_on_member():
    holders = [m.__name__ for m in _record_models() if fields_of_class(m, DataClass.C1_DIRECT_IDENTIFIER)]
    assert holders == ["Member"]
    assert fields_of_class(Member, DataClass.C1_DIRECT_IDENTIFIER) == {"first_name", "last_name", "dob"}


def test_money_rejects_float_and_quantizes():
    with pytest.raises(ValidationError):
        BankDeposit(deposit_id="D1", deposit_date=date(2026, 1, 5), amount=830.0, payer="State")
    dep = BankDeposit(deposit_id="D1", deposit_date=date(2026, 1, 5), amount="830.005", payer="State")
    assert dep.amount == Decimal("830.01")


def test_llm_outputs_reject_unknown_fields():
    assert issubclass(RateNotice, LLMOutput)
    with pytest.raises(ValidationError):
        RateNotice.model_validate({
            "kind": "full", "is_retroactive": False, "published_date": "2025-12-01",
            "rates": [], "confidence": 0.9, "surprise": "field",
        })


def test_llm_rate_must_be_two_decimal_string():
    bad = {"kind": "amendment", "is_retroactive": True, "published_date": "2026-05-20", "confidence": 0.9,
           "rates": [{"region": "North", "category": "Adult", "effective_from": "2026-01-01",
                      "pmpm": "420", "source_ref": "email:para2"}]}
    with pytest.raises(ValidationError):
        RateNotice.model_validate(bad)
    bad["rates"][0]["pmpm"] = "420.00"
    assert RateNotice.model_validate(bad).rates[0].pmpm == "420.00"


def test_masking():
    assert mask_name("Asha", "Ramirez") == "A*** R******"
    assert mask_dob(date(1990, 4, 12)) == "****-**-**"


def test_redacting_logger(caplog):
    logger = logging.getLogger("redact-test")
    logger.addFilter(RedactingFilter(names={"Asha"}, dobs_iso={"1990-04-12"}))
    with caplog.at_level(logging.INFO, logger="redact-test"):
        logger.info("member %s (%s) born %s", "M000123", "Asha", "1990-04-12")
    text = caplog.text
    assert "Asha" not in text and "M000123" not in text and "1990-04-12" not in text
