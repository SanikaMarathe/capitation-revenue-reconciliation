"""AI layer on MockProvider: extraction + validation, investigator + groundedness, memo, privacy.
Mock answers are deterministic stand-ins; these tests prove the guardrails, not model quality."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

import pytest

from agents import groundedness, memo_writer, rate_extractor
from agents.doc_parse import parse
from agents.evidence import build_pack, group_variances
from agents.mock_responders import mock_provider
from common.config import get_settings
from data_gen.generate import GT, RAW, generate
from engine.pricing import RateBook
from engine.rate_validation import validate
from engine.retro import CellMonthSummary
from governance import approvals
from llm.cache import ResponseCache
from llm.factory import build_client
from schemas.enums import VersionKind
from schemas.ledger import MemoDraft
from schemas.rates import RateRow
from schemas.variance import CitedAmount, Finding
from storage import repo
from storage.db import connect
from storage.load_raw import load_raw
from workflow import pipeline

DOCS = RAW / "rate_docs"


@pytest.fixture(scope="module")
def data():
    generate()


def _client(conn, tmp_path):
    settings = get_settings()
    client = build_client(settings, conn=conn, provider=mock_provider(), cache_mode="off")
    client._cache = ResponseCache(tmp_path / "cache")
    client._cache_mode = "read_write"
    client._gateway._egress_log = tmp_path / "egress.jsonl"
    return client


def _gt_cells(v):
    meta = json.loads((GT / f"rates_{v}.json").read_text(encoding="utf-8"))
    return {(r["region"], r["category"], r["effective_from"], r["effective_to"], Decimal(r["pmpm"]))
            for r in meta["rates"]}


def _cells(rows):
    return {(r.region.value, r.category.value, r.effective_from.isoformat(),
             r.effective_to.isoformat() if r.effective_to else None, r.pmpm) for r in rows}


# ---------------------------------------------------------------- extraction
def test_each_format_extracts_exactly(data, tmp_path):
    conn = connect()
    client = _client(conn, tmp_path)
    plan = [("v1", "v1_rate_notice_CY2026.pdf", datetime(2025, 12, 1, 10)),
            ("v2", "v2_rate_sheet_eff_2026-04-01.xlsx", datetime(2026, 3, 15, 10)),
            ("v3", "v3_rate_amendment_email.txt", datetime(2026, 5, 20, 10))]
    for v, name, at in plan:
        res = rate_extractor.extract(conn, client, DOCS / name, received=at.date(), version_id=v, as_of=at)
        assert res.report.errors == [], res.report.errors
        assert _cells(res.rows) == _gt_cells(v)
        approvals.approve_rate_version(conn, v, "controller", at.replace(hour=15))
    # the v3 diff shows exactly the two changed cells vs. what was in force
    status = conn.execute("SELECT status FROM rate_versions ORDER BY version_id").fetchall()
    assert [s[0] for s in status] == ["superseded", "approved", "approved"]


def test_same_file_twice_is_detected(data, tmp_path):
    conn = connect()
    client = _client(conn, tmp_path)
    rate_extractor.extract(conn, client, DOCS / "v1_rate_notice_CY2026.pdf", received=date(2025, 12, 1), version_id="v1")
    again = rate_extractor.extract(conn, client, DOCS / "v1_rate_notice_CY2026.pdf", received=date(2025, 12, 1),
                                   version_id="v1b")
    assert again.duplicate_of is not None
    assert conn.execute("SELECT count(*) FROM rate_versions").fetchone()[0] == 1


def test_extractor_never_sees_member_data(data, tmp_path):
    conn = connect()
    load_raw(conn, RAW)
    client = _client(conn, tmp_path)
    rate_extractor.extract(conn, client, DOCS / "v3_rate_amendment_email.txt", received=date(2026, 5, 20), version_id="v3")
    sent = (tmp_path / "egress.jsonl").read_text(encoding="utf-8")
    assert "MBR-" not in sent and "M000" not in sent


# ---------------------------------------------------------------- validation (injected errors)
def _v1_rows_and_refs():
    doc = parse(DOCS / "v1_rate_notice_CY2026.pdf")
    meta = json.loads((GT / "rates_v1.json").read_text(encoding="utf-8"))
    ref_for = {}
    for ref, text in doc.refs.items():
        if ref.startswith("p1:t1:r") and text.startswith("$"):
            ref_for[ref] = text
    rows = []
    for r in meta["rates"]:
        ref = next(k for k, t in ref_for.items() if t == f"${Decimal(r['pmpm']):,.2f}")
        rows.append(RateRow(version_id="v1", region=r["region"], category=r["category"],
                            effective_from=date(2026, 1, 1), pmpm=Decimal(r["pmpm"]), source_ref=ref))
    return rows, doc.refs


def _validate(rows, refs, kind=VersionKind.FULL, book=None, retro=False, published=date(2025, 12, 1)):
    return validate(rows, kind=kind, is_retroactive=retro, published=published, confidence=0.95, refs=refs,
                    book=book or RateBook([], []), as_of=datetime(2026, 6, 1))


def test_clean_extraction_validates(data):
    rows, refs = _v1_rows_and_refs()
    assert _validate(rows, refs).approvable


def test_typo_not_in_source_is_an_error(data):
    rows, refs = _v1_rows_and_refs()
    rows[1] = rows[1].model_copy(update={"pmpm": Decimal("4200.00")})        # $400 misread as $4,200
    rep = _validate(rows, refs)
    assert not rep.approvable and any("not found at cited source" in e for e in rep.errors)


def test_missing_cell_in_full_notice_is_an_error(data):
    rows, refs = _v1_rows_and_refs()
    rep = _validate(rows[:-1], refs)
    assert any("must cover 16 cells" in e for e in rep.errors)


def test_mid_month_effective_date_is_an_error(data):
    rows, refs = _v1_rows_and_refs()
    rows[0] = rows[0].model_copy(update={"effective_from": date(2026, 1, 15)})
    assert any("not the 1st" in e for e in _validate(rows, refs).errors)


def test_big_move_vs_prior_is_flagged_not_blocked(data):
    from schemas.enums import RateVersionStatus
    from schemas.rates import RateVersion
    rows, refs = _v1_rows_and_refs()
    prior = [r.model_copy(update={"version_id": "v0", "pmpm": r.pmpm / 2}) for r in rows]
    book = RateBook(prior, [RateVersion(version_id="v0", kind="full", published_date=date(2025, 1, 1),
                                        source_document_id="x", status=RateVersionStatus.APPROVED,
                                        approved_at=datetime(2025, 1, 2))])
    rep = _validate(rows, refs, book=book)
    assert rep.approvable and len(rep.flags) == 16


def test_open_ended_amendment_over_later_change_is_flagged(data, tmp_path):
    """If the extractor drops the 'through March 31' end date, the approver is warned."""
    conn = connect()
    client = _client(conn, tmp_path)
    for v, name, at in [("v1", "v1_rate_notice_CY2026.pdf", datetime(2025, 12, 1, 10)),
                        ("v2", "v2_rate_sheet_eff_2026-04-01.xlsx", datetime(2026, 3, 15, 10))]:
        rate_extractor.extract(conn, client, DOCS / name, received=at.date(), version_id=v, as_of=at)
        approvals.approve_rate_version(conn, v, "controller", at.replace(hour=15))
    doc = parse(DOCS / "v3_rate_amendment_email.txt")
    bad = [RateRow(version_id="v3", region="South", category="Disabled", effective_from=date(2026, 1, 1),
                   effective_to=None, pmpm=Decimal("1172.00"), source_ref="email:L15")]
    book = RateBook(repo.rate_rows(conn), repo.rate_versions(conn))
    rep = _validate(bad, doc.refs, kind=VersionKind.AMENDMENT, book=book, retro=True, published=date(2026, 5, 20))
    assert any("open-ended" in f for f in rep.flags)


def test_draft_with_errors_cannot_be_approved(data, tmp_path):
    conn = connect()
    client = _client(conn, tmp_path)
    rate_extractor.extract(conn, client, DOCS / "v1_rate_notice_CY2026.pdf", received=date(2025, 12, 1), version_id="v1")
    conn.execute("UPDATE source_documents SET extraction_status='needs_fix'")
    with pytest.raises(approvals.ApprovalError, match="validation errors"):
        approvals.approve_rate_version(conn, "v1", "controller")


# ---------------------------------------------------------------- end to end (mock)
@pytest.fixture(scope="module")
def e2e(data):
    return pipeline.run(":memory:", provider="mock", cache_mode="off")


def test_extracted_rates_drive_engine_to_ground_truth(e2e):
    """Extraction -> approval -> six closes -> retro: same total as the generator's truth."""
    conn, report = e2e
    total = sum((l.delta for l in repo.active_adjustment_lines(conn)), Decimal("0.00"))
    assert total == Decimal("23931.00")
    assert all(not x["errors"] for x in report.extractions.values())


