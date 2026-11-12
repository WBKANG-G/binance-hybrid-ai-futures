"""Used GET journal recovery preserves history and refuses every unresolved outcome."""

# ruff: noqa: F811
import copy
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import event, select

from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_read_audit import (
    AuditedDemoReadJournal,
    read_events,
    validate_transition,
)
from packages.persistence.demo_read_recovery import recover_completed_read_clock as recover
from packages.persistence.demo_shared_checkpoint import checkpoints
from services.execution.demo_runtime_storage import RuntimeClocks
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_read_audit import audited, reader, records  # noqa: F401
from tests.integration.test_runtime_read_accounting import financial, journal, signed
from tests.integration.test_shared_weight_checkpoint import anchored  # noqa: F401
from tests.unit.test_quota_startup_read_only import files


@pytest.fixture
def used(audited):
    reader(audited)(*signed())
    return audited


def clocks(case, kind="boot"):
    a, _, _ = case
    current = RuntimeClocks(a[2].mono, a[2].utc, lambda: "test-boot")
    if kind == "boot":
        return replace(current, boot=lambda: "new-boot")
    if kind == "wall":
        return replace(current, wall_clock=lambda: a[2].utc() + timedelta(seconds=5))
    if kind == "backwards":
        return replace(current, monotonic=lambda: a[2].mono() - 50)
    return current


def run(case, current=None, **kwargs):
    a, audit, shared = case
    return recover(
        a[3],
        shared.scope,
        shared.identity,
        anchor=a[1],
        audit=audit,
        apply=True,
        clocks=current or clocks(case),
        **kwargs,
    )


def reopened(case, current):
    a, audit, shared = case
    return AuditedDemoReadJournal(
        a[3],
        shared.scope,
        shared.identity,
        anchors=a[1],
        audit=audit,
        monotonic=current.monotonic,
        wall_clock=current.wall_clock,
        boot=current.boot,
    )


def checkpoint_rows(case):
    a, _, _ = case
    with a[1].engine.connect() as c:
        return c.execute(select(checkpoints).order_by(checkpoints.c.revision)).mappings().all()


def test_explicit_apply_is_required_before_settings_or_storage_access():
    with pytest.raises(LocalQuotaStopped, match="EXPLICIT_APPLY_REQUIRED"):
        recover(None, None, None, anchor=None, audit=None)


@pytest.mark.parametrize("kind", ["boot", "wall", "backwards"])
def test_used_complete_rows_rebase_conservatively_preserving_history_and_full_startup_hold(
    used, kind
):
    a, audit, shared = used
    current = clocks(used, kind)
    old_files = files(a[3])
    old_database = a[0].path.read_bytes()
    old_rows = records(a)
    old_cp = checkpoint_rows(used)
    fin = financial(a)
    old_state = copy.deepcopy(old_rows[-1]["state"])
    result = run(used, current)
    assert result["read_clock_recovery"] == "WAIT_STARTUP_HOLD" and result["shared_journal_changed"]
    assert result["shared_total_weight"] == 5 and not result["startup_ready"]
    assert not result["financial_ledger_changed"] and not result["period_baselines_changed"]
    assert (
        financial(a) == fin and records(a)[:-1] == old_rows and checkpoint_rows(used)[:-1] == old_cp
    )
    row = records(a)[-1]
    assert row["action"]["kind"] == "CLOCK_REBASE"
    backup = next(a[3].glob("read-clock-recovery-*/original.sqlite3"))
    assert backup.read_bytes() == old_database
    assert files(a[3])[a[0].lock_path.name] == old_files[a[0].lock_path.name]
    validate_transition(old_state, row["state"], row["action"])
    restored = reopened(used, current)
    assert restored.view()["pressure"] >= 100  # no immediate loss of the venue floor
    with pytest.raises(LocalQuotaStopped, match="SHARED_STARTUP_HOLD"):
        restored.reserve_read("/fapi/v3/account", 5)
    a[2].seconds += 70
    assert not audit.review(restored)["startup_ready"]
    a[2].seconds += 0.002
    assert audit.review(restored)["startup_ready"] and restored.view()["pressure"] == 0
    before = files(a[3])
    events = records(a)
    cps = checkpoint_rows(used)
    result = run(used, current)
    assert (
        not result["shared_journal_changed"]
        and result["read_clock_recovery"] == "RECOVERED_KEEP_PAUSED"
    )
    assert files(a[3]) == before and records(a) == events and checkpoint_rows(used) == cps


