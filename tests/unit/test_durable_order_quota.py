"""Real fsync/process locks with injected time; no exchange or financial DB."""

import multiprocessing
import os
import signal
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from packages.persistence.demo_order_quota import (
    RESTART_HOLD_SECONDS,
    DurableOrderQuota,
    LocalQuotaStopped,
)


class QuotaClock:
    def __init__(self):
        self.seconds = 0.0
        self.wall_delta = 0.0

    def mono(self):
        return self.seconds

    def utc(self):
        return datetime(2026, 11, 9, tzinfo=UTC) + timedelta(seconds=self.seconds + self.wall_delta)


def roles(tail="a"):
    return dict(ENTRY="DE-" + tail, SL="DS-" + tail, TP="DT-" + tail, EXIT="DX-" + tail)


@pytest.fixture
def journal(tmp_path):
    clock = QuotaClock()
    identity = uuid4()
    q = DurableOrderQuota.create(
        tmp_path, "DEMO-test", identity, monotonic=clock.mono, wall_clock=clock.utc
    )
    q.start_session(uuid4(), 1)
    yield q, clock, tmp_path, identity
    q.close()


def ready(journal):
    q, clock, *_ = journal
    clock.seconds = RESTART_HOLD_SECONDS
    return q, clock


def test_cold_start_is_unknown_not_zero_and_cannot_reserve(journal):
    q, clock, *_ = journal
    assert not q.view()["local_window_ready"] and q.view()["slots_10s"] is None
    assert q.view()["external_account_usage"] == "UNKNOWN"
    with pytest.raises(LocalQuotaStopped, match="RESTART_HOLD"):
        q.reserve(uuid4(), roles())
    clock.seconds = RESTART_HOLD_SECONDS - 0.001
    with pytest.raises(LocalQuotaStopped):
        q.reserve(uuid4(), roles())
    clock.seconds = RESTART_HOLD_SECONDS
    assert q.view()["slots_10s"] == 0 and not q.view()["execution_approved"]


def test_whole_flow_remains_reserved_even_after_minutes_and_claims(journal):
    q, clock = ready(journal)
    flow = uuid4()
    q.reserve(flow, roles())
    for role in ["ENTRY", "SL", "TP"]:
        q.claim(flow, role, roles()[role])
    clock.seconds += 300
    assert q.view()["slots_10s"] == q.view()["slots_60s"] == 6
    with pytest.raises(LocalQuotaStopped, match="PRIOR_FLOW"):
        q.reserve(uuid4(), roles("b"))
    q.claim(flow, "EXIT", "DX-a")
    assert q.view()["slots_60s"] == 6


def test_full_closure_charges_all_six_slots_at_seal_not_early_intent(journal):
    q, clock = ready(journal)
    flow = uuid4()
    q.reserve(flow, roles())
    clock.seconds += 300
    q.seal(flow)
    assert q.view()["slots_10s"] == q.view()["slots_60s"] == 6
    with pytest.raises(LocalQuotaStopped, match="CAPACITY"):
        q.reserve(uuid4(), roles("b"))
    clock.seconds += 10.002
    assert q.view()["slots_10s"] == 0 and q.view()["slots_60s"] == 6
    second = uuid4()
    q.reserve(second, roles("b"))
    q.seal(second)
    assert q.view()["slots_60s"] == 12
    clock.seconds += 10.002
    with pytest.raises(LocalQuotaStopped, match="CAPACITY"):
        q.reserve(uuid4(), roles("c"))
    clock.seconds += 40
    assert q.view()["slots_60s"] == 6


def test_commit_before_send_is_one_shot_even_without_a_response(journal):
    q, _ = ready(journal)
    flow = uuid4()
    q.reserve(flow, roles())
    q.claim(flow, "ENTRY", "DE-a")
    with pytest.raises(LocalQuotaStopped, match="ALREADY_CONSUMED"):
        q.claim(flow, "ENTRY", "DE-a")
    assert set(q.flow_records()[str(flow)]["claims"]) == {"ENTRY"}


