"""Explicit GET setup on real PG/SQLite; no actual exchange requests."""

# ruff: noqa: F401,F811
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, inspect, select, text, update
from sqlalchemy.exc import DBAPIError

from packages.persistence.demo import accounts
from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_read_audit import AuditedDemoReadJournal, ReadAuditStore, read_events
from packages.persistence.demo_response_evidence import (
    EvidenceReadJournal,
    ResponseEvidenceStore,
    bindings,
    events,
)
from packages.persistence.demo_shared_weight import SharedWeightStopped
from services.execution.demo_response_evidence_bootstrap import prepare_response_evidence
from services.execution.demo_runtime_storage import RuntimeClocks
from tests.integration.test_demo_read_audit import reader, records
from tests.integration.test_read_audit_bootstrap import call as v1call
from tests.integration.test_read_audit_bootstrap import ready, state
from tests.integration.test_runtime_read_accounting import signed
from tests.integration.test_shared_weight_checkpoint import anchored, demo_system
from tests.unit.test_quota_startup_read_only import files


@pytest.fixture
def setup_ready(ready):
    v1call(ready, apply=True, readers_stopped=True)
    return ready


def clock(a, **kw):
    opts = dict(monotonic=a[2].mono, wall_clock=a[2].utc, boot=lambda: "test-boot")
    opts.update(kw)
    return RuntimeClocks(**opts)


def call(a, **kw):
    return prepare_response_evidence(
        a[1].engine,
        "DEMO-test",
        a[3],
        a[0].scope,
        a[4],
        a[1].installation_id(),
        clocks=kw.pop("clocks", clock(a)),
        **kw,
    )


def shared(a):
    audit = ReadAuditStore(a[1].engine)
    return audit, AuditedDemoReadJournal(a[3], a[0].scope, a[4], anchors=a[1], audit=audit, **a[5])


def test_plan_is_read_only_and_apply_preserves_used_v1_history(setup_ready):
    a = setup_ready
    audit, old = shared(a)
    reader((a, audit, old))(*signed())
    before, history = state(a), records(a)
    tables = inspect(a[1].engine).get_table_names()
    plan = call(a)
    assert plan["evidence_schema_required"] and plan["evidence_binding_required"]
    assert state(a) == before and inspect(a[1].engine).get_table_names() == tables
    applied = call(a, apply=True, readers_stopped=True)
    assert applied["evidence_schema_created"] and applied["evidence_binding_created"]
    assert applied["prior_audit_events_preserved"] == 2
    assert not applied["installation_ready"] and not applied["prior_audit_backfilled"]
    # Only the new independent witness is added; all old file bytes and DB state stay exact.
    after = state(a)
    assert after[1:] == before[1:]
    witness = ResponseEvidenceStore.witness(a[3], a[0].scope)
    assert {k: v for k, v in after[0].items() if k != witness.name} == before[0]
    assert records(a) == history
    with a[1].engine.connect() as c:
        root = c.execute(select(bindings)).all()
        assert len(root) == 1 and not c.execute(select(events)).all()
    before = state(a)
    again = call(a, apply=True, readers_stopped=True)
    assert not again["evidence_schema_created"] and not again["evidence_binding_created"]
    assert state(a) == before


def test_explicit_stop_attestation_required_before_any_access():
    with pytest.raises(LocalQuotaStopped, match="READERS_STOPPED_REQUIRED"):
        prepare_response_evidence(None, None, None, None, None, None, apply=True)


@pytest.mark.parametrize("fault", ["owner", "unpaused", "period", "binding", "positions"])
def test_account_guard_before_any_schema_or_file_write(setup_ready, fault):
    a = setup_ready
    with a[1].engine.begin() as c:
        row = c.execute(select(accounts)).mappings().one()
        ledger = row["state"]
        if fault == "owner":
            c.execute(update(accounts).values(owner=str(uuid4())))
        else:
            if fault == "unpaused":
                ledger["paused"] = False
            if fault == "period":
                ledger["daily_key"] = "2026-11-08"
            if fault == "binding":
                ledger["operations"] = {}
            if fault == "positions":
                ledger["positions"] = {"BTCUSDT": "1"}
            c.execute(update(accounts).values(state=ledger))
    before = state(a)
    with pytest.raises((LocalQuotaStopped, ValueError)):
        call(a, apply=True, readers_stopped=True)
    assert state(a) == before
    assert bindings.name not in inspect(a[1].engine).get_table_names()


