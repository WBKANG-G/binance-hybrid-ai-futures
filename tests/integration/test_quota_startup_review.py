"""Read-only PostgreSQL transaction plus read-only local journal; no exchange calls."""

# ruff: noqa: F811
import json
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from packages.persistence.demo import accounts, events, outbox
from services.execution.demo_managed_order_quota import RESERVE_PREFIX
from services.execution.demo_quota_startup_review import assess_snapshot, review_startup
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_managed_order_quota import managed  # noqa: F401
from tests.unit.test_quota_startup_read_only import files


def snapshot(store):
    with store.engine.connect() as c:
        return [
            c.execute(select(table).order_by(*table.primary_key.columns)).all()
            for table in (accounts, events, outbox)
        ]


def local_review(managed):
    store, c, bridge, proxy, q, clock, path = managed
    return review_startup(store.engine, c.lease.account_id, path, now=store.now())


def test_clean_closed_storage_is_reviewed_without_writes_or_http(managed):
    store, c, bridge, proxy, q, clock, path = managed
    q.close()
    store.release(c.lease)
    before, disk, calls = snapshot(store), files(path), list(c.client.send.calls)
    seen = []

    def inspect_transaction(connection, cursor, statement, parameters, context, many):
        if statement.startswith("SELECT demo_accounts.state"):
            with connection.connection.cursor() as raw:
                raw.execute("SHOW transaction_read_only")
                read_only = raw.fetchone()[0]
                raw.execute("SHOW transaction_isolation")
                seen.append((read_only, raw.fetchone()[0]))

    event.listen(store.engine, "before_cursor_execute", inspect_transaction)
    try:
        result = local_review(managed)
    finally:
        event.remove(store.engine, "before_cursor_execute", inspect_transaction)
    assert seen == [("on", "repeatable read")]
    assert result["local_checks_passed"] and result["pending_flows"] == 0
    assert result["slots_10s"] is None and not result["execution_approved"]
    assert not result["leader_acquired"] and not result["session_started"]
    assert snapshot(store) == before and files(path) == disk
    assert list(c.client.send.calls) == calls


def test_active_writer_and_active_lease_are_distinct_blockers(managed):
    store, c, bridge, proxy, q, clock, path = managed
    assert local_review(managed)["blockers"] == ["QUOTA_MANAGED_WRITER_BUSY"]
    q.close()
    result = local_review(managed)
    assert result["local_storage_consistent"]
    assert result["blockers"] == ["ACTIVE_LEADER_LEASE"]


def test_postgres_enforces_rejection_of_an_accidental_write(managed):
    store, c, bridge, proxy, q, clock, path = managed
    q.close()
    before = snapshot(store)

    def inject_write(connection, cursor, statement, parameters, context, many):
        if statement.startswith("SELECT demo_accounts.state"):
            cursor.execute("UPDATE demo_accounts SET fence=fence+1")

    event.listen(store.engine, "before_cursor_execute", inject_write)
    try:
        result = local_review(managed)
    finally:
        event.remove(store.engine, "before_cursor_execute", inject_write)
    assert result["blockers"] == ["LOCAL_DATABASE_ACCESS_FAILED"]
    assert snapshot(store) == before


def test_missing_binding_never_reads_or_provisions_directory(demo_system, tmp_path):
    store, c, fake = demo_system
    missing = tmp_path / "not-created"
    before = snapshot(store)
    result = review_startup(store.engine, c.lease.account_id, missing, now=store.now())
    assert result["blockers"] == ["STORAGE_NOT_PROVISIONED"]
    assert not missing.exists() and snapshot(store) == before