def test_valid_clock_is_a_read_only_noop(used):
    a, _, _ = used
    before = files(a[3])
    events = records(a)
    cps = checkpoint_rows(used)
    result = run(used, clocks(used, "valid"))
    assert not result["shared_journal_changed"]
    assert files(a[3]) == before and records(a) == events and checkpoint_rows(used) == cps


def test_pending_request_is_not_expired_or_cleared(audited):
    a, _, shared = audited
    shared.reserve_read("/fapi/v3/account", 5)
    a[2].seconds += 1000
    before = files(a[3])
    events = records(a)
    with pytest.raises(LocalQuotaStopped, match="PENDING_REQUESTS"):
        run(audited)
    assert files(a[3]) == before and records(a) == events and journal(a)[1][0][1] is None


@pytest.mark.parametrize("fault", ["unknown", "rate_limit", "missing_metadata"])
def test_unknown_or_stopped_journal_is_never_reset(audited, fault):
    from integrations.binance_usdm.rest import RESTResponse

    a, _, _ = audited

    def send(*args):
        if fault == "unknown":
            raise RuntimeError("SYNTHETIC_PRIVATE_NETWORK")
        return RESTResponse(429 if fault == "rate_limit" else 200, b"{}", {})

    with pytest.raises(LocalQuotaStopped):
        reader(audited, send)(*signed())
    before = files(a[3])
    events = records(a)
    with pytest.raises(LocalQuotaStopped, match="STOPPED_OR_BINDING_INVALID"):
        run(audited)
    assert files(a[3]) == before and records(a) == events and journal(a)[0] == (5, 1)


def test_outside_unaudited_change_is_not_adopted(used):
    a, _, _ = used
    t = a[0].reserve(5, "ORDINARY")
    a[0].finish(t, used_weight=10, status=200)
    before = files(a[3])
    events = records(a)
    with pytest.raises(LocalQuotaStopped, match="UNRECORDED_CHANGE"):
        run(used)
    assert files(a[3]) == before and records(a) == events


def test_backup_corruption_is_detected_on_review_and_reopen(used):
    a, audit, _ = used
    current = clocks(used)
    run(used, current)
    backup = next(a[3].glob("read-clock-recovery-*/original.sqlite3"))
    backup.write_bytes(b"changed")
    with pytest.raises(LocalQuotaStopped, match="BACKUP_INVALID"):
        reopened(used, current)


def test_audit_failure_keeps_original_database_and_preserves_intent_for_review(used, monkeypatch):
    a, audit, _ = used
    original = a[0].path.read_bytes()
    events = records(a)
    cps = checkpoint_rows(used)

    def fail(*args):
        raise RuntimeError("SYNTHETIC_PRIVATE_AUDIT")

    monkeypatch.setattr(audit, "append", fail)
    with pytest.raises(RuntimeError):
        run(used)
    assert (
        a[0].path.read_bytes() == original and records(a) == events and checkpoint_rows(used) == cps
    )
    assert len(list(a[3].glob("read-clock-recovery-*/intent.json"))) == 1
    with pytest.raises(LocalQuotaStopped, match="UNFINISHED_OR_MISSING_BACKUP"):
        run(used)
    # Even if the original clock is restored, an unexplained intent is not ignored.
    with pytest.raises(LocalQuotaStopped, match="UNFINISHED_OR_MISSING_BACKUP"):
        reopened(used, clocks(used, "valid"))


