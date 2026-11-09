"""Natural-time synthetic lifecycle: production observation remains300 seconds."""

# ruff: noqa: F811
import asyncio
import json
import os
import time
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from integrations.binance_usdm.rest import RESTResponse
from scripts.capture_demo_wss_hints import MAX_HINTS, Capture
from services.execution import demo as demo_module
from services.execution import demo_tp_trigger_check as tp_module
from tests.fixtures.tp_candidate_session import observe
from tests.fixtures.tp_first_confirmation import first_coordinator
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401
from tests.integration.test_tp_admission_candidate import scenario


@pytest.mark.parametrize(
    "mode",
    ["quiet_tp", "quiet_delayed_cancel", "quiet_failed_cancel", "pulse_50ms", "pulse_50ms_cap"],
)
def test_natural_time_minute_lifecycle(opposite_case, monkeypatch, capsys, mode):
    duration = 45 if mode == "pulse_50ms_cap" else 20 if mode == "pulse_50ms" else 300
    opposite_case[3]["trigger"] = mode == "quiet_tp"
    s, b, meter, route, old, history = scenario(opposite_case, cap=100000, pages=10)
    store, prior, fake, options = s
    c = first_coordinator(store, prior, b)
    s = (store, c, fake, options)
    if mode.startswith("pulse_"):
        meter.latency = 0.05
    begin_native, confirm_native, sender = c.begin_finalization, c.confirm_flat, b.sender
    snapshots_native = demo_module.historical_snapshot
    pending = set()
    attempts = []
    snapshots = []
    cleanup_start = []
    delayed = []
    count = 0

    def begin():
        cleanup_start.append(len(meter.rows) + 1)
        meter.finalizing = True
        begin_native()

    def confirm():
        nonlocal count
        count += 1
        if count == 4 and mode != "quiet_failed_cancel":
            for identifier in pending:
                fake.algos[identifier]["algoStatus"] = "CANCELED"
        result = confirm_native()
        attempts.append({"attempt": count, "result": result})
        return result

    def historical(*args, **kwargs):
        snapshots.append({"attempt": count, "first_request_seq": len(meter.rows) + 1})
        return snapshots_native(*args, **kwargs)

    def send(method, path, query, key):
        params = parse_qs(query.get_secret_value())
        identifier = params.get("clientAlgoId", [""])[0]
        before = fake.algos.get(identifier, {}).copy()
        response = sender(method, path, query, key)
        if (
            method == "DELETE"
            and path == "/fapi/v1/algoOrder"
            and before.get("orderType") == "STOP_MARKET"
            and before.get("algoStatus") == "NEW"
            and mode != "quiet_tp"
            and (count < 4 or mode == "quiet_failed_cancel")
        ):
            fake.algos[identifier] = before
            pending.add(identifier)
            delayed.append({"attempt": count, "seq": len(meter.rows)})
            if mode == "quiet_failed_cancel":
                meter.rows[-1]["status"] = 500
                return RESTResponse(500, b'{"code":-1007}', response.headers)
        return response

    monkeypatch.setattr(c, "begin_finalization", begin)
    monkeypatch.setattr(c, "confirm_flat", confirm)
    monkeypatch.setattr(demo_module, "historical_snapshot", historical)
    b.sender = send
    before = store.read("DEMO-test")
    capture = Capture()
    started = time.monotonic()
    original_observation = tp_module.OBSERVATION_SECONDS
    try:
        report = asyncio.run(
            observe(
                s,
                monkeypatch,
                profile="pulse" if mode.startswith("pulse_") else "quiet",
                duration=duration,
                capture=capture,
            )
        )
    finally:
        elapsed = time.monotonic() - started
        route.close()
        c.client.send = old
    capsys.readouterr()
    result = {
        "synthetic_only": True,
        "counterfactual_limit": 100000,
        "mode": mode,
        "production_observation_seconds_before_run": original_observation,
        "test_observation_seconds": duration,
        "actual_monotonic_elapsed_seconds": elapsed,
        "clock_accelerated": False,
        "capture_hint_count": len(capture.hints),
        "capture_hint_limit": MAX_HINTS,
        "natural_300_second_test": duration == 300,
        "http_latency_assumption_seconds": meter.latency,
        "cleanup_start_seq": cleanup_start[0],
        "confirmation_attempts": attempts,
        "historical_confirmation_snapshots": snapshots,
        "delayed_delete_acknowledgements": delayed,
        "result": report,
        "budget": b.diagnostics(),
        "handoff": c.handoff_diagnostics(),
        "first_confirmation": c.first_confirmation_diagnostics(),
        "trace": meter.rows,
    }
    folder = Path(os.environ["TP_MINUTE_LIFECYCLE_REPORT_DIR"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (mode + ".json")).write_text(json.dumps(result, indent=2) + "\n")
    assert original_observation == 300 and elapsed >= duration
    assert report["new_entry_post_count"] == 1
    assert not any(fake.positions.values()) and store.read("DEMO-test").paused
    assert report["history_and_baselines_preserved"]
    assert all(store.read("DEMO-test").orders[k] == v for k, v in before.orders.items())
    assert all(r["status"] != 429 for r in meter.rows)
    assert not b.cleanup_overrun
    if mode == "quiet_tp":
        assert report["tp_trigger_check"] == "TP_FILLED_THEN_REMAINING_SL_CANCELED_FLAT_VERIFIED"
        assert report["operator_exit_post_attempts"] == 0 and report["risk_state"] == "NORMAL"
    else:
        assert (
            report["tp_trigger_check"] == "NOT_ACCEPTED"
            and report["operator_exit_post_attempts"] == 1
        )
        assert report["risk_state"] == "EMERGENCY"
    assert report["flat_confirmation"] == (
        "UNKNOWN" if mode in {"quiet_failed_cancel", "pulse_50ms_cap"} else "VERIFIED_FLAT"
    )
    if mode == "quiet_failed_cancel":
        assert [a["result"] for a in attempts] == ["UNKNOWN"] * 4
        assert c.first_pending_deferrals == 0
        assert any(a["algoStatus"] == "NEW" for a in fake.algos.values())

    if mode == "pulse_50ms_cap":
        assert len(capture.hints) == MAX_HINTS
        assert report["failure"] == "DEMO_SESSION_OR_QUERY_FAILED"
        assert report["private_session_diagnostic"]["stage"] == "RECEIVE"
        assert report["private_session_diagnostic"]["code"] == "DEMO_QUERY_OR_SESSION"
        assert attempts[-1]["result"] == "VERIFIED_FLAT"
