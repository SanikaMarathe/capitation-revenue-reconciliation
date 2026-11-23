"""Generator sanity: deterministic, labelled, parseable documents, no PHI where it shouldn't be."""

from __future__ import annotations

import csv
import hashlib
from decimal import Decimal

import pdfplumber
import pytest
from openpyxl import load_workbook

from data_gen import spec
from data_gen.generate import GT, RAW, generate


def _digest(paths) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.read_bytes())
    return h.hexdigest()


@pytest.fixture(scope="module")
def generated():
    return generate()


def _csv(path):
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_deterministic(generated):
    files = list(RAW.glob("*.csv")) + list(GT.glob("*.csv")) + list(GT.glob("*.json"))
    first = _digest(files)
    generate()
    assert _digest(files) == first


def test_issue_rate_in_target_band(generated):
    assert 3.0 <= generated["payment_issue_rate_pct"] <= 5.0


def test_retro_scope_is_jan_to_apr_only(generated):
    rows = _csv(GT / "retro_deltas.csv")
    months = {r["coverage_month"] for r in rows if "RETRO_RATE_CHANGE" in r["causes"]}
    assert months == {"2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"}
    south_disabled = {r["coverage_month"] for r in rows
                      if r["region"] == "South" and r["category"] == "Disabled" and "RETRO_RATE_CHANGE" in r["causes"]}
    assert south_disabled == {"2026-01-01", "2026-02-01", "2026-03-01"}   # v3 interval ends in March


def test_state_settles_retro_for_jan_to_mar_only(generated):
    rows = _csv(GT / "retro_deltas.csv")
    april_na = [r for r in rows if r["coverage_month"] == "2026-04-01" and r["region"] == "North"
                and r["category"] == "Adult" and r["causes"] == "RETRO_RATE_CHANGE"]
    assert april_na and all(Decimal(r["state_retro_paid"]) == 0 for r in april_na)


def test_pdf_contains_all_v1_rates(generated):
    with pdfplumber.open(RAW / "rate_docs" / "v1_rate_notice_CY2026.pdf") as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    for value in spec.RATES_V1.values():
        assert f"${value:,.2f}" in text


def test_xlsx_has_16_rate_rows(generated):
    ws = load_workbook(RAW / "rate_docs" / "v2_rate_sheet_eff_2026-04-01.xlsx").active
    rates = [r for r in ws.iter_rows(min_row=5, values_only=True) if isinstance(r[3], (int, float))]
    assert len(rates) == 16


def test_email_mentions_only_changed_cells(generated):
    text = (RAW / "rate_docs" / "v3_rate_amendment_email.txt").read_text(encoding="utf-8")
    assert "$420.00" in text and "$1,172.00" in text and "March 31, 2026" in text


def test_no_member_names_outside_members_file(generated):
    members = _csv(RAW / "members.csv")
    names = {m["first_name"] for m in members} | {m["last_name"] for m in members}
    for path in [RAW / "enrollment_834.csv", RAW / "remittance_820_lines.csv", GT / "discrepancies.csv"]:
        text = path.read_text(encoding="utf-8")
        assert not any(n in text for n in names), path.name


def test_rate_documents_are_byte_identical_across_runs(tmp_path):
    """The sha256 of a document is its identity for idempotent ingestion, so regenerating must not change it."""
    import hashlib
    import time

    from data_gen import docs

    digests = []
    for i in range(2):
        docs.write_pdf(tmp_path / f"n{i}.pdf")
        docs.write_xlsx(tmp_path / f"s{i}.xlsx")
        digests.append([hashlib.sha256((tmp_path / f).read_bytes()).hexdigest() for f in (f"n{i}.pdf", f"s{i}.xlsx")])
        time.sleep(1.1)          # a timestamp in the file would now differ
    assert digests[0] == digests[1]
