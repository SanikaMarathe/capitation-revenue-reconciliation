from __future__ import annotations

import json

import pytest

from privacy.pseudonymise import Pseudonymiser
from privacy.scanner import PrivacyViolation

OK_PAYLOAD = {
    "variance": {"member_id": "M000123", "coverage_month": "2026-03-01", "expected": "250.00", "remitted": "200.00"},
    "features": {"cell_match": True},
}


def test_member_ids_are_pseudonymised(gateway):
    text = gateway.prepare("investigator", OK_PAYLOAD)
    assert "M000123" not in text
    assert "MBR-" in text


def test_tokens_are_stable_and_reversible():
    p = Pseudonymiser("secret")
    assert p.token("M000123") == p.token("M000123")
    assert p.token("M000123") != p.token("M000124")
    assert p.reidentify_text(f"see {p.token('M000123')}") == "see M000123"


def test_different_secret_gives_different_token():
    assert Pseudonymiser("a").token("M000123") != Pseudonymiser("b").token("M000123")


def test_field_outside_allow_list_is_rejected(gateway):
    with pytest.raises(PrivacyViolation, match="not allowed"):
        gateway.prepare("investigator", {**OK_PAYLOAD, "member_profile": {}})


def test_extractor_cannot_send_member_data(gateway):
    with pytest.raises(PrivacyViolation):
        gateway.prepare("rate_extractor", {"variance": OK_PAYLOAD["variance"]})


@pytest.mark.parametrize(
    "leak",
    [
        {"note": "member Asha called about this"},          # a known name
        {"note": "RAMIREZ disputes the payment"},           # case-insensitive
        {"note": "born 1990-04-12"},                        # a known DOB, ISO
        {"note": "born 04/12/1990"},                        # a known DOB, US format
        {"first_name": "x"},                                # forbidden field name
    ],
)
def test_phi_in_payload_is_blocked(gateway, leak):
    payload = {"variance": {**OK_PAYLOAD["variance"], **leak}}
    with pytest.raises(PrivacyViolation, match="blocked"):
        gateway.prepare("investigator", payload)


def test_block_message_does_not_echo_the_phi(gateway):
    with pytest.raises(PrivacyViolation) as exc:
        gateway.prepare("investigator", {"variance": {"note": "Asha"}})
    assert "Asha" not in str(exc.value)


def test_egress_log_holds_only_tokenised_payloads(gateway, tmp_path):
    gateway.prepare("investigator", OK_PAYLOAD)
    lines = (tmp_path / "egress.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["agent"] == "investigator"
    assert "M000123" not in record["payload"]


def test_blocked_call_is_not_logged_as_sent(gateway, tmp_path):
    with pytest.raises(PrivacyViolation):
        gateway.prepare("investigator", {"variance": {"note": "Asha"}})
    assert not (tmp_path / "egress.jsonl").exists()
