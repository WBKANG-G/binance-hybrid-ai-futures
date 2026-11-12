"""Atomic first audit attachment without account/journal/checkpoint changes."""

# ruff: noqa: F811
import json
from uuid import uuid4

import pytest
from sqlalchemy import event, inspect, select, text, update

from packages.persistence.demo import accounts, events, outbox
from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_read_audit import ReadAuditStore, read_bindings, read_events
from packages.persistence.demo_shared_checkpoint import checkpoints
from packages.persistence.demo_shared_weight import POLICY
from scripts import review_demo_read_journal as cli
from services.execution.demo_read_audit_bootstrap import prepare_read_audit
from services.execution.demo_runtime_storage import RUNTIME_STORAGE_KEY, RuntimeClocks
from services.risk.paper import period_keys
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_shared_weight_checkpoint import anchored  # noqa: F401
from tests.unit.test_quota_startup_read_only import files


@pytest.fixture
def ready(anchored):
    a = anchored
    store, coordinator = a[6:8]
    installation = a[1].installation_id()

    def configure(ledger):
        ledger.paused = True
        ledger.daily_key, ledger.weekly_key = period_keys(a[2].utc(), ledger.policy.week_start_kst)
        ledger.operations[RUNTIME_STORAGE_KEY] = json.dumps(
            dict(
                version=1,
                scope=a[0].scope,
                journal=str(a[4]),
                checkpoint_installation=str(installation),
                policy=POLICY,
            )
        )

    store.change(coordinator.lease, "TEST_CONFIGURE", configure)
    store.release(coordinator.lease)
    return a


def call(a, **kwargs):
    return prepare_read_audit(
        a[1].engine,
        "DEMO-test",
        a[3],
        a[0].scope,
        a[4],
        a[1].installation_id(),
        clocks=RuntimeClocks(a[2].mono, a[2].utc, lambda: "test-boot"),
        **kwargs,
    )


def state(a):
    with a[1].engine.connect() as c:
        return (
            files(a[3]),
            c.execute(select(accounts)).all(),
            c.execute(select(events).order_by(events.c.event_id)).all(),
            c.execute(select(outbox).order_by(outbox.c.event_id)).all(),
            c.execute(select(checkpoints).order_by(checkpoints.c.revision)).all(),
        )


def test_plan_does_not_create_tables_or_change_existing_state(ready):
    before = state(ready)
    tables = inspect(ready[1].engine).get_table_names()
    result = call(ready)
    assert result["audit_schema_required"] and result["audit_binding_required"]
    assert not result["audit_schema_created"] and not result["audit_binding_created"]
    assert state(ready) == before and inspect(ready[1].engine).get_table_names() == tables


def test_apply_creates_only_auxiliary_schema_and_binding_then_is_idempotent(ready):
    before = state(ready)
    result = call(ready, apply=True, readers_stopped=True)
    assert result["audit_schema_created"] and result["audit_binding_created"]
    assert not result["financial_ledger_changed"] and not result["resource_checkpoint_changed"]
    assert state(ready) == before
    with ready[1].engine.connect() as c:
        root = c.execute(select(read_bindings)).all()
        assert len(root) == 1 and not c.execute(select(read_events)).all()
    result = call(ready, apply=True, readers_stopped=True)
    assert not result["audit_schema_created"] and not result["audit_binding_created"]
    with ready[1].engine.connect() as c:
        assert c.execute(select(read_bindings)).all() == root
    assert state(ready) == before


def test_existing_unbound_schema_is_bound_without_recreating_it(ready):
    ReadAuditStore(ready[1].engine).provision(apply=True)
    result = call(ready, apply=True, readers_stopped=True)
    assert not result["audit_schema_created"] and result["audit_binding_created"]


def test_missing_reader_stop_confirmation_precedes_any_access():
    with pytest.raises(LocalQuotaStopped, match="READERS_STOPPED_REQUIRED"):
        prepare_read_audit(None, None, None, None, None, None, apply=True)


@pytest.mark.parametrize("kind", ["owner", "unpaused", "period", "binding", "positions"])
def test_account_guard_refuses_before_schema_change(ready, kind):
    a = ready
    with a[1].engine.begin() as c:
        row = c.execute(select(accounts)).mappings().one()
        ledger = row["state"]
        if kind == "owner":
            c.execute(update(accounts).values(owner=str(uuid4())))
        else:
            if kind == "unpaused":
                ledger["paused"] = False
            elif kind == "period":
                ledger["daily_key"] = "2026-11-08"
            elif kind == "binding":
                ledger["operations"][RUNTIME_STORAGE_KEY] = "{}"
            else:
                ledger["positions"] = {"BTCUSDT": "1"}
            c.execute(update(accounts).values(state=ledger))
    before = state(a)
    with pytest.raises((LocalQuotaStopped, ValueError)):
        call(a, apply=True, readers_stopped=True)
    assert state(a) == before
    assert "demo_read_audit_bindings" not in inspect(a[1].engine).get_table_names()


