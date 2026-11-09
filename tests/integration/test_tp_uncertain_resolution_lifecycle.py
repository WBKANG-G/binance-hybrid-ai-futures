"""Natural300-second error-then-terminal path; retain the original full success proof."""

# ruff: noqa: F811
import json
import os
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from integrations.binance_usdm.rest import RESTResponse
from tests.fixtures.tp_uncertain_cancel import uncertain_coordinator
from tests.integration import test_tp_minute_lifecycle as checks
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401


@pytest.fixture(autouse=True)
def uncertain_delete_response(monkeypatch):
    created = []

    def create(store, prior, budget):
        c = uncertain_coordinator(store, prior, budget)
        created.append(c)
        sender = budget.sender
        meter = sender.original

        def send(method, path, query, key):
            response = sender(method, path, query, key)
            params = parse_qs(query.get_secret_value())
            if (
                method == "DELETE"
                and path == "/fapi/v1/algoOrder"
                and params.get("clientAlgoId", [""])[0].startswith("DS-")
            ):
                # The unchanged outer lifecycle fixture delays the terminal
                # status until attempt4. This inner response remains uncertain.
                # A successful ACK is never manufactured from this response.
                meter.rows[-1]["status"] = 500
                return RESTResponse(500, b'{"code":-1007}', response.headers)
            return response

        budget.sender = send
        return c

    monkeypatch.setattr(checks, "first_coordinator", create)
    yield
    folder = Path(os.environ["TP_MINUTE_LIFECYCLE_REPORT_DIR"])
    for c in created:
        path = folder / "quiet_delayed_cancel.json"
        data = json.loads(path.read_text())
        assert c.uncertain_deferrals == 3 and sum(c.client.cancel_failures.values()) == 1
        assert data["result"]["flat_confirmation"] == "VERIFIED_FLAT"
        data["scenario"] = "quiet_uncertain_resolves"
        data["uncertain_cancel"] = c.uncertain_cancel_diagnostics()
        # The outer fixture's original trace recorder sees the500 response.
        data["failed_sdk_cancel_calls"] = sum(c.client.cancel_failures.values())
        (folder / "quiet_uncertain_resolves.json").write_text(json.dumps(data, indent=2) + "\n")
        path.unlink()


test_uncertain_resolution_natural = checks.test_natural_time_minute_lifecycle