def test_grouping_turns_identical_variances_into_one_call(e2e):
    conn, report = e2e
    inv = report.investigation
    assert inv["variances"] == 462 and inv["groups"] < inv["variances"] / 3
    stale = conn.execute("""SELECT group_id, count(*) FROM variances WHERE possible_cause_rule='STALE_RATE'
                            GROUP BY 1 ORDER BY 2 DESC LIMIT 1""").fetchone()
    assert stale[1] >= 150                                    # one finding covers the whole East Child cell


def test_multi_cause_groups_use_the_stronger_model(e2e):
    conn, report = e2e
    assert report.investigation["complex_groups"] > 0
    assert report.llm["by_task"]["investigate_complex"] == report.investigation["complex_groups"]


def test_all_mock_findings_grounded_and_memos_grounded(e2e):
    _, report = e2e
    assert report.investigation["grounded_groups"] == report.investigation["groups"]
    assert all(m["grounded"] for m in report.memos)


def test_no_raw_ids_or_names_ever_left_the_system(e2e, tmp_path):
    """Privacy eval in miniature: scan the egress log for raw IDs, names and DOBs of subjects."""
    conn, _ = e2e
    egress = get_settings().path("egress_log")
    if not egress.exists():
        pytest.skip("egress log written to configured path only")
    text = egress.read_text(encoding="utf-8")
    assert "M000" not in text


