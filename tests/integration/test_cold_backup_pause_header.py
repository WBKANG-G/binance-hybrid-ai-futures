"""Actual READ ONLY pause-header query; no private state or credentials in report."""

# ruff: noqa: F401,F811
import json
import runpy
from pathlib import Path

import pytest
from sqlalchemy import event, text

from tests.integration.test_demo import system as demo_system
from tests.integration.test_runtime_inventory_worker import financial_state, inventory

ROOT = Path(__file__).resolve().parents[2]
WORKER = runpy.run_path(
    str(ROOT / "tools/windows_demo_cold_backup/account_header.py"), run_name="pause_header_test"
)


def test_read_only_header_observes_pause_and_unresolved_count_without_changes(inventory):
    before = financial_state(inventory[0])
    report = WORKER["collect"]()
    assert report == dict(
        status="PAUSED_NO_ACTIVE_LEASE",
        paused=True,
        active_lease=False,
        orders=0,
        unresolved_requests=4,
        database_changed=False,
    )
    assert financial_state(inventory[0]) == before
    assert "PRIVATE" not in json.dumps(report)


@pytest.mark.parametrize(
    "kind", ["active-lease", "not-paused", "wrong-paused-type", "missing-account", "bad-env"]
)
def test_header_refuses_unsafe_or_missing_state_without_echo(inventory, monkeypatch, kind):
    store = inventory[0]
    with store.engine.begin() as c:
        if kind == "active-lease":
            c.execute(
                text(
                    "UPDATE demo_accounts SET lease_until=now()+interval '1 hour' WHERE account_id='DEMO-WINDOWS-ISOLATED-BTC-V1'"
                )
            )
        elif kind in {"not-paused", "wrong-paused-type"}:
            value = "false" if kind == "not-paused" else '"PRIVATE_VALUE"'
            c.execute(
                text(
                    "UPDATE demo_accounts SET state=jsonb_set(state,'{paused}',CAST(:value AS jsonb)) WHERE account_id='DEMO-WINDOWS-ISOLATED-BTC-V1'"
                ),
                dict(value=value),
            )
        elif kind == "missing-account":
            c.execute(
                text("DELETE FROM demo_accounts WHERE account_id='DEMO-WINDOWS-ISOLATED-BTC-V1'")
            )
    if kind == "bad-env":
        monkeypatch.setenv("DATABASE_URL", "PRIVATE_URL_PASSWORD")
    before = financial_state(store)
    report = WORKER["collect"]()
    assert report["status"] == "BLOCKED" and not report["database_changed"]
    assert "PRIVATE" not in json.dumps(report)
    assert financial_state(store) == before


def test_actual_read_only_transaction_denies_injected_write(inventory, monkeypatch):
    import sqlalchemy

    engine = inventory[0].engine
    before = financial_state(inventory[0])
    fired = []

    def inject(c, cursor, statement, params, context, many):
        if "SELECT jsonb_typeof(state" in statement and not fired:
            fired.append(True)
            c.execute(text("UPDATE demo_accounts SET fence=fence+1"))

    event.listen(engine, "before_cursor_execute", inject)
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *a, **kw: engine)
    try:
        report = WORKER["collect"]()
    finally:
        event.remove(engine, "before_cursor_execute", inject)
    assert fired and report["status"] == "BLOCKED"
    assert financial_state(inventory[0]) == before
