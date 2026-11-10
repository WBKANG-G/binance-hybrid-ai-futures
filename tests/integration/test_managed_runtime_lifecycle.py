"""Use the assembly owner, original WSS session and synthetic exchange end to end."""

# ruff: noqa: F811
import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from integrations.binance_usdm.demo import DemoClient
from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo_shared_weight import SharedWeightJournal
from scripts.capture_demo_wss_hints import Capture
from scripts.review_tp_minute_budget import review
from services.execution.demo_managed_order_quota import ManagedQuotaBridge
from services.execution.demo_managed_runtime import (
    RuntimeClocks,
    RuntimeStorage,
    assemble_managed_runtime,
)
from tests.fixtures import tp_candidate_session
from tests.fixtures.tp_candidate_session import observe
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401
from tests.integration.test_tp_admission_candidate import scenario


@pytest.mark.parametrize("mode", ["tp", "partial"])
def test_assembled_lifecycle(opposite_case, monkeypatch, capsys, tmp_path, mode):
    opposite_case[3]["trigger"] = mode == "tp"
    if mode == "partial":
        opposite_case[3]["fill"] = False
    s, oldbudget, meter, route, old, history = scenario(
        opposite_case, cap=6000, pages=10, partial=mode == "partial"
    )
    store, prior, fake, options = s
    store.change(
        prior.lease, "TEST_ASSEMBLY_PAUSED", lambda ledger: setattr(ledger, "paused", True)
    )
    order_dir = tmp_path / "orders"
    order_dir.mkdir(mode=0o700)
    pressure_dir = tmp_path / "pressure"
    pressure_dir.mkdir(mode=0o700)
    q = ManagedQuotaBridge.bootstrap(store, prior.lease, order_dir)
    q.close()
    offset = [0.0]
    clocks = RuntimeClocks(
        lambda: time.monotonic() + offset[0],
        lambda: datetime.now(UTC) + timedelta(seconds=offset[0]),
    )
    identity = uuid4()
    SharedWeightJournal.create(
        pressure_dir,
        "DEMO-assembly",
        identity,
        monotonic=clocks.monotonic,
        wall_clock=clocks.wall_clock,
    )
    offset[0] = 70.01
    sender = oldbudget.sender

    def send(method, path, query, key):
        response = sender(method, path, query, key)
        if path == "/fapi/v1/exchangeInfo":
            body = json.loads(response.body)
            body["rateLimits"] += [
                {"rateLimitType": "ORDERS", "interval": "SECOND", "intervalNum": 10, "limit": 300},
                {"rateLimitType": "ORDERS", "interval": "MINUTE", "intervalNum": 1, "limit": 1200},
            ]
            return RESTResponse(response.status, json.dumps(body).encode(), response.headers)
        return response

    source = DemoClient(
        prior.client.settings.model_copy(update={"demo_trading_enabled": False}),
        prior.client.credentials,
        send=send,
        clock_ms=prior.client.clock_ms,
    )
    source.offset, source.synced_at = prior.client.offset, prior.client.synced_at
    scope = assemble_managed_runtime(
        store,
        prior.lease,
        source,
        send,
        RuntimeStorage(order_dir, pressure_dir, "DEMO-assembly", identity),
        allow_synthetic_model_execution=True,
        clocks=clocks,
    )
    offset[0] += 70.01
    c = scope.coordinator
    native = c.begin_finalization

    def finalize():
        meter.finalizing = True
        native()

    c.begin_finalization = finalize

    async def owned_run(coordinator, operation):
        assert coordinator is c
        return await scope.run(operation)

    monkeypatch.setattr(tp_candidate_session, "run_admission_candidate", owned_run)
    before = store.read("DEMO-test")
    try:
        result = asyncio.run(
            observe(
                (store, c, fake, options),
                monkeypatch,
                profile="quiet",
                duration=0.8,
                capture=Capture(),
            )
        )
    finally:
        scope.close()
        route.close()
    capsys.readouterr()
    after = store.read("DEMO-test")
    assert result["new_entry_post_count"] == 1 and result["flat_confirmation"] == "VERIFIED_FLAT"
    assert result["operator_exit_post_attempts"] == (0 if mode == "tp" else 1)
    assert result["history_and_baselines_preserved"] and after.paused
    assert all(after.orders[k] == v for k, v in before.orders.items())
    assert not any(fake.positions.values()) and not source.settings.demo_trading_enabled
    assert not c.client.settings.demo_trading_enabled and scope.budget.revoked
    raw = dict(
        synthetic_only=True,
        mode=mode,
        observation_seconds=0.8,
        startup_holds_accelerated=True,
        result=result,
        trace=meter.rows,
        closed_runtime=True,
    )
    measured = review(raw)
    assert measured["guarded_peak"]["weight"] <= 5700
    output = Path(os.environ.get("RUNTIME_ASSEMBLY_REPORT_DIR", "/tmp/runtime-assembly-lifecycle"))
    output.mkdir(parents=True, exist_ok=True)
    (output / (mode + ".json")).write_text(json.dumps(raw, indent=2) + "\n")
    (output / (mode + "-minute.json")).write_text(json.dumps(measured, indent=2) + "\n")
