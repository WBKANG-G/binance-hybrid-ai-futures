"""Pre-audit clock recovery cannot adopt usage or rewrite financial/period state."""

# ruff: noqa: F811
import hashlib
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import event, inspect, select

from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_read_audit import ReadAuditStore
from packages.persistence.demo_shared_checkpoint import checkpoints
from services.execution.demo_runtime_storage import RuntimeClocks
from services.execution.demo_unused_read_clock import PREFIX, recover_unused_clock
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_read_audit_bootstrap import call as prepare
from tests.integration.test_read_audit_bootstrap import ready, state  # noqa: F401
from tests.integration.test_shared_weight_checkpoint import anchored  # noqa: F401


def clocks(a, kind="boot"):
    original = RuntimeClocks(a[2].mono, a[2].utc, lambda: "test-boot")
    if kind == "wall":
        return replace(original, wall_clock=lambda: a[2].utc() + timedelta(seconds=10))
    if kind == "backwards":
        return replace(original, monotonic=lambda: a[2].mono() - 50)
    return replace(original, boot=lambda: "new-boot") if kind == "boot" else original


def run(a, clock=None):
    return recover_unused_clock(
        a[1].engine,
        "DEMO-test",
        a[3],
        a[0].scope,
        a[4],
        a[1].installation_id(),
        apply=True,
        clocks=clock or clocks(a),
    )


@pytest.mark.parametrize("kind", ["boot", "wall", "backwards"])
def test_recovery_preserves_financial_rows_original_bytes_and_holds_new_window(ready, kind):
    a = ready
    before = state(a)
    original = a[0].path.read_bytes()
    current = clocks(a, kind)
    report = run(a, current)
    assert (
        report["unused_clock_recovery"] == "WAIT_STARTUP_HOLD" and report["shared_journal_changed"]
    )
    assert state(a)[1:-1] == before[1:-1]  # account/events/outbox unchanged
    backup = next(a[3].glob(PREFIX + "*/original.sqlite3"))
    assert backup.read_bytes() == original
    assert len(state(a)[-1]) == len(before[-1]) + 1
    assert "demo_read_audit_bindings" not in inspect(a[1].engine).get_table_names()
    count = len(state(a)[-1])
    assert not run(a, current)["shared_journal_changed"]
    a[2].seconds += 70
    assert not run(a, current)["startup_ready"]
    a[2].seconds += 0.002
    assert run(a, current)["startup_ready"]
    assert len(state(a)[-1]) == count
    assert hashlib.sha256(backup.read_bytes()).digest() == hashlib.sha256(original).digest()


def test_explicit_apply_precedes_any_access():
    with pytest.raises(LocalQuotaStopped, match="EXPLICIT_APPLY"):
        recover_unused_clock(None, None, None, None, None, None)


@pytest.mark.parametrize("done", [True, False])
def test_used_journal_never_cleared_even_if_request_completed(ready, done):
    token = ready[0].reserve(5, "ORDINARY")
    if done:
        ready[0].finish(token, status=200, used_weight=5)
    before = state(ready)
    with pytest.raises(LocalQuotaStopped, match="USED_OR_STOPPED"):
        run(ready)
    assert state(ready) == before and not list(ready[3].glob(PREFIX + "*"))


def test_existing_audit_binding_cannot_be_bypassed(ready):
    store = ReadAuditStore(ready[1].engine)
    store.provision(apply=True)
    store.bind_empty(ready[0], apply=True)
    before = state(ready)
    with pytest.raises(LocalQuotaStopped, match="AUDITED_SCOPE_FORBIDDEN"):
        run(ready)
    assert state(ready) == before


def test_valid_clock_noop_is_byte_and_checkpoint_preserving(ready):
    before = state(ready)
    assert run(ready, clocks(ready, "valid"))["startup_ready"]
    assert state(ready) == before and not list(ready[3].glob(PREFIX + "*"))


def test_checkpoint_gap_after_sqlite_commit_is_not_repaired(ready):
    engine = ready[1].engine

    def fail(connection):
        if connection.execute(select(checkpoints.c.revision)).scalars().all() == [0, 1]:
            raise RuntimeError("synthetic commit failure")

    event.listen(engine, "commit", fail)
    try:
        with pytest.raises(RuntimeError):
            run(ready)
    finally:
        event.remove(engine, "commit", fail)
    with pytest.raises(LocalQuotaStopped, match="CHECKPOINT_MISMATCH"):
        run(ready)
    assert next(ready[3].glob(PREFIX + "*/original.sqlite3")).exists()


def test_missing_backup_blocks_even_after_clock_became_valid(ready):
    current = clocks(ready)
    run(ready, current)
    next(ready[3].glob(PREFIX + "*/intent.json")).unlink()
    with pytest.raises(LocalQuotaStopped, match="INCOMPLETE_BACKUP"):
        run(ready, current)


def test_recovered_unused_journal_can_be_planned_without_new_audit_rows(ready):
    # Wall-only recovery keeps the fixture's boot identity for prepare().
    run(ready, clocks(ready, "wall"))
    ready[2].wall_delta += 10
    ready[2].seconds += 70.002
    result = prepare(ready)
    assert result["audit_binding_required"] and not result["audit_binding_created"]


def test_clock_change_during_status_wait_does_not_trigger_another_recovery(ready):
    current = clocks(ready)
    run(ready, current)
    before = state(ready)
    with pytest.raises(LocalQuotaStopped, match="CHANGED_DURING_WAIT"):
        recover_unused_clock(
            ready[1].engine,
            "DEMO-test",
            ready[3],
            ready[0].scope,
            ready[4],
            ready[1].installation_id(),
            apply=True,
            status_only=True,
            clocks=replace(current, boot=lambda: "changed-again"),
        )
    assert state(ready) == before
