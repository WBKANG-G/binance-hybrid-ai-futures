"""Immutable paired install guard: synthetic venue, isolated PG, no writes."""

# ruff: noqa: F811
import importlib.util
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import select

from integrations.binance_usdm import demo_snapshot
from integrations.binance_usdm.demo import DemoRateLimited
from integrations.binance_usdm.demo_read_routing import TPReadRouting
from packages.persistence.demo import accounts, events
from tests.fixtures.closed_history_probe import setup  # noqa: F401
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_tp_rate_install_guard import closed  # noqa: F401

spec = importlib.util.spec_from_file_location("budget_guard", Path("guard_tp_budget_install.py"))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


def rows(store):
    with store.engine.connect() as connection:
        return connection.execute(select(accounts)).mappings().all(), connection.execute(
            select(events)
        ).mappings().all()


def bind(setup, monkeypatch):
    store, old, _, options, fake, _ = setup
    monkeypatch.setattr(g, "ACCOUNT", "DEMO-test")
    monkeypatch.setattr(g, "operational_snapshot", demo_snapshot.operational_snapshot)

    class Pool:
        def __call__(self, *args):
            return old.send.route(*args)

        def close(self):
            pass

        def diagnostics(self):
            return {}

    route = TPReadRouting(old.send.route, pool=Pool())
    monitor = g.Monitor(route)
    client = g.ReadClient(old.settings, old.credentials, send=monitor, clock_ms=old.clock_ms)
    monitor.server_ms = lambda: client.clock_ms() + client.offset
    return store, client, monitor, options, fake


def test_same_pair_pre_post_preserves_entire_database_and_receipt(setup, monkeypatch):
    store, client, monitor, options, fake = bind(setup, monkeypatch)
    before = rows(store)
    pre = g.compare(store, client, monitor, "pre")
    store, client, monitor, options, fake = bind(setup, monkeypatch)
    post = g.compare(
        store,
        client,
        monitor,
        "post",
        pre["account_digest"],
        pre["exchange_digest"],
        pre["pair_signed_reads"],
    )
    assert pre["pair_signed_reads"] == post["pair_signed_reads"] == 26
    assert post["tp_budget_install"] == "POSTCHECKED_BUDGET_CODE_ONLY_KEEP_PAUSED"
    assert post["real_execution_default_disabled"] and post["legacy_tp_cli_bypass_closed"]
    assert not post["leader_acquired"] and not post["database_changed"] and not post["order_writes"]
    assert (
        post["paused"] and post["manual_close_receipt_preserved"] and post["failed_trial_preserved"]
    )
    assert rows(store) == before
    assert all(method == "GET" for method, path, params in fake.calls)
    folder = Path(os.environ.get("TP_BUDGET_INSTALL_REPORT_DIR", "/tmp/tp-budget-install-reports"))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "guard_pair.json").write_text(
        json.dumps(
            {
                "synthetic_only": True,
                "pre": pre,
                "post": post,
                "database_unchanged": True,
                "exchange_mutations": 0,
            },
            indent=2,
        )
        + "\n"
    )


@pytest.mark.parametrize(
    "fault",
    [
        "binding",
        "remote",
        "local",
        "missing_header",
        "pressure",
        "rate",
        "late_algo",
        "during_local",
    ],
)
def test_guard_faults_stop_without_writes_or_recovery(setup, monkeypatch, fault):
    store, client, monitor, options, fake = bind(setup, monkeypatch)
    pre = g.compare(store, client, monitor, "pre")
    before = rows(store)
    store, client, monitor, options, fake = bind(setup, monkeypatch)
    account, remote, reads = pre["account_digest"], pre["exchange_digest"], pre["pair_signed_reads"]
    if fault == "binding":
        reads = None
    elif fault == "remote":
        remote = "f" * 64
    elif fault == "local":
        account = "f" * 64
    elif fault == "missing_header":
        options["header"] = False
    elif fault == "pressure":
        options["weight"] = 5999
    elif fault == "rate":
        options["rate_at"] = "/fapi/v1/time"
    elif fault == "late_algo":
        options["late_open_algo"] = True
    else:
        options["change_local"] = True
    # Pre consumed two account reads; the fixture injects these faults on the
    # third read, inside this postcheck rather than before its local binding.
    with pytest.raises((g.Blocked, g.ProbeStop, DemoRateLimited)):
        g.compare(store, client, monitor, "post", account, remote, reads)
    if fault != "during_local":
        assert rows(store) == before
    assert all(method == "GET" for method, path, params in fake.calls)