def test_restart_never_drops_pending_flow_or_reuses_old_entry(journal):
    q, clock, path, identity = journal
    clock.seconds = RESTART_HOLD_SECONDS
    flow = uuid4()
    q.reserve(flow, roles())
    q.claim(flow, "ENTRY", "DE-a")
    q.close()
    second = DurableOrderQuota(
        path, "DEMO-test", identity, monotonic=clock.mono, wall_clock=clock.utc
    )
    try:
        second.start_session(uuid4(), 2)
        assert second.view()["slots_10s"] is None and second.view()["retained_model_slots_10s"] == 6
        clock.seconds += RESTART_HOLD_SECONDS
        with pytest.raises(LocalQuotaStopped, match="PRIOR_FLOW"):
            second.reserve(uuid4(), roles("b"))
        with pytest.raises(LocalQuotaStopped, match="RECOVERY_EXIT_ONLY"):
            second.claim(flow, "SL", "DS-a")
        second.claim(flow, "EXIT", "DX-a")
        assert second.view()["slots_60s"] == 6
    finally:
        second.close()


def test_missing_file_is_not_automatically_reinitialized(journal):
    q, clock, path, identity = journal
    q.close()
    q.path.unlink()
    with pytest.raises(LocalQuotaStopped, match="JOURNAL_MISSING"):
        DurableOrderQuota(path, "DEMO-test", identity, monotonic=clock.mono, wall_clock=clock.utc)
    assert not q.path.exists()


def test_second_owner_cannot_open_same_journal(journal):
    q, _, path, identity = journal
    with pytest.raises(LocalQuotaStopped, match="WRITER_BUSY"):
        DurableOrderQuota(path, "DEMO-test", identity)
    assert q.owns_process_lock()


@pytest.mark.parametrize("fault", ["backward", "forward_wall", "nan", "backward_wall"])
def test_clock_fault_never_refunds_a_reserved_flow(journal, fault):
    q, clock = ready(journal)
    flow = uuid4()
    q.reserve(flow, roles())
    if fault == "backward":
        clock.seconds -= 1
    elif fault == "forward_wall":
        clock.wall_delta = 3600
    elif fault == "backward_wall":
        clock.wall_delta = -60
    else:
        clock.seconds = float("nan")
    if fault == "nan":
        clock.utc = lambda: datetime(2026, 11, 9, tzinfo=UTC)
        q.wall_clock = clock.utc
    assert not q.view()["clock_trusted"] and q.view()["slots_10s"] is None
    assert q.view()["retained_model_slots_10s"] == 6
    with pytest.raises(LocalQuotaStopped):
        q.seal(flow)


def test_append_only_rows_and_chain_are_checked(journal):
    q, clock, path, identity = journal
    assert q.connection is not None
    with pytest.raises(sqlite3.IntegrityError):
        q.connection.execute("DELETE FROM events")
    with pytest.raises(sqlite3.IntegrityError):
        q.connection.execute("UPDATE events SET payload='{}'")
    q.close()
    conn = sqlite3.connect(q.path)
    conn.execute("DROP TRIGGER quota_no_update")
    conn.execute("UPDATE events SET payload='{}'")
    conn.commit()
    conn.close()
    with pytest.raises(LocalQuotaStopped, match="CHAIN_INVALID"):
        DurableOrderQuota(path, "DEMO-test", identity)


def test_file_replacement_is_not_treated_as_fresh_credit(journal):
    q, _ = ready(journal)
    q.path.rename(q.path.with_suffix(".saved"))
    q.path.write_bytes(b"")
    os.chmod(q.path, 0o600)
    with pytest.raises(LocalQuotaStopped, match="FILE_REPLACED"):
        q.view()
    assert q.owns_process_lock()


def _crash_after_claim(path, identity, flow, pipe):
    clock = QuotaClock()
    q = DurableOrderQuota(
        Path(path), "DEMO-test", identity, monotonic=clock.mono, wall_clock=clock.utc
    )
    q.start_session(uuid4(), 2)
    clock.seconds = RESTART_HOLD_SECONDS
    q.reserve(flow, roles())
    q.claim(flow, "ENTRY", "DE-a")
    pipe.send("COMMITTED")
    os.kill(os.getpid(), signal.SIGKILL)


