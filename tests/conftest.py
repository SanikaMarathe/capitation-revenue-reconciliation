from __future__ import annotations

from datetime import date

import pytest

from common.config import get_settings
from privacy.gateway import PrivacyGateway
from privacy.pseudonymise import Pseudonymiser
from privacy.scanner import PromptScanner


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def gateway(tmp_path, settings):
    return PrivacyGateway(
        allow_lists=settings.allow_lists,
        pseudonymiser=Pseudonymiser("test-secret"),
        scanner=PromptScanner(names={"Asha", "Ramirez"}, dobs_iso={date(1990, 4, 12).isoformat()}),
        egress_log=tmp_path / "egress.jsonl",
    )