@pytest.mark.parametrize("fault", ["boot", "drift", "backward", "startup", "pending"])
def test_clock_and_pending_fail_without_setup_writes(setup_ready, fault):
    a = setup_ready
    opts = {}
    if fault == "boot":
        opts["boot"] = lambda: "new-boot"
    if fault == "drift":
        opts["wall_clock"] = lambda: a[2].utc() + timedelta(seconds=2)
    if fault == "backward":
        opts["monotonic"] = lambda: -1.0
    if fault == "startup":
        a[2].seconds = 69.0
    if fault == "pending":
        shared(a)[1].reserve_read("/fapi/v3/account", 5)
    before = state(a)
    with pytest.raises((LocalQuotaStopped, SharedWeightStopped)):
        call(a, apply=True, readers_stopped=True, clocks=clock(a, **opts))
    assert state(a) == before
    assert bindings.name not in inspect(a[1].engine).get_table_names()


def test_existing_schema_is_bound_and_partial_schema_is_never_repaired(setup_ready):
    a = setup_ready
    with a[1].engine.begin() as c:
        bindings.create(c)
    before = state(a)
    with pytest.raises(LocalQuotaStopped, match="PARTIAL_SCHEMA"):
        call(a, apply=True, readers_stopped=True)
    assert state(a) == before and events.name not in inspect(a[1].engine).get_table_names()


def test_existing_schema_supported(setup_ready):
    a = setup_ready
    ResponseEvidenceStore(a[1].engine).provision(apply=True)
    result = call(a, apply=True, readers_stopped=True)
    assert not result["evidence_schema_created"] and result["evidence_binding_created"]


@pytest.mark.parametrize("fault", ["before_witness", "after_witness", "commit"])
def test_write_boundary_failures_never_report_success_or_repair_orphans(
    setup_ready, monkeypatch, fault
):
    import packages.persistence.demo_response_evidence as module

    a = setup_ready
    before = state(a)
    original = module._write_new

    def fail(*args):
        if fault == "after_witness":
            original(*args)
        raise RuntimeError("synthetic boundary failure")

    def fail_commit(c):
        if (
            c.execute(text("SELECT to_regclass('read_response_evidence_bindings_v2')")).scalar()
            is not None
        ):
            raise RuntimeError("synthetic boundary failure")

    if fault == "commit":
        event.listen(a[1].engine, "commit", fail_commit)
    else:
        monkeypatch.setattr(module, "_write_new", fail)
    try:
        with pytest.raises(RuntimeError, match="synthetic boundary"):
            call(a, apply=True, readers_stopped=True)
    finally:
        if fault == "commit":
            event.remove(a[1].engine, "commit", fail_commit)
    after = state(a)
    assert after[1:] == before[1:]
    assert bindings.name not in inspect(a[1].engine).get_table_names()
    if fault != "before_witness":
        retained = files(a[3])
        with pytest.raises(LocalQuotaStopped, match="ORPHAN_WITNESS"):
            call(a, apply=True, readers_stopped=True)
        assert files(a[3]) == retained
    else:
        assert after == before


def test_account_write_locked_during_binding(setup_ready, monkeypatch):
    a = setup_ready
    original = ResponseEvidenceStore._bind_on
    checked = []

    def guarded(self, *args):
        with a[1].engine.connect() as c, c.begin():
            c.execute(text("SET LOCAL lock_timeout='50ms'"))
            with pytest.raises(DBAPIError):
                c.execute(update(accounts).values(owner=str(uuid4())))
        checked.append(True)
        return original(self, *args)

    monkeypatch.setattr(ResponseEvidenceStore, "_bind_on", guarded)
    assert call(a, apply=True, readers_stopped=True)["evidence_binding_created"]
    assert checked == [True]


@pytest.mark.parametrize("fault", ["boot", "drift", "backward"])
def test_bound_writer_clock_change_denies_without_writes(setup_ready, fault):
    a = setup_ready
    call(a, apply=True, readers_stopped=True)
    audit = ReadAuditStore(a[1].engine)
    opts = a[5].copy()
    live = EvidenceReadJournal(
        a[3],
        a[0].scope,
        a[4],
        anchors=a[1],
        audit=audit,
        evidence=ResponseEvidenceStore(a[1].engine),
        account="DEMO-test",
        enable_evidence_binding=True,
        read_only=False,
        **opts,
    )
    if fault == "boot":
        live.boot = lambda: "new-boot"
    if fault == "drift":
        live.wall_clock = lambda: a[2].utc() + timedelta(seconds=2)
    if fault == "backward":
        live.monotonic = lambda: -1.0
    before = state(a)
    with pytest.raises(LocalQuotaStopped, match="CLOCK_REVIEW_REQUIRED"):
        live.view()
    assert state(a) == before
    with pytest.raises(LocalQuotaStopped, match="CLOCK_REVIEW_REQUIRED"):
        # Directly trying a write cannot bypass the same preflight.
        live.reserve_read("/fapi/v3/account", 5)
    assert state(a) == before


def test_same_boot_reopen_remains_readonly(setup_ready):
    a = setup_ready
    call(a, apply=True, readers_stopped=True)
    a[2].seconds += 10
    before = state(a)
    assert call(a)["get_evidence_setup"] == "CONFIGURED_KEEP_PAUSED"
    assert state(a) == before