def test_pg_commit_failure_leaves_both_audit_and_checkpoint_old_and_blocks_recovery_retry(used):
    a, audit, _ = used
    events = records(a)
    cps = checkpoint_rows(used)
    fired = [False]

    def fail(c):
        if c.execute(
            select(read_events.c.event_id).where(
                read_events.c.action["kind"].astext == "CLOCK_REBASE"
            )
        ).first():
            fired[0] = True
            raise RuntimeError("SYNTHETIC_PRIVATE_PG")

    event.listen(a[1].engine, "commit", fail)
    try:
        with pytest.raises(RuntimeError):
            run(used)
    finally:
        event.remove(a[1].engine, "commit", fail)
    assert fired[0] and records(a) == events and checkpoint_rows(used) == cps
    assert next(a[3].glob("read-clock-recovery-*/original.sqlite3")).exists()
    with pytest.raises(LocalQuotaStopped, match="UNRECORDED_CHANGE"):
        run(used)


@pytest.mark.parametrize(
    "fault", ["charge", "pending", "fake_valid_clock", "identity", "revision", "bool_stop"]
)
def test_clock_event_cannot_hide_refunds_or_unknown_outcomes(used, fault):
    a, _, _ = used
    run(used)
    events = records(a)
    before = copy.deepcopy(events[-2]["state"])
    after = copy.deepcopy(events[-1]["state"])
    action = copy.deepcopy(events[-1]["action"])
    if fault == "charge":
        after[2][0][1] -= 1
    elif fault == "pending":
        before[2][0][4:] = [None, None, None]
    elif fault == "fake_valid_clock":
        after[1][0][3:7] = before[1][0][3:7]
    elif fault == "identity":
        after[1][0][0] = "other"
    elif fault == "bool_stop":
        after[1][0][8] = False
    else:
        action["recovery"]["before_revision"] += 1
    with pytest.raises(LocalQuotaStopped):
        validate_transition(before, after, action)


def test_resumed_read_reuses_cumulative_total_and_preserves_pre_recovery_history(used):
    from integrations.binance_usdm.rest import RESTResponse
    from services.execution.demo_runtime_read_transport import AccountedDemoReadTransport
    from tests.integration.test_runtime_read_accounting import settings

    a, audit, _ = used
    current = clocks(used)
    run(used, current)
    a[2].seconds += 70.002
    old_rows = records(a)
    restored = reopened(used, current)
    r = AccountedDemoReadTransport(
        settings(a),
        restored,
        enable_reads=True,
        sender=lambda *args: RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "5"}),
        monotonic=current.monotonic,
    )
    r(*signed())
    assert records(a)[: len(old_rows)] == old_rows and journal(a)[0] == (10, 0)
    result = audit.review(restored)
    assert result["successful_reads"] == 2 and result["local_read_activity_complete"]


