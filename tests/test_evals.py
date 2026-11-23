"""The eval suite itself runs and the deterministic numbers hold (mock LLM, no cache writes)."""

from __future__ import annotations

import pytest

from data_gen.generate import generate
from evals.run_evals import evaluate


@pytest.fixture(scope="module")
def results():
    generate()
    return evaluate("mock", "off")


def test_detection_and_retro_are_exact(results):
    assert results["detection"]["recall_pct"] == 100.0
    assert results["detection"]["false_positives"] == 0
    assert results["retro"]["exact_pct"] == 100.0
    assert results["retro"]["retro_total_engine"] == results["retro"]["retro_total_truth"]


def test_bank_cases_all_correct(results):
    assert results["bank"]["correct"] == results["bank"]["total"]


def test_guardrails_catch_everything_they_are_designed_for(results):
    assert results["adversarial"]["designed_catch_rate_pct"] == 100.0
    swap = results["adversarial"]["extraction"]["two cells swapped WITH their references"]
    assert swap["expected_to_catch"] is False      # documented limitation: the approver's diff review


def test_privacy_no_leaks_and_all_injections_blocked(results):
    assert results["privacy"]["total_leaks"] == 0
    assert results["privacy"]["injection_test"]["blocked_pct"] == 100.0


def test_rules_resolve_single_cause_and_route_multi_cause(results):
    ro = results["classification"]["rules_only"]
    assert ro["single_cause"]["correct_pct"] == 100.0
    assert ro["unresolved"] > 0                    # multi-cause cases go to the LLM