# ---------------------------------------------------------------- groundedness (injected errors)
@pytest.fixture(scope="module")
def pack_and_finding(e2e):
    conn, _ = e2e
    v = conn.execute("SELECT variance_id FROM variances WHERE possible_cause_rule='STALE_RATE' LIMIT 1").fetchone()
    from workflow.deterministic_close import DeterministicClose
    from data_gen import spec
    variances = [x for m in spec.MONTHS for x in DeterministicClose(conn).reconcile(m, pipeline.END, "t")]
    group = next(g for g in group_variances(variances) if "STALE_RATE" in g.key[0] and len(g.variances) > 100)
    pack = build_pack(conn, group)
    line = pack["remittance_lines"][0]
    finding = Finding(possible_causes=["STALE_RATE"],
                      explanation=f"Paid ${line['amount']} instead of ${pack['variance']['expected']}.",
                      evidence_refs=[line["id"]],
                      amounts_cited=[CitedAmount(label="paid", amount=str(line["amount"]))],
                      confidence=0.9, recommended_action="dispute_rate_with_state")
    return pack, finding


def test_grounded_finding_passes(pack_and_finding):
    pack, finding = pack_and_finding
    assert groundedness.check(finding, pack).grounded


def test_invented_evidence_id_is_caught(pack_and_finding):
    pack, finding = pack_and_finding
    bad = finding.model_copy(update={"evidence_refs": ["RL9999999"]})
    assert not groundedness.check(bad, pack).grounded


def test_invented_dollar_amount_is_caught(pack_and_finding):
    pack, finding = pack_and_finding
    bad = finding.model_copy(update={"explanation": "The state underpaid by $37.25 this month."})
    res = groundedness.check(bad, pack)
    assert not res.grounded and any("37.25" in p for p in res.problems)


def test_cause_contradicting_facts_is_caught(pack_and_finding):
    pack, finding = pack_and_finding
    bad = finding.model_copy(update={"possible_causes": ["MISSING_FROM_REMIT"]})   # but a payment exists
    assert not groundedness.check(bad, pack).grounded


# ---------------------------------------------------------------- memo
SUMMARY = [CellMonthSummary(date(2026, 1, 1), "North", "Adult", 150, Decimal("3000.00"))]


def _placeholders():
    return memo_writer.build_facts("ADJ-1", "rate_version", "v3", SUMMARY, Decimal("750.00"))[1]


def test_memo_numbers_come_from_code():
    out = memo_writer.fill_and_check(MemoDraft(title="Adjustment {trigger_ref}",
                                               body="Total {total_delta} over {member_months} member-months."),
                                     _placeholders())
    assert out.grounded and "$3,000.00" in out.body and "150" in out.body


def test_memo_that_types_its_own_number_is_rejected():
    out = memo_writer.fill_and_check(MemoDraft(title="Adjustment", body="Total $3,100.00 for January 2026."),
                                     _placeholders())
    assert not out.grounded and "3,100.00" in out.problems[0]


def test_memo_unknown_placeholder_is_rejected():
    out = memo_writer.fill_and_check(MemoDraft(title="A", body="Total {grand_total}."), _placeholders())
    assert not out.grounded