def test_existing_shared_lock_blocks_recovery_before_any_file_or_record_change(used):
    import fcntl
    import os

    a, _, _ = used
    before = files(a[3])
    events = records(a)
    fd = os.open(a[0].lock_path, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(LocalQuotaStopped, match="SHARED_WRITER_BUSY"):
            run(used)
    finally:
        os.close(fd)
    assert files(a[3]) == before and records(a) == events


@pytest.mark.parametrize("limit", ["audit", "backups", "bytes"])
def test_resource_limits_refuse_before_creating_an_intent(used, monkeypatch, limit):
    from packages.persistence import demo_read_audit as audit_module
    from packages.persistence import demo_read_recovery as module

    a, _, _ = used
    before = files(a[3])
    events = records(a)
    if limit == "audit":
        monkeypatch.setattr(audit_module, "MAX_RECORDS", 3)
    elif limit == "backups":
        monkeypatch.setattr(module, "MAX_BACKUPS", 0)
    else:
        monkeypatch.setattr(module, "MAX_BACKUP_BYTES", 1)
    with pytest.raises(LocalQuotaStopped, match="CAPACITY|BACKUP_LIMIT"):
        run(used)
    assert files(a[3]) == before and records(a) == events


def test_changed_clock_during_backup_is_not_committed(used):
    a, _, _ = used
    current = clocks(used)
    count = [0]
    original = a[0].path.read_bytes()
    events = records(a)

    def boot():
        count[0] += 1
        return "first-boot" if count[0] == 1 else "second-boot"

    unstable = replace(current, boot=boot)
    with pytest.raises(LocalQuotaStopped, match="CLOCK_CHANGED_DURING_APPLY"):
        run(used, unstable)
    assert a[0].path.read_bytes() == original and records(a) == events
    assert next(a[3].glob("read-clock-recovery-*/original.sqlite3")).exists()


@pytest.mark.parametrize("artifact", ["intent", "missing", "symlink", "permissions"])
def test_recovery_artifacts_are_verified_before_reopen(used, artifact):
    import os

    a, _, _ = used
    current = clocks(used)
    run(used, current)
    folder = next(a[3].glob("read-clock-recovery-*"))
    backup = folder / "original.sqlite3"
    if artifact == "intent":
        (folder / "intent.json").write_bytes(b"{}")
    elif artifact == "missing":
        (folder / "intent.json").unlink()
    elif artifact == "permissions":
        os.chmod(folder, 0o755)
    else:
        backup.rename(folder / "moved.sqlite3")
        backup.symlink_to("moved.sqlite3")
    with pytest.raises(LocalQuotaStopped):
        reopened(used, current)


def test_expired_completed_rows_can_reanchor_without_resetting_cumulative_charge(used):
    a, audit, shared = used
    a[2].seconds += 70.002
    # A normal next completed read prunes the first expired row but keeps all audit history.
    reader(used)(*signed())
    a[2].seconds += 70.002
    token = shared.reserve_read("/fapi/v3/account", 5)
    shared.finish(token, used_weight=None, status=None, sent=False)
    old_total = journal(a)[0][0]
    old_rows = records(a)
    run(used, clocks(used))
    assert journal(a)[0][0] == old_total and records(a)[: len(old_rows)] == old_rows


def test_natural_elapsed_hold_retains_completed_pressure_until_new_window(used):
    import time

    a, audit, _ = used
    current = RuntimeClocks(boot=lambda: "natural-boot")
    started = time.monotonic()
    result = run(used, current)
    assert result["read_clock_recovery"] == "WAIT_STARTUP_HOLD"
    restored = reopened(used, current)
    time.sleep(35)
    assert not audit.review(restored)["startup_ready"] and restored.view()["pressure"] >= 100
    time.sleep(35.15)
    assert audit.review(restored)["startup_ready"] and restored.view()["pressure"] == 0
    assert time.monotonic() - started >= 70.001


def test_existing_reader_does_not_ignore_interrupted_recovery_files(used, monkeypatch):
    a, audit, shared = used
    original = audit.append

    def fail(*args):
        raise RuntimeError("synthetic append interruption")

    monkeypatch.setattr(audit, "append", fail)
    with pytest.raises(RuntimeError):
        run(used)
    monkeypatch.setattr(audit, "append", original)
    before = files(a[3])
    events = records(a)
    with pytest.raises(LocalQuotaStopped, match="UNFINISHED_OR_MISSING_BACKUP"):
        reader(used, lambda *args: pytest.fail("orphan recovery must block wire"))(*signed())
    assert files(a[3]) == before and records(a) == events


def test_existing_reader_rechecks_changed_recovery_backup_before_reserving(used):
    from integrations.binance_usdm.rest import RESTResponse
    from services.execution.demo_runtime_read_transport import AccountedDemoReadTransport
    from tests.integration.test_runtime_read_accounting import settings

    a, _, _ = used
    current = clocks(used)
    run(used, current)
    a[2].seconds += 70.002
    shared = reopened(used, current)
    r = AccountedDemoReadTransport(
        settings(a),
        shared,
        enable_reads=True,
        sender=lambda *args: RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "10"}),
        monotonic=current.monotonic,
    )
    backup = next(a[3].glob("read-clock-recovery-*/original.sqlite3"))
    backup.write_bytes(b"altered")
    before = files(a[3])
    events = records(a)
    with pytest.raises(LocalQuotaStopped, match="BACKUP_INVALID"):
        r(*signed())
    assert r.diagnostics()["http_attempts"] == 0 and files(a[3]) == before and records(a) == events