def test_sigkill_after_commit_preserves_slot_and_duplicate_barrier(tmp_path):
    identity = uuid4()
    q = DurableOrderQuota.create(tmp_path, "DEMO-test", identity)
    q.close()
    parent, child = multiprocessing.get_context("spawn").Pipe()
    flow = uuid4()
    process = multiprocessing.get_context("spawn").Process(
        target=_crash_after_claim, args=(str(tmp_path), identity, flow, child)
    )
    process.start()
    assert parent.poll(10) and parent.recv() == "COMMITTED"
    process.join(10)
    assert process.exitcode == -signal.SIGKILL
    q = DurableOrderQuota(tmp_path, "DEMO-test", identity)
    try:
        q.start_session(uuid4(), 3)
        assert q.view()["retained_model_slots_10s"] == 6
        with pytest.raises(LocalQuotaStopped, match="ALREADY_CONSUMED"):
            q.claim(flow, "ENTRY", "DE-a")
    finally:
        q.close()


def _open_while_busy(path, identity, pipe):
    try:
        q = DurableOrderQuota(Path(path), "DEMO-test", identity)
        q.close()
        pipe.send("UNEXPECTED_OPEN")
    except LocalQuotaStopped as error:
        pipe.send(str(error))


def test_competing_process_cannot_open_while_owner_remains_alive(journal):
    q, _, path, identity = journal
    parent, child = multiprocessing.get_context("spawn").Pipe()
    worker = multiprocessing.get_context("spawn").Process(
        target=_open_while_busy, args=(str(path), identity, child)
    )
    worker.start()
    assert parent.poll(10) and parent.recv() == "QUOTA_MANAGED_WRITER_BUSY"
    worker.join(10)
    assert worker.exitcode == 0 and q.owns_process_lock()


def _inherited_object(q, pipe):
    try:
        q.view()
        pipe.send("UNEXPECTED_USE")
    except LocalQuotaStopped as error:
        pipe.send(str(error))
    finally:
        q.close()


def test_forked_child_cannot_use_or_unlock_parent_object(journal):
    q, _, path, identity = journal
    context = multiprocessing.get_context("fork")
    parent, child = context.Pipe()
    worker = context.Process(target=_inherited_object, args=(q, child))
    worker.start()
    assert parent.poll(10) and parent.recv() == "QUOTA_PROCESS_OWNERSHIP_INVALID"
    worker.join(10)
    assert worker.exitcode == 0 and q.owns_process_lock()
    with pytest.raises(LocalQuotaStopped, match="WRITER_BUSY"):
        DurableOrderQuota(path, "DEMO-test", identity)


def test_existing_storage_id_and_policy_cannot_be_switched(journal):
    from packages.persistence.demo_order_quota import LocalQuotaPolicy

    q, _, path, identity = journal
    q.close()
    with pytest.raises(LocalQuotaStopped, match="STORAGE_BINDING_MISMATCH"):
        DurableOrderQuota(path, "DEMO-test", uuid4())
    with pytest.raises(LocalQuotaStopped, match="STORAGE_BINDING_MISMATCH"):
        DurableOrderQuota(path, "DEMO-test", identity, LocalQuotaPolicy(12, 12))


def test_old_closed_attempts_are_retained_during_restart_hold(journal):
    q, clock, path, identity = journal
    clock.seconds = RESTART_HOLD_SECONDS
    flow = uuid4()
    q.reserve(flow, roles())
    q.seal(flow)
    q.close()
    clock.seconds += 3600
    restored = DurableOrderQuota(
        path, "DEMO-test", identity, monotonic=clock.mono, wall_clock=clock.utc
    )
    try:
        restored.start_session(uuid4(), 2)
        assert (
            restored.view()["slots_60s"] is None
            and restored.view()["retained_model_slots_60s"] == 6
        )
        clock.seconds += RESTART_HOLD_SECONDS
        assert restored.view()["slots_60s"] == 0
        assert str(flow) in restored.flow_records()
    finally:
        restored.close()
