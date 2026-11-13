"""Actual read-only PostgreSQL scope inspection with secret-bearing fixtures."""

# ruff: noqa: F401,F811
import json
import os

import pytest
from sqlalchemy import event, select, text, update

from packages.persistence.demo import accounts
from tests.integration.test_demo import system as demo_system
from tests.integration.test_runtime_inventory_worker import ACCOUNT, financial_state, inventory
from tests.unit.test_backup_scope_preflight import WORKER, config


@pytest.fixture
def scope_case(inventory):
    store, _, root = inventory
    storage = root / "storage"
    storage.mkdir()
    shared = storage / "demo-runtime-20261111" / "shared-weight"
    shared.mkdir(parents=True)
    (shared / "PRIVATE_NAME-wal").write_bytes(b"PRIVATE_FILE_BODY")
    with store.engine.begin() as c:
        c.execute(text("CREATE TABLE read_response_evidence_events_v2 (value text)"))
        c.execute(text('CREATE TABLE "PRIVATE_TABLE_NAME" (value text)'))
        c.execute(
            text("INSERT INTO read_response_evidence_events_v2 VALUES ('PRIVATE_EVENT_BODY')")
        )
        c.execute(
            text(
                "CREATE FUNCTION private_secret_function() RETURNS text LANGUAGE sql AS $$ SELECT 'PRIVATE_FUNCTION_BODY'::text $$"
            )
        )
    return store, storage


def collect(case):
    return WORKER["collect"](config(), storage_root=case[1])


def test_catalog_includes_v2_and_unclassified_objects_without_contents_or_writes(scope_case):
    before = financial_state(scope_case[0])
    report, code = collect(scope_case)
    assert code == 0 and report["backup_scope_preflight"] == "READ_ONLY_SCOPE_OBSERVED_KEEP_PAUSED"
    names = {r["name"] for r in report["tables"]}
    assert "read_response_evidence_events_v2" in names and "UNCLASSIFIED" in names
    assert report["catalog"]["functions"] >= 1
    assert report["account_found"] and report["account_paused"]
    assert report["storage"]["files"] == 1
    for flag in WORKER["FALSE_FLAGS"]:
        assert report[flag] is False
    assert "PRIVATE" not in json.dumps(report) and "postgresql" not in json.dumps(report)
    assert financial_state(scope_case[0]) == before


def test_actual_transaction_rejects_attempted_write(scope_case, monkeypatch):
    import sqlalchemy

    engine = scope_case[0].engine
    before = financial_state(scope_case[0])
    fired = []

    def inject(c, cursor, statement, params, context, many):
        if "SELECT jsonb_typeof" in statement and not fired:
            fired.append(True)
            c.execute(
                text("UPDATE demo_accounts SET fence=fence+1 WHERE account_id=:account"),
                dict(account=ACCOUNT),
            )

    event.listen(engine, "before_cursor_execute", inject)
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *a, **kw: engine)
    try:
        report, code = collect(scope_case)
    finally:
        event.remove(engine, "before_cursor_execute", inject)
    assert fired and code == 2 and report["sqlstate"] == "25006"
    assert financial_state(scope_case[0]) == before


@pytest.mark.parametrize(
    "kind",
    [
        "no-database",
        "private-url",
        "account-missing",
        "account-not-paused",
        "invalid-paused",
        "object-limit",
        "storage-link",
    ],
)
def test_failures_are_bounded_private_and_do_not_change_financial_state(
    scope_case, monkeypatch, kind
):
    store, storage = scope_case
    if kind == "no-database":
        monkeypatch.delenv("DATABASE_URL")
    elif kind == "private-url":
        monkeypatch.setenv("DATABASE_URL", "PRIVATE_SECRET_URL")
    elif kind == "storage-link":
        (storage / "link").symlink_to("/etc")
    elif kind == "object-limit":
        with store.engine.begin() as c:
            for i in range(130):
                c.execute(text(f"CREATE TABLE extra_{i} (v int)"))
    else:
        with store.engine.begin() as c:
            if kind == "account-missing":
                c.execute(
                    text("DELETE FROM demo_accounts WHERE account_id=:account"),
                    dict(account=ACCOUNT),
                )
            else:
                state = c.execute(
                    select(accounts.c.state).where(accounts.c.account_id == ACCOUNT)
                ).scalar_one()
                state["paused"] = False if kind == "account-not-paused" else "PRIVATE_PAUSED"
                c.execute(
                    update(accounts).where(accounts.c.account_id == ACCOUNT).values(state=state)
                )
    before = financial_state(store)
    report, code = collect(scope_case)
    assert code == 2 and report["backup_scope_preflight"] == "BLOCKED_KEEP_PAUSED"
    assert "PRIVATE" not in json.dumps(report)
    assert financial_state(store) == before


def test_cross_schema_dependencies_are_counted_without_printing_names(scope_case):
    import uuid

    schema = "private_" + uuid.uuid4().hex
    store, _ = scope_case
    with store.engine.begin() as c:
        current = c.execute(text("SELECT current_schema()")).scalar_one()
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
        c.execute(text(f'CREATE TABLE "{schema}".parent (id int PRIMARY KEY)'))
        c.execute(text(f'CREATE TABLE linked (id int REFERENCES "{schema}".parent(id))'))
    try:
        report, code = collect(scope_case)
        assert code == 0 and report["catalog"]["cross_schema_foreign_keys"] == 1
        assert schema not in json.dumps(report) and current not in json.dumps(report)
    finally:
        with store.engine.begin() as c:
            c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