def test_pending_claim_requires_reconciliation_not_a_new_session(managed):
    store, c, bridge, proxy, q, clock, path = managed
    flow = uuid4()
    bridge.reserve(flow)
    ids = json.loads(store.read(c.lease.account_id).operations[RESERVE_PREFIX + str(flow)])["roles"]
    q.claim(flow, "ENTRY", ids["ENTRY"])
    q.close()
    store.release(c.lease)
    before, disk = snapshot(store), files(path)
    result = local_review(managed)
    assert result["blockers"] == ["PENDING_FLOW_REQUIRES_RECONCILIATION"]
    assert result["pending_reserved_slots"] == 6 and result["durable_claims"] == 1
    assert result["slots_60s"] is None and not result["execution_approved"]
    assert snapshot(store) == before and files(path) == disk


def test_pg_anchor_with_missing_journal_does_not_recreate_it(managed):
    store, c, bridge, proxy, q, clock, path = managed
    journal_path = q.path
    q.close()
    journal_path.unlink()
    before = files(path)
    result = local_review(managed)
    assert result["blockers"] == ["QUOTA_JOURNAL_MISSING"]
    assert files(path) == before and not journal_path.exists()


def test_orphan_reservation_and_wrong_binding_are_blocked(managed):
    store, c, bridge, proxy, q, clock, path = managed
    from services.execution.demo_managed_order_quota import roles_for

    q.reserve(uuid4(), roles_for(c.lease.account_id, uuid4()))
    q.close()
    assert local_review(managed)["blockers"] == ["QUOTA_PG_JOURNAL_FLOW_MISMATCH"]


@pytest.mark.parametrize(
    "change,blocker",
    [
        (lambda x: setattr(x, "paused", False), "ACCOUNT_NOT_PAUSED"),
        (lambda x: setattr(x, "daily_key", "2026-01-01"), "PERIOD_KEYS_MISMATCH"),
    ],
)
def test_snapshot_state_blockers_do_not_rewrite_baselines(managed, change, blocker):
    store, c, bridge, proxy, q, clock, path = managed
    q.close()
    ledger = store.read(c.lease.account_id)
    change(ledger)
    before = ledger.model_dump_json()
    result = assess_snapshot(ledger, c.lease.account_id, path, now=store.now(), lease_until=None)
    assert blocker in result["blockers"]
    assert ledger.model_dump_json() == before


def test_missing_account_and_non_demo_names_are_blocked(managed):
    store, c, bridge, proxy, q, clock, path = managed
    assert review_startup(store.engine, "DEMO-missing", path)["blockers"] == [
        "DEMO_ACCOUNT_NOT_FOUND"
    ]
    assert review_startup(store.engine, "LIVE-x", path)["blockers"] == ["DEMO_ACCOUNT_REQUIRED"]


def test_wrong_policy_or_private_binding_does_not_leak_values(managed):
    store, c, bridge, proxy, q, clock, path = managed
    q.close()
    ledger = store.read(c.lease.account_id)
    from services.execution.demo_managed_order_quota import STORAGE_KEY

    ledger.operations[STORAGE_KEY] = json.dumps({"journal": "secret-invalid-id"})
    result = assess_snapshot(ledger, c.lease.account_id, path, now=store.now(), lease_until=None)
    assert result["blockers"] == ["STORAGE_BINDING_INVALID"]
    assert "secret-invalid-id" not in json.dumps(result)


@pytest.mark.parametrize("binding", ["null", "[]", '{"journal":123}', '{"journal":null}'])
def test_invalid_binding_types_are_blocked_without_filesystem_access(managed, binding):
    store, c, bridge, proxy, q, clock, path = managed
    from services.execution.demo_managed_order_quota import STORAGE_KEY

    ledger = store.read(c.lease.account_id)
    ledger.operations[STORAGE_KEY] = binding
    result = assess_snapshot(
        ledger, c.lease.account_id, path / "absent", now=store.now(), lease_until=None
    )
    assert result["blockers"] == ["STORAGE_BINDING_INVALID"]
    assert not (path / "absent").exists()


def test_naive_clock_is_blocked_before_database_access(managed):
    store, c, bridge, proxy, q, clock, path = managed
    result = review_startup(
        store.engine, c.lease.account_id, path, now=store.now().replace(tzinfo=None)
    )
    assert result["blockers"] == ["LOCAL_SNAPSHOT_INVALID"]
