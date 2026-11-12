"""Explicit empty-journal clock recovery using isolated PG and real SQLite."""

# ruff: noqa: F811
import importlib.util
import json
import sqlite3
import time
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from packages.persistence.demo_shared_checkpoint import checkpoints

BASE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "storage_test", BASE / "runtime-storage-setup-20261111/test_runtime_storage_setup.py"
)
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
setup = previous.setup
closed = previous.closed
demo_system = previous.demo_system
applied = previous.applied
spec = importlib.util.spec_from_file_location(
    "clock_recovery", Path(__file__).with_name("recover_unused_shared_clock.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture
def configured(applied, monkeypatch):
    previous.prepare(applied)
    applied[4](70.002)
    previous.finalize(applied)
    monkeypatch.setattr(m.r, "ACCOUNT", "DEMO-test")
    return applied


def run(a, clocks=None, **kw):
    return m.recover(a[0], a[1], parent=a[2], clocks=clocks or a[3], **kw)


def status(a, clocks=None):
    return m.status(a[0], a[1], parent=a[2], clocks=clocks or a[3])


def checkpoint_rows(a):
    with a[1].engine.connect() as c:
        return c.execute(select(checkpoints).order_by(checkpoints.c.revision)).mappings().all()


def changed_boot(a):
    return replace(a[3], boot=lambda: "00000000-0000-0000-0000-000000000002")


@pytest.mark.parametrize("kind", ["boot", "wall", "mono"])
def test_recovery_preserves_financial_rows_original_bytes_and_history_then_waits(configured, kind):
    a = configured
    clocks = (
        changed_boot(a)
        if kind == "boot"
        else (
            replace(a[3], wall_clock=lambda: a[0].now() + timedelta(seconds=5))
            if kind == "wall"
            else replace(a[3], monotonic=lambda: a[3].monotonic() + 5)
        )
    )
    rows = previous.rows(a[0])
    files = previous.disk(a[2])
    history = checkpoint_rows(a)
    with pytest.raises(Exception, match="SHARED_CLOCK_OR_BOOT_CHANGED"):
        status(a, clocks)
    result = run(a, clocks)
    assert result["runtime_clock_recovery"] == "WAIT_STARTUP_HOLD"
    assert result["shared_clock_changed"] and result["checkpoint_appended"]
    assert not result["startup_ready"] and not result["execution_approved"]
    assert result["http_attempts"] == result["order_writes"] == 0
    assert previous.rows(a[0]) == rows
    after = checkpoint_rows(a)
    assert after[:-1] == history and len(after) == len(history) + 1
    root = a[2] / m.r.STORAGE_DIRECTORY
    intent_path = next(root.glob("clock-recovery-*/intent.json"))
    intent = json.loads(intent_path.read_text())
    backup = intent_path.with_name("original.sqlite3").read_bytes()
    active = next(root.glob("shared-weight/*.sqlite3"))
    assert backup and m.hashlib.sha256(backup).hexdigest() == intent["original_sqlite_sha256"]
    assert m.hashlib.sha256(backup).hexdigest() == files[str(active.relative_to(a[2]))]
    for name, digest in files.items():
        if "shared-weight/" not in name or not name.endswith(".sqlite3"):
            assert previous.disk(a[2])[name] == digest
    a[4](70)
    assert status(a, clocks)["runtime_clock_recovery"] == "WAIT_STARTUP_HOLD"
    a[4](0.002)
    assert status(a, clocks)["runtime_clock_recovery"] == "RECOVERED_KEEP_PAUSED"
    reviewed = m.r.review_controls(a[0], a[1], parent=a[2], clocks=clocks)
    assert reviewed["installed_controls_verified"] and not reviewed["execution_approved"]
    stable = previous.disk(a[2])
    history = checkpoint_rows(a)
    again = run(a, clocks)
    assert not again["shared_clock_changed"]
    assert previous.disk(a[2]) == stable and checkpoint_rows(a) == history
    assert previous.rows(a[0]) == rows


def test_valid_clock_does_not_modify_files_or_checkpoint(configured):
    a = configured
    before = previous.disk(a[2])
    history = checkpoint_rows(a)
    rows = previous.rows(a[0])
    result = run(a)
    assert result["runtime_clock_recovery"] == "RECOVERED_KEEP_PAUSED"
    assert not result["shared_clock_changed"]
    assert before == previous.disk(a[2]) and history == checkpoint_rows(a)
    assert rows == previous.rows(a[0])


def test_period_mismatch_is_not_rebased_by_clock_recovery(configured):
    a = configured
    a[4](86400)
    rows = previous.rows(a[0])
    result = run(a, changed_boot(a))
    assert not result["period_keys_match"] and not result["period_baselines_changed"]
    assert result["periods"]["stored_day_kst"] == "2026-11-11"
    assert result["periods"]["current_day_kst"] == "2026-11-12"
    assert previous.rows(a[0]) == rows


def test_used_shared_journal_is_refused_without_refunding(configured):
    a = configured
    ledger, root, record, storage = m.context(a[0], a[1], a[2], a[3])
    from packages.persistence.demo_shared_checkpoint import AnchoredSharedWeightJournal

    writer = AnchoredSharedWeightJournal(
        storage.shared_directory,
        storage.shared_scope,
        storage.shared_identity,
        anchors=a[1],
        monotonic=a[3].monotonic,
        wall_clock=a[3].wall_clock,
        boot=a[3].boot,
    )
    writer.reserve(20, "ORDINARY")
    before = previous.disk(a[2])
    history = checkpoint_rows(a)
    with pytest.raises(m.r.Blocked, match="RECOVERY_SHARED_JOURNAL_USED"):
        run(a, changed_boot(a))
    assert before == previous.disk(a[2]) and history == checkpoint_rows(a)


@pytest.mark.parametrize("kind", ["rollback", "stopped", "symlink", "sidecar", "intent"])
def test_unsafe_or_partial_state_is_refused_without_modification(configured, kind):
    a = configured
    root = a[2] / m.r.STORAGE_DIRECTORY
    active = next(root.glob("shared-weight/*.sqlite3"))
    if kind in ("rollback", "stopped"):
        if kind == "rollback":
            with sqlite3.connect(active) as c:
                c.execute("UPDATE meta SET wall=wall+1")
        else:
            _, _, _, storage = m.context(a[0], a[1], a[2], a[3])
            with m.locked_raw_shared(storage, a[1], write=True) as (c, _, cp, _):
                c.execute("UPDATE meta SET stopped=1")
                a[1].append(
                    cp.connection,
                    storage.shared_scope,
                    storage.shared_identity,
                    cp.revision + 1,
                    m.fingerprint(c),
                )
    if kind == "symlink":
        other = active.with_name("other.db")
        active.rename(other)
        active.symlink_to(other.name)
    if kind == "sidecar":
        Path(str(active) + "-wal").write_bytes(b"not a trusted journal")
    if kind == "intent":
        history = checkpoint_rows(a)
        cp = history[-1]
        _, _, record, _ = m.context(a[0], a[1], a[2], a[3])
        ident = m.uuid5(
            m.UUID(record["shared_journal"]),
            "unused-clock-recovery:" + str(cp["revision"]) + ":" + cp["digest"],
        )
        (root / ("clock-recovery-" + str(ident))).mkdir(mode=0o700)
    before = previous.disk(a[2])
    history = checkpoint_rows(a)
    expected = {
        "rollback": "SHARED_CHECKPOINT_MISMATCH",
        "stopped": "RECOVERY_STOPPED_JOURNAL_REQUIRES_REVIEW",
        "symlink": "SHARED_FILE_IDENTITY_INVALID",
        "sidecar": "RECOVERY_SQLITE_SIDECAR_PRESENT",
        "intent": "RECOVERY_PRIOR_INTENT_REQUIRES_REVIEW",
    }[kind]
    with pytest.raises(Exception, match=expected):
        run(a, changed_boot(a))
    assert before == previous.disk(a[2]) and history == checkpoint_rows(a)


def test_sqlite_pg_commit_gap_stays_blocked_and_original_is_preserved(configured):
    a = configured
    history = checkpoint_rows(a)
    rows = previous.rows(a[0])

    def crash():
        raise RuntimeError("synthetic crash between stores")

    with pytest.raises(RuntimeError, match="synthetic crash"):
        run(a, changed_boot(a), after_sqlite_commit=crash)
    assert history == checkpoint_rows(a) and rows == previous.rows(a[0])
    root = a[2] / m.r.STORAGE_DIRECTORY
    assert len(list(root.glob("clock-recovery-*/original.sqlite3"))) == 1
    before = previous.disk(a[2])
    with pytest.raises(m.r.Blocked, match="SHARED_CHECKPOINT_MISMATCH"):
        run(a, changed_boot(a))
    assert before == previous.disk(a[2])


def test_active_leader_refuses_before_clock_changes(configured):
    from uuid import uuid4

    a = configured
    lease = a[0].acquire("DEMO-test", uuid4())
    before = previous.disk(a[2])
    history = checkpoint_rows(a)
    try:
        with pytest.raises(m.r.Blocked, match="SETUP_ACTIVE_OWNER"):
            run(a, changed_boot(a))
        assert before == previous.disk(a[2]) and history == checkpoint_rows(a)
    finally:
        a[0].release(lease)


def test_natural_startup_hold_after_recovery(configured):
    a = configured
    clocks = m.RuntimeClocks()
    start = time.monotonic()
    result = run(a, clocks)
    assert result["runtime_clock_recovery"] == "WAIT_STARTUP_HOLD"
    time.sleep(35)
    assert status(a, clocks)["runtime_clock_recovery"] == "WAIT_STARTUP_HOLD"
    time.sleep(35.1)
    assert status(a, clocks)["runtime_clock_recovery"] == "RECOVERED_KEEP_PAUSED"
    assert time.monotonic() - start >= 70.001


def test_concurrent_shared_lock_refuses_without_changes(configured):
    import fcntl
    import os

    a = configured
    path = next((a[2] / m.r.STORAGE_DIRECTORY).glob("shared-weight/*.lock"))
    fd = os.open(path, os.O_RDONLY)
    before = previous.disk(a[2])
    history = checkpoint_rows(a)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(m.r.Blocked, match="SHARED_WRITER_BUSY"):
            run(a, changed_boot(a))
        assert previous.disk(a[2]) == before and checkpoint_rows(a) == history
    finally:
        os.close(fd)


def test_concurrent_account_row_lock_refuses_without_changes(configured):
    from sqlalchemy.exc import OperationalError

    a = configured
    before = previous.disk(a[2])
    history = checkpoint_rows(a)
    with a[0].engine.connect() as c, c.begin():
        c.execute(
            select(m.r.accounts.c.account_id)
            .where(m.r.accounts.c.account_id == "DEMO-test")
            .with_for_update()
        ).one()
        with pytest.raises(OperationalError):
            run(a, changed_boot(a))
    assert previous.disk(a[2]) == before and checkpoint_rows(a) == history


def test_backup_write_failure_never_changes_active_db_or_checkpoint(configured, monkeypatch):
    a = configured
    active = next((a[2] / m.r.STORAGE_DIRECTORY).glob("shared-weight/*.sqlite3"))
    original = active.read_bytes()
    history = checkpoint_rows(a)

    def fail(*args):
        raise OSError("synthetic full disk before backup write")

    monkeypatch.setattr(m, "write_new", fail)
    with pytest.raises(OSError, match="synthetic full disk"):
        run(a, changed_boot(a))
    assert active.read_bytes() == original and checkpoint_rows(a) == history
    with pytest.raises(m.r.Blocked, match="RECOVERY_PRIOR_INTENT_REQUIRES_REVIEW"):
        run(a, changed_boot(a))


def test_audit_storage_budget_refuses_before_changing_active_state(configured):
    a = configured
    root = a[2] / m.r.STORAGE_DIRECTORY
    count = len(m.r.readonly_files(root))
    for i in range(15 - count):
        (root / ("extra-" + str(i))).write_text("preserve existing file")
    before = previous.disk(a[2])
    history = checkpoint_rows(a)
    with pytest.raises(m.r.Blocked, match="RECOVERY_AUDIT_STORAGE_LIMIT"):
        run(a, changed_boot(a))
    assert previous.disk(a[2]) == before and checkpoint_rows(a) == history