def test_used_unaudited_journal_is_refused_even_when_all_requests_completed(ready):
    token = ready[0].reserve(5, "ORDINARY")
    ready[0].finish(token, used_weight=5, status=200)
    before = state(ready)
    with pytest.raises(LocalQuotaStopped, match="USED_STORE_NOT_ADOPTED"):
        call(ready, apply=True, readers_stopped=True)
    assert state(ready) == before
    assert "demo_read_audit_bindings" not in inspect(ready[1].engine).get_table_names()


def test_schema_and_binding_are_rolled_back_together_when_binding_fails(ready, monkeypatch):
    before = state(ready)

    def fail(*args):
        raise RuntimeError("synthetic bind failure")

    monkeypatch.setattr(ReadAuditStore, "_bind_empty_on", fail)
    with pytest.raises(RuntimeError, match="synthetic"):
        call(ready, apply=True, readers_stopped=True)
    assert state(ready) == before
    assert "demo_read_audit_bindings" not in inspect(ready[1].engine).get_table_names()


def test_partial_schema_is_refused_without_repair(ready):
    with ready[1].engine.begin() as c:
        read_bindings.create(c)
    before = state(ready)
    with pytest.raises(LocalQuotaStopped, match="PARTIAL_SCHEMA"):
        call(ready, apply=True, readers_stopped=True)
    assert state(ready) == before
    assert "demo_read_audit_events" not in inspect(ready[1].engine).get_table_names()


def test_connection_failure_on_commit_does_not_report_success(ready):
    engine = ready[1].engine

    def fail(c):
        if c.execute(text("SELECT to_regclass('demo_read_audit_bindings')")).scalar() is not None:
            raise RuntimeError("synthetic commit failure")

    event.listen(engine, "commit", fail)
    try:
        with pytest.raises(RuntimeError, match="synthetic commit"):
            call(ready, apply=True, readers_stopped=True)
    finally:
        event.remove(engine, "commit", fail)
    assert "demo_read_audit_bindings" not in inspect(engine).get_table_names()


def test_cli_prepare_and_apply(ready, monkeypatch):
    a = ready
    monkeypatch.setattr(cli, "create_engine", lambda *args, **kw: a[1].engine)
    argv = [
        "prepare-audit",
        "--directory",
        str(a[3]),
        "--scope",
        a[0].scope,
        "--journal",
        str(a[4]),
        "--installation",
        str(a[1].installation_id()),
        "--account",
        "DEMO-test",
    ]
    clock = RuntimeClocks(a[2].mono, a[2].utc, lambda: "test-boot")
    code, report = cli.run(argv, clocks=clock)
    assert code == 0 and report["read_audit_setup"] == "PLAN_READY_KEEP_PAUSED"
    assert report["account_state_checked"]
    code, report = cli.run(argv + ["--apply", "--readers-stopped"], clocks=clock)
    assert code == 0 and report["audit_binding_created"] and not report["orders_enabled"]


@pytest.mark.parametrize("fault", ["disabled", "removed", "function_changed"])
def test_existing_schema_with_weakened_immutability_is_not_adopted(ready, fault):
    ReadAuditStore(ready[1].engine).provision(apply=True)
    with ready[1].engine.begin() as c:
        sql = {
            "disabled": "ALTER TABLE demo_read_audit_events DISABLE TRIGGER demo_read_audit_immutable",
            "removed": "DROP TRIGGER demo_read_audit_immutable ON demo_read_audit_events",
            "function_changed": "CREATE OR REPLACE FUNCTION demo_read_audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
        }[fault]
        c.execute(text(sql))
    before = state(ready)
    with pytest.raises(LocalQuotaStopped, match="IMMUTABILITY_SCHEMA_INVALID"):
        call(ready, apply=True, readers_stopped=True)
    assert state(ready) == before
    with ready[1].engine.connect() as c:
        assert not c.execute(select(read_bindings)).all()


def test_account_row_cannot_be_changed_during_schema_and_binding_transaction(ready, monkeypatch):
    from sqlalchemy.exc import DBAPIError

    original = ReadAuditStore._bind_empty_on
    checked = []

    def check(self, *args):
        with ready[1].engine.connect() as c, c.begin():
            c.execute(text("SET LOCAL lock_timeout='50ms'"))
            with pytest.raises(DBAPIError):
                c.execute(
                    update(accounts)
                    .where(accounts.c.account_id == "DEMO-test")
                    .values(owner=str(uuid4()))
                )
        checked.append(True)
        return original(self, *args)

    monkeypatch.setattr(ReadAuditStore, "_bind_empty_on", check)
    assert call(ready, apply=True, readers_stopped=True)["audit_binding_created"]
    assert checked == [True]


def test_valid_other_journal_does_not_replace_account_binding(ready):
    folder = ready[3] / "other"
    folder.mkdir(mode=0o700)
    identity = uuid4()
    ready[1].bootstrap(folder, "DEMO-other", identity, **ready[5])
    ready[2].seconds += 71
    before = state(ready)
    with pytest.raises(LocalQuotaStopped, match="ACCOUNT_BINDING_MISMATCH"):
        prepare_read_audit(
            ready[1].engine,
            "DEMO-test",
            folder,
            "DEMO-other",
            identity,
            ready[1].installation_id(),
            apply=True,
            readers_stopped=True,
            clocks=RuntimeClocks(ready[2].mono, ready[2].utc, lambda: "test-boot"),
        )
    assert state(ready) == before
