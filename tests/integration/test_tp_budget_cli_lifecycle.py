"""Canonical CLI through a real300s local WSS lifecycle, synthetic HTTP only."""

# ruff: noqa: F811
import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from integrations.binance_usdm.demo_read_routing import TPReadRouting
from integrations.binance_usdm.demo_tp_window_budget import WindowTPBudget
from scripts import check_demo_tp_trigger as cli
from scripts.capture_demo_wss_hints import Capture
from scripts.review_tp_minute_budget import review
from services.execution import demo_tp_trigger_check as module
from services.execution.demo_tp_budget_integration import BudgetedTPTriggerCoordinator
from services.execution.demo_tp_trigger_check import TPTriggerClient
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401
from tests.integration.test_tp_admission_candidate import scenario


def test_canonical_cli_routes_full_tp_lifecycle_through_window_budget(
    opposite_case, monkeypatch, capsys
):
    duration = float(os.environ.get("TP_CLI_DURATION", ".8"))
    s, oldbudget, meter, route, old, history = scenario(opposite_case, cap=6000, pages=10)
    store, prior, fake, options = s
    op = uuid4()
    store.release(prior.lease)
    budget_created = []
    coordinator_created = []

    def synthetic_budget(*args, **kwargs):
        assert kwargs.pop("allow_synthetic_model_execution") is False
        b = WindowTPBudget(*args, **kwargs, allow_synthetic_model_execution=True)
        budget_created.append(b)
        return b

    def coordinator(*args):
        c = BudgetedTPTriggerCoordinator(*args)
        native = c.begin_finalization

        def begin():
            meter.finalizing = True
            native()

        c.begin_finalization = begin
        coordinator_created.append(c)
        return c

    class Pool:
        def __call__(self, *args):
            return meter(*args)

        def close(self):
            pass

        def diagnostics(self):
            return {}

    monkeypatch.setattr(
        cli,
        "TPTriggerClient",
        lambda cfg, key: TPTriggerClient(cfg, key, send=meter, clock_ms=meter.clock_ms),
    )
    monkeypatch.setattr(cli, "transport", meter)
    monkeypatch.setattr(cli, "TPReadRouting", lambda sender: TPReadRouting(sender, pool=Pool()))
    monkeypatch.setattr(cli, "credentials", lambda **kwargs: prior.client.credentials)
    monkeypatch.setattr(cli, "create_engine", lambda *args, **kwargs: store.engine)
    monkeypatch.setattr(cli, "DemoStore", lambda engine: store)
    monkeypatch.setattr(
        cli, "DemoSettings", SimpleNamespace(model_validate=lambda raw: prior.client.settings)
    )
    monkeypatch.setattr(cli, "ACCOUNT", "DEMO-test")
    monkeypatch.setattr(cli, "WindowTPBudget", synthetic_budget)
    monkeypatch.setattr(cli, "BudgetedTPTriggerCoordinator", coordinator)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check",
            "--enable-demo",
            "--enable-tp-trigger-observation",
            "--operation-id",
            str(op),
            "--entry-style",
            "OPPOSITE_QUOTE",
            "--enable-opposite-quote-tp-observation",
            "--enable-pooled-read-tp-observation",
        ],
    )
    if duration != 300:
        monkeypatch.setattr(module, "OBSERVATION_SECONDS", duration)
    else:
        assert module.OBSERVATION_SECONDS == 300

    async def exercise():
        triggered = False

        async def handler(socket):
            nonlocal triggered
            try:
                while socket.close_code is None:
                    state = store.read("DEMO-test")
                    order = state.orders.get(str(op))
                    if (
                        not triggered
                        and order
                        and state.validation_evidence.get(str(op), {}).get("target_verified")
                    ):
                        fake.trigger(order.tp_algo_id)
                        triggered = True
                    await socket.send(json.dumps({"e": "ACCOUNT_UPDATE", "E": meter.clock_ms()}))
                    await asyncio.sleep(15)
            except Exception:
                pass

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            class LocalCapture(Capture):
                async def supervise(self, c, *, on_ready):
                    @asynccontextmanager
                    async def connector(url):
                        async with connect(f"ws://127.0.0.1:{port}") as socket:
                            yield socket

                    return await super().supervise(c, connector=connector, on_ready=on_ready)

            monkeypatch.setattr(cli, "Capture", LocalCapture)
            await asyncio.to_thread(cli.main)

    before = store.read("DEMO-test")
    start = time.monotonic()
    try:
        asyncio.run(exercise())
    finally:
        route.close()
    elapsed = time.monotonic() - start
    lines = capsys.readouterr().out.strip().splitlines()
    report = json.loads(lines[-1])
    raw = {
        "synthetic_only": True,
        "published_limit": 6000,
        "actual_monotonic_elapsed_seconds": elapsed,
        "test_observation_seconds": duration,
        "result": report,
        "trace": meter.rows,
    }
    measured = review(raw)
    folder = Path(os.environ.get("TP_BUDGET_INSTALL_REPORT_DIR", "/tmp/tp-budget-install-reports"))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "canonical_cli_lifecycle.json").write_text(json.dumps(raw, indent=2) + "\n")
    (folder / "canonical_cli_minute.json").write_text(json.dumps(measured, indent=2) + "\n")
    assert len(budget_created) == len(coordinator_created) == 1
    assert report["tp_trigger_check"] == "TP_FILLED_THEN_REMAINING_SL_CANCELED_FLAT_VERIFIED"
    assert report["tp_budget_diagnostic"]["entry_post_forwarded"]
    assert report["new_entry_post_count"] == 1 and report["operator_exit_post_attempts"] == 0
    assert measured["guarded_peak"]["weight"] <= 5700
    assert not any(fake.positions.values())
    after = store.read("DEMO-test")
    assert after.paused and all(after.orders[k] == v for k, v in before.orders.items())
    assert elapsed >= duration
