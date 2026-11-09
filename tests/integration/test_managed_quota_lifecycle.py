"""Managed journal + original TP lifecycle; all HTTP/positions are synthetic."""

# ruff: noqa: F811
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo_order_quota import RESTART_HOLD_SECONDS
from scripts.capture_demo_wss_hints import Capture
from scripts.review_tp_minute_budget import review
from services.execution.demo_managed_order_quota import (
    RESERVE_PREFIX,
    SEAL_PREFIX,
    ManagedOrderWindowBudget,
    ManagedQuotaBridge,
    ManagedQuotaTPTriggerCoordinator,
    run_managed_quota_candidate,
)
from services.execution.demo_tp_uncertain_cancel_candidate import UncertainCancellationClient
from tests.fixtures import tp_candidate_session
from tests.fixtures.tp_candidate_session import observe
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401
from tests.integration.test_tp_admission_candidate import scenario
from tests.unit.test_durable_order_quota import QuotaClock


@pytest.mark.parametrize("mode", ["tp", "exit", "partial", "sl_journal_failure", "cancel_failure"])
def test_managed_lifecycle(opposite_case, monkeypatch, capsys, tmp_path, mode):
    duration = float(os.environ.get("MANAGED_QUOTA_DURATION", ".8"))
    natural = duration == 300
    opposite_case[3]["trigger"] = mode == "tp"
    if mode == "partial":
        opposite_case[3]["fill"] = False
    s, oldbudget, meter, route, old, history = scenario(
        opposite_case, cap=6000, pages=10, partial=mode == "partial"
    )
    store, prior, fake, options = s
    clock = QuotaClock()
    q = ManagedQuotaBridge.bootstrap(
        store,
        prior.lease,
        tmp_path,
        **(
            {"wall_clock": store.now}
            if natural
            else {"monotonic": clock.mono, "wall_clock": clock.utc}
        ),
    )
    bridge = ManagedQuotaBridge(store, prior.lease, q)
    if not natural:
        clock.seconds = RESTART_HOLD_SECONDS
    sender = oldbudget.sender

    def send(method, path, query, key):
        params = parse_qs(query.get_secret_value())
        identifier = params.get("clientAlgoId", [""])[0]
        before = fake.algos.get(identifier, {}).copy()
        response = sender(method, path, query, key)
        if path == "/fapi/v1/exchangeInfo":
            body = json.loads(response.body)
            body["rateLimits"] += [
                {"rateLimitType": "ORDERS", "interval": "SECOND", "intervalNum": 10, "limit": 300},
                {"rateLimitType": "ORDERS", "interval": "MINUTE", "intervalNum": 1, "limit": 1200},
            ]
            response = RESTResponse(response.status, json.dumps(body).encode(), response.headers)
        if (
            mode == "cancel_failure"
            and method == "DELETE"
            and path == "/fapi/v1/algoOrder"
            and before.get("orderType") == "STOP_MARKET"
            and before.get("algoStatus") == "NEW"
        ):
            fake.algos[identifier] = before
            meter.rows[-1]["status"] = 500
            return RESTResponse(500, b'{"code":-1007}', response.headers)
        return response

    b = ManagedOrderWindowBudget(
        send,
        oldbudget.profile,
        oldbudget.costs,
        bridge=bridge,
        allow_synthetic_model_execution=True,
    )
    source = prior.client
    client = UncertainCancellationClient(
        source.settings, source.credentials, send=b, clock_ms=source.clock_ms
    )
    client.offset, client.synced_at = source.offset, source.synced_at
    c = ManagedQuotaTPTriggerCoordinator(store, prior.lease, client, b)
    native = c.begin_finalization

    def begin():
        meter.finalizing = True
        native()

    monkeypatch.setattr(c, "begin_finalization", begin)
    monkeypatch.setattr(
        tp_candidate_session, "run_admission_candidate", run_managed_quota_candidate
    )
    if mode == "sl_journal_failure":
        native_claim = q.claim

        def fail_on_sl(flow, role, identifier):
            if role == "SL":
                q.path.unlink()
            return native_claim(flow, role, identifier)

        monkeypatch.setattr(q, "claim", fail_on_sl)
    before = store.read("DEMO-test")
    try:
        result = asyncio.run(
            observe(
                (store, c, fake, options),
                monkeypatch,
                profile="quiet",
                duration=duration,
                capture=Capture(),
            )
        )
    finally:
        route.close()
    capsys.readouterr()
    after = store.read("DEMO-test")
    raw = {
        "synthetic_only": True,
        "mode": mode,
        "observation_seconds": duration,
        "quota_warmup_accelerated": not natural,
        "result": result,
        "quota": bridge.diagnostics(),
        "trace": meter.rows,
    }
    measured = review(raw)
    folder = Path(os.environ.get("MANAGED_QUOTA_REPORT_DIR", "/tmp/managed-quota-lifecycle"))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (mode + ".json")).write_text(json.dumps(raw, indent=2) + "\n")
    (folder / (mode + "-minute.json")).write_text(json.dumps(measured, indent=2) + "\n")
    assert result["new_entry_post_count"] == 1
    assert not any(fake.positions.values()) and after.paused
    assert result["history_and_baselines_preserved"]
    assert all(after.orders[k] == v for k, v in before.orders.items())
    assert measured["guarded_peak"]["weight"] <= 5700
    if mode == "tp":
        assert (
            result["flat_confirmation"] == "VERIFIED_FLAT"
            and result["operator_exit_post_attempts"] == 0
        )
    if mode in {"exit", "partial"}:
        assert (
            result["flat_confirmation"] == "VERIFIED_FLAT"
            and result["operator_exit_post_attempts"] == 1
        )
    if mode == "sl_journal_failure":
        assert bridge.exit_fallbacks == 1 and result["operator_exit_post_attempts"] == 1
        assert (
            result["tp_trigger_check"] == "NOT_ACCEPTED"
            and result["flat_confirmation"] == "UNKNOWN"
        )
    if mode == "cancel_failure":
        assert result["flat_confirmation"] == "UNKNOWN"
    if mode in {"tp", "exit", "partial"}:
        flows = q.flow_records()
        assert len(flows) == 1
        flow, state = next(iter(flows.items()))
        assert (
            state["seal"] is not None
            and SEAL_PREFIX + flow in after.operations
            and RESERVE_PREFIX + flow in after.operations
        )
        assert bridge.diagnostics()["slots_60s"] == 6
    q.close()
