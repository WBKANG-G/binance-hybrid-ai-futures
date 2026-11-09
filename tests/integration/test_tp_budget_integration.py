"""Actual6000 synthetic gate, original proof and real-time observations."""

# ruff: noqa: F811
import asyncio
import json
import os
import time
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from integrations.binance_usdm.demo_tp_window_budget import WindowTPBudget
from integrations.binance_usdm.rest import RESTResponse
from scripts.capture_demo_wss_hints import Capture
from scripts.review_tp_minute_budget import review
from services.execution import demo as demo_module
from services.execution.demo_tp_budget_integration import BudgetedTPTriggerCoordinator
from services.execution.demo_tp_uncertain_cancel_candidate import UncertainCancellationClient
from tests.fixtures.tp_candidate_session import observe
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401
from tests.integration.test_tp_admission_candidate import scenario


@pytest.mark.parametrize("mode", ["tp", "delayed", "failed", "recovery", "burst", "partial"])
def test_integrated_6000(opposite_case, monkeypatch, capsys, mode):
    duration = float(os.environ.get("TP_BUDGET_DURATION", ".8"))
    opposite_case[3]["trigger"] = mode == "tp"
    if mode == "partial":
        opposite_case[3]["fill"] = False  # Meter injects exactly one partial fill.
    s, oldbudget, meter, route, old, history = scenario(
        opposite_case, cap=6000, pages=10, partial=mode == "partial"
    )
    store, prior, fake, options = s
    b = WindowTPBudget(
        oldbudget.sender, oldbudget.profile, oldbudget.costs, allow_synthetic_model_execution=True
    )
    source = prior.client
    client = UncertainCancellationClient(
        source.settings, source.credentials, send=b, clock_ms=source.clock_ms
    )
    client.offset, client.synced_at = source.offset, source.synced_at
    c = BudgetedTPTriggerCoordinator(store, prior.lease, client, b)
    s = store, c, fake, options
    native_begin, native_confirm, sender = c.begin_finalization, c.confirm_flat, b.sender
    native_historical = demo_module.historical_snapshot
    pending, attempts, snapshots = set(), [], []
    count = 0

    def begin():
        meter.finalizing = True
        native_begin()

    def confirm():
        nonlocal count
        count += 1
        if count >= 4 and mode != "failed":
            for identifier in pending:
                fake.algos[identifier]["algoStatus"] = "CANCELED"
        result = native_confirm()
        attempts.append({"attempt": count, "result": result})
        return result

    def historical(*args, **kwargs):
        snapshots.append({"attempt": count, "seq": len(meter.rows) + 1})
        return native_historical(*args, **kwargs)

    def send(method, path, query, key):
        identifier = parse_qs(query.get_secret_value()).get("clientAlgoId", [""])[0]
        before = fake.algos.get(identifier, {}).copy()
        response = sender(method, path, query, key)
        if (
            method == "DELETE"
            and path == "/fapi/v1/algoOrder"
            and before.get("orderType") == "STOP_MARKET"
            and before.get("algoStatus") == "NEW"
            and mode in {"delayed", "failed", "recovery"}
            and (count < 4 or mode == "failed")
        ):
            fake.algos[identifier] = before
            pending.add(identifier)
            if mode in {"failed", "recovery"}:
                meter.rows[-1]["status"] = 500
                return RESTResponse(500, b'{"code":-1007}', response.headers)
        return response

    monkeypatch.setattr(c, "begin_finalization", begin)
    monkeypatch.setattr(c, "confirm_flat", confirm)
    monkeypatch.setattr(demo_module, "historical_snapshot", historical)
    b.sender = send
    before = store.read("DEMO-test")
    started = time.monotonic()
    try:
        report = asyncio.run(
            observe(
                s,
                monkeypatch,
                profile="burst" if mode == "burst" else "quiet",
                duration=duration,
                capture=Capture(),
            )
        )
    finally:
        route.close()
    capsys.readouterr()
    raw = {
        "synthetic_only": True,
        "mode": mode,
        "test_observation_seconds": duration,
        "actual_monotonic_elapsed_seconds": time.monotonic() - started,
        "published_limit": 6000,
        "result": report,
        "budget": b.diagnostics(),
        "budgeted_flat_confirmation": c.budgeted_flat_confirmation,
        "resource_wait_seconds": c.proof_resource_wait_seconds,
        "confirmation_attempts": attempts,
        "completed_confirmation_attempts": c.confirmation_attempts_completed,
        "historical_snapshots": snapshots,
        "trace": meter.rows,
    }
    analyzed = review(raw)
    folder = Path(os.environ.get("TP_BUDGET_REPORT_DIR", "/tmp/tp-budget-integration"))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (mode + ".json")).write_text(json.dumps(raw, indent=2) + "\n")
    (folder / (mode + "-minute.json")).write_text(json.dumps(analyzed, indent=2) + "\n")
    assert b.peak <= 5700
    assert c.confirmation_attempts_completed <= 4
    assert analyzed["guarded_peak"]["weight"] <= 5700
    assert analyzed["phase_witness_peak"] <= 5700
    assert report["new_entry_post_count"] == 1
    assert not any(fake.positions.values()) and store.read("DEMO-test").paused
    assert all(store.read("DEMO-test").orders[k] == v for k, v in before.orders.items())
    assert all(r["status"] != 429 for r in meter.rows)
    assert report["history_and_baselines_preserved"]
    if mode == "burst" and b.stopped:
        assert report["flat_confirmation"] == "UNKNOWN"
        assert c.budgeted_flat_confirmation == "VERIFIED_FLAT"
    else:
        assert report["flat_confirmation"] == (
            "UNKNOWN" if mode == "failed" else "VERIFIED_FLAT"
        ), raw
    assert report["operator_exit_post_attempts"] == (0 if mode == "tp" else 1)
    if mode != "failed":
        assert len([r for r in snapshots if r["attempt"] == attempts[-1]["attempt"]]) == 5
    if mode in {"failed", "recovery"}:
        assert sum(client.cancel_failures.values()) == 1
    if mode == "failed":
        assert [a["result"] for a in attempts] == ["UNKNOWN"] * 4
        assert any(row["algoStatus"] == "NEW" for row in fake.algos.values())
