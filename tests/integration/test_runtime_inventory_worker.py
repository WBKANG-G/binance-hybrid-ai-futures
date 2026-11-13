"""Standalone worker: real READ ONLY PG, projected account state, no app imports."""

# ruff: noqa: F401,F811
import hashlib
import json
import os
import runpy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import event, text, update
from sqlalchemy.engine import make_url

from packages.persistence.demo import accounts
from tests.integration.test_demo import system as demo_system
from tests.integration.test_runtime_read_accounting import financial
from tests.unit.test_quota_startup_read_only import files

ROOT = Path(__file__).resolve().parents[2]
WORKER = runpy.run_path(
    str(ROOT / "tools/windows_demo_runtime_inventory/worker.py"), run_name="inventory_integration"
)
ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"


@pytest.fixture
def inventory(demo_system, tmp_path, monkeypatch):
    store, coordinator, fake = demo_system
    ledger = store.read("DEMO-test").model_copy(deep=True)
    ledger.paused = True
    ledger.daily_key, ledger.weekly_key = "2026-11-13", "2026-11-09"
    ledger.requests = {
        "PRIVATE_UNKNOWN_ONE": "UNKNOWN",
        "PRIVATE_UNKNOWN_TWO": "UNKNOWN",
        "PRIVATE_UNKNOWN_THREE": "UNKNOWN",
        "PRIVATE_SUBMITTING": "SUBMITTING",
        "PRIVATE_OTHER": "PRIVATE_VALUE",
    }
    store.create(ACCOUNT, ledger)
    with store.engine.connect() as c:
        schema = c.execute(text("SELECT current_schema()")).scalar_one()
    url = make_url(os.environ["TEST_DATABASE_URL"]).update_query_dict(
        {"options": "-csearch_path=" + schema}
    )
    monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
    (tmp_path / "packages").mkdir()
    (tmp_path / "packages/test.py").write_bytes(b"synthetic source")
    config = dict(
        source_reference="a" * 40,
        baseline_reference="b" * 40,
        account=ACCOUNT,
        expected_day_kst="2026-11-13",
        baseline_files={"packages/test.py": hashlib.sha256(b"synthetic source").hexdigest()},
        candidate_files={"services/future.py": "c" * 64},
    )
    return store, config, tmp_path


def projection(case):
    return WORKER["collect"](case[1], root=case[2], now=datetime(2026, 11, 13, tzinfo=UTC))


def financial_state(store):
    from sqlalchemy import select

    from packages.persistence.demo import events, outbox

    with store.engine.connect() as c:
        return [
            c.execute(select(t).order_by(*t.primary_key.columns)).all()
            for t in (accounts, events, outbox)
        ]


def test_projected_inventory_does_not_change_source_or_financial_state(inventory):
    before, disk = financial_state(inventory[0]), files(inventory[2])
    report, status = projection(inventory)
    assert status == 0 and report["runtime_inventory"] == "READ_ONLY_INVENTORY_COMPLETE_KEEP_PAUSED"
    assert report["paused"] and report["period_keys_match"] and report["risk_state"] == "NORMAL"
    assert report["baseline_files_verified"] == 1 and report["candidate_files_matching"] == 0
    assert report["changed_or_missing_candidate_files"] == ["services/future.py"]
    assert report["request_state_counts"]["UNKNOWN"] == 3
    assert (
        report["request_state_counts"]["SUBMITTING"] == 1
        and report["request_state_counts"]["OTHER"] == 1
    )
    assert not report["candidate_table_names_present"]
    assert "PRIVATE_" not in json.dumps(report)
    assert financial_state(inventory[0]) == before and files(inventory[2]) == disk


def test_read_only_transaction_actually_rejects_a_write(inventory, monkeypatch):
    import sqlalchemy

    engine = inventory[0].engine
    fired = []

    def write(c, cursor, statement, params, context, many):
        if "SELECT state->>'paused'" in statement and not fired:
            fired.append(True)
            c.execute(
                text("UPDATE demo_accounts SET fence=fence+1 WHERE account_id=:account"),
                dict(account=ACCOUNT),
            )

    before = financial_state(inventory[0])
    event.listen(engine, "before_cursor_execute", write)
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *args, **kw: engine)
    try:
        report, status = projection(inventory)
    finally:
        event.remove(engine, "before_cursor_execute", write)
    assert fired and status == 2 and report["sqlstate"] == "25006"
    assert report["failure_category"] == "SOURCE_OR_DATABASE_READ_FAILED"
    assert financial_state(inventory[0]) == before


def test_missing_database_configuration_has_no_fallback(inventory, monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    report, status = projection(inventory)
    assert status == 2 and report["failure_category"] == "DATABASE_NOT_CONFIGURED"
    assert not report["database_configuration_used"]


@pytest.mark.parametrize(
    "kind",
    ["account-missing", "private-risk", "string-paused", "too-many-requests", "period-mismatch"],
)
def test_account_projection_gaps_do_not_leak_values_or_repair_periods(inventory, kind):
    from sqlalchemy import select

    with inventory[0].engine.begin() as c:
        state = c.execute(
            select(accounts.c.state).where(accounts.c.account_id == ACCOUNT)
        ).scalar_one()
        if kind == "account-missing":
            c.execute(
                text("DELETE FROM demo_accounts WHERE account_id=:account"), dict(account=ACCOUNT)
            )
        else:
            if kind == "private-risk":
                state["risk_state"] = "PRIVATE_RISK_VALUE"
            if kind == "string-paused":
                state["paused"] = "true"
            if kind == "too-many-requests":
                state["requests"] = {str(i): "UNKNOWN" for i in range(769)}
            if kind == "period-mismatch":
                state["daily_key"] = "2026-11-12"
            c.execute(update(accounts).where(accounts.c.account_id == ACCOUNT).values(state=state))
    before = financial_state(inventory[0])
    report, status = projection(inventory)
    assert status == (0 if kind == "period-mismatch" else 2)
    if kind == "period-mismatch":
        assert not report["period_keys_match"]
    assert "PRIVATE_" not in json.dumps(report)
    assert financial_state(inventory[0]) == before


def test_plain_postgresql_url_uses_installed_psycopg_driver(inventory, monkeypatch):
    value = make_url(os.environ["DATABASE_URL"]).set(drivername="postgresql")
    monkeypatch.setenv("DATABASE_URL", value.render_as_string(hide_password=False))
    report, status = projection(inventory)
    assert status == 0 and report["account_found"]
