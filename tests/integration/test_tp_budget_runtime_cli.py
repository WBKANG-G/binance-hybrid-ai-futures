"""Real CLI default admission gate: no lease writes, stream or exchange orders."""

# ruff: noqa: F811
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from integrations.binance_usdm.demo_read_routing import TPReadRouting
from scripts import check_demo_tp_trigger as cli
from services.execution.demo_tp_trigger_check import TPTriggerClient
from tests.fixtures.closed_history_probe import setup  # noqa: F401
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_tp_budget_install_guard import rows
from tests.integration.test_tp_rate_install_guard import closed  # noqa: F401


@pytest.mark.parametrize(
    "mode", ["default", "synthetic_env", "wrong_scope", "repeat", "pressure", "missing_header"]
)
def test_canonical_cli_cannot_bypass_default_gate_or_write_lease(setup, monkeypatch, capsys, mode):
    store, old, _, options, fake, start = setup
    source = old.send.route
    closed_calls = []

    class Pool:
        def __call__(self, *args):
            return source(*args)

        def close(self):
            closed_calls.append(True)

        def diagnostics(self):
            return {}

    def route(sender):
        return TPReadRouting(sender, pool=Pool())

    def client(settings, credentials):
        return TPTriggerClient(settings, credentials, send=source, clock_ms=old.clock_ms)

    monkeypatch.setattr(cli, "TPTriggerClient", client)
    monkeypatch.setattr(cli, "transport", source)
    monkeypatch.setattr(cli, "TPReadRouting", route)
    monkeypatch.setattr(cli, "credentials", lambda **kwargs: old.credentials)
    monkeypatch.setattr(cli, "create_engine", lambda *args, **kwargs: store.engine)
    monkeypatch.setattr(cli, "DemoStore", lambda engine: store)
    monkeypatch.setattr(cli, "ACCOUNT", "DEMO-test")
    monkeypatch.setattr(
        store,
        "acquire",
        lambda *args, **kwargs: pytest.fail("No lease write before default admission"),
    )
    monkeypatch.setattr(
        cli,
        "BudgetedTPTriggerCoordinator",
        lambda *args, **kwargs: pytest.fail("No coordinator before default admission"),
    )
    op = next(iter(store.read("DEMO-test").orders)) if mode == "repeat" else str(uuid4())
    args = [
        "check",
        "--enable-demo",
        "--enable-tp-trigger-observation",
        "--operation-id",
        op,
        "--entry-style",
        "OPPOSITE_QUOTE",
        "--enable-opposite-quote-tp-observation",
    ]
    if mode != "wrong_scope":
        args.append("--enable-pooled-read-tp-observation")
    if mode == "synthetic_env":
        monkeypatch.setenv("ALLOW_SYNTHETIC_MODEL_EXECUTION", "true")
        monkeypatch.setenv("DEMO_TRADING_ENABLED", "true")
        monkeypatch.setenv("TP_BUDGET_MODEL_APPROVED", "true")
    if mode == "pressure":
        options["weight"] = 5999
    if mode == "missing_header":
        options["header"] = False
    monkeypatch.setattr(sys, "argv", args)
    before = rows(store)
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["tp_trigger_check"] == "BLOCKED"
    assert not report["orders_enabled"] and not report["live_trading_enabled"]
    budget = report["tp_budget_diagnostic"]
    assert not budget["synthetic_execution_explicit"] and not budget["entry_post_forwarded"]
    if mode in {"default", "synthetic_env"}:
        assert budget["reason"] == "UNPROVEN_MODEL_REAL_EXECUTION_DISABLED"
        assert budget["forwarded_attempts"] == 2
    assert rows(store) == before
    assert all(method == "GET" for method, path, params in fake.calls[start:])
    if mode != "wrong_scope":
        assert closed_calls == [True]
    folder = Path(os.environ.get("TP_BUDGET_INSTALL_REPORT_DIR", "/tmp/tp-budget-install-reports"))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ("cli_" + mode + ".json")).write_text(
        json.dumps(
            {
                "synthetic_only": True,
                "mode": mode,
                "report": report,
                "database_unchanged": True,
                "exchange_mutations": 0,
            },
            indent=2,
        )
        + "\n"
    )
