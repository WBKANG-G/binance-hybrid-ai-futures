"""Reuse exact natural-time assertions with the new uncertain-cancel component."""

# ruff: noqa: F811
import json
import os
from pathlib import Path

import pytest

from tests.fixtures.tp_uncertain_cancel import uncertain_coordinator
from tests.integration import test_tp_minute_lifecycle as checks
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401


@pytest.fixture(autouse=True)
def explicit_uncertain_component(monkeypatch):
    created = []

    def create(store, prior, budget):
        c = uncertain_coordinator(store, prior, budget)
        created.append(c)
        return c

    monkeypatch.setattr(checks, "first_coordinator", create)
    yield
    folder = Path(os.environ["TP_MINUTE_LIFECYCLE_REPORT_DIR"])
    for c in created:
        for path in folder.glob("*.json"):
            data = json.loads(path.read_text())
            if data["result"]["operation_id"] == str(c._own_operation):
                data["uncertain_cancel"] = c.uncertain_cancel_diagnostics()
                data["failed_sdk_cancel_calls"] = sum(c.client.cancel_failures.values())
                if data["mode"] == "quiet_failed_cancel":
                    assert c.uncertain_deferrals == 4
                    assert sum(c.client.cancel_failures.values()) == 1
                    assert data["result"]["sl_delete_attempts"] == 1
                path.write_text(json.dumps(data, indent=2) + "\n")


test_uncertain_natural_lifecycle = checks.test_natural_time_minute_lifecycle
