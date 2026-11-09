"""Same cancellation/pagination cases, now against the fenced handoff component."""

# ruff: noqa: F811
import json
import os
from pathlib import Path

import pytest

from tests.fixtures.tp_fenced_handoff import handoff_coordinator
from tests.integration import test_tp_cleanup_envelope as checks
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401


@pytest.fixture(autouse=True)
def explicit_handoff_component(monkeypatch):
    created = []

    def create(store, prior, budget):
        c = handoff_coordinator(store, prior, budget)
        created.append(c)
        return c

    monkeypatch.setattr(checks, "staged_coordinator", create)
    yield
    for c in created:
        assert c.handoff_used == 1, c.handoff_diagnostics()
        folder = Path(os.environ["TP_CLEANUP_ENVELOPE_REPORT_DIR"])
        for path in folder.glob("stagedTrue-*.json"):
            data = json.loads(path.read_text())
            if data["result"]["operation_id"] == str(c._own_operation):
                data["fenced_handoff"] = c.handoff_diagnostics()
                path.write_text(json.dumps(data, indent=2) + "\n")


test_handoff_cancellation_and_cost_matrix = (
    checks.test_original_four_attempt_cleanup_cost_without_earlier_traffic
)
