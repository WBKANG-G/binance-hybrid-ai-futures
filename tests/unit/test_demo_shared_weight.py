"""Real disk/process weight admission; injected clock, no exchange requests."""

import multiprocessing
import os
import signal
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from packages.persistence.demo_shared_weight import (
    STARTUP_HOLD,
    WINDOW,
    SharedWeightJournal,
    SharedWeightStopped,
)
from tests.unit.test_durable_order_quota import QuotaClock


@pytest.fixture
def shared(tmp_path):
    clock = QuotaClock()
    identity = uuid4()
    b = SharedWeightJournal.create(
        tmp_path,
        "DEMO-shared",
        identity,
        monotonic=clock.mono,
        wall_clock=clock.utc,
        boot=lambda: "test-boot",
    )
    yield b, clock, tmp_path, identity


def ready(shared):
    b, clock, *_ = shared
    clock.seconds = STARTUP_HOLD
    return b, clock


def test_startup_requires_hold_without_claiming_ip_capacity(shared):
    b, clock, *_ = shared
    with pytest.raises(SharedWeightStopped, match="HOLD"):
        b.reserve(1, "ORDINARY")
    assert not b.view()["startup_ready"]
    clock.seconds = STARTUP_HOLD
    assert b.view()["startup_ready"] and not b.view()["actual_ip_capacity_reserved"]


def test_two_objects_share_reservations_and_emergency_reserve(shared):
    b, clock, path, identity = shared
    clock.seconds = STARTUP_HOLD
    other = SharedWeightJournal(
        path,
        "DEMO-shared",
        identity,
        monotonic=clock.mono,
        wall_clock=clock.utc,
        boot=lambda: "test-boot",
    )
    b.reserve(5060, "ORDINARY")
    with pytest.raises(SharedWeightStopped, match="CAPACITY"):
        other.reserve(1, "ORDINARY")
    other.reserve(128, "WATCH")
    with pytest.raises(SharedWeightStopped, match="CAPACITY"):
        b.reserve(1, "WATCH")
    b.reserve(384, "PROOF")
    other.reserve(128, "RESCUE")
    assert other.view()["pressure"] == 5700
    with pytest.raises(SharedWeightStopped, match="CAPACITY"):
        b.reserve(1, "RESCUE")


def test_pending_never_expires_and_completion_starts_new_window(shared):
    b, clock = ready(shared)
    token = b.reserve(5700, "RESCUE")
    clock.seconds += 3600
    assert b.view()["pending_weight"] == 5700
    b.finish(token, used_weight=1, status=200)
    clock.seconds += WINDOW - 0.001
    with pytest.raises(SharedWeightStopped, match="CAPACITY"):
        b.reserve(1, "RESCUE")
    clock.seconds += 0.002
    assert b.view()["pressure"] == 0
    b.reserve(1, "RESCUE")


def test_lower_out_of_order_headers_do_not_refund_inflight_sends(shared):
    b, clock = ready(shared)
    first = b.reserve(100, "RESCUE")
    second = b.reserve(200, "RESCUE")
    b.finish(second, used_weight=1, status=200)
    assert b.view()["pressure"] >= 300
    b.finish(first, used_weight=5500, status=200)
    assert b.view()["pressure"] >= 5700
    with pytest.raises(SharedWeightStopped, match="CAPACITY"):
        b.reserve(1, "RESCUE")


def test_old_pending_is_included_even_with_newer_high_header(shared):
    b, clock = ready(shared)
    first = b.reserve(300, "RESCUE")
    second = b.reserve(1, "RESCUE")
    b.finish(second, used_weight=5400, status=200)
    assert b.view()["pressure"] >= 5700
    b.finish(first, used_weight=1, status=200)
    assert b.view()["pressure"] >= 5700


@pytest.mark.parametrize(
    "used,status", [(None, 200), (1, 429), (1, 418), (-1, 200), (True, 200), (1, None)]
)
def test_bad_metadata_or_venue_limit_poison_is_durable(shared, used, status):
    b, clock, path, identity = shared
    clock.seconds = STARTUP_HOLD
    token = b.reserve(1, "RESCUE")
    b.finish(token, used_weight=used, status=status)
    with pytest.raises(SharedWeightStopped, match="STOPPED"):
        b.reserve(1, "RESCUE")
    with pytest.raises(SharedWeightStopped, match="STOPPED"):
        SharedWeightJournal(
            path,
            "DEMO-shared",
            identity,
            monotonic=clock.mono,
            wall_clock=clock.utc,
            boot=lambda: "test-boot",
        )


def test_local_denial_keeps_charge_without_fabricated_header(shared):
    b, clock = ready(shared)
    token = b.reserve(100, "ORDINARY")
    b.finish(token, used_weight=None, status=None, sent=False)
    assert b.view()["pressure"] == 100 and b.view()["pending_attempts"] == 0
    with pytest.raises(SharedWeightStopped, match="NOT_PENDING"):
        b.finish(token, used_weight=1, status=200)


@pytest.mark.parametrize("kind", ["backward", "wall_forward", "boot"])
def test_clock_and_boot_faults_are_sticky(shared, kind):
    b, clock = ready(shared)
    b.reserve(1, "RESCUE")
    if kind == "backward":
        clock.seconds -= 1
    elif kind == "wall_forward":
        clock.wall_delta = 3600
    else:
        b.boot = lambda: "other-boot"
    with pytest.raises(SharedWeightStopped, match="CLOCK_OR_BOOT"):
        b.view()
    clock.seconds = STARTUP_HOLD + 2
    clock.wall_delta = 0
    b.boot = lambda: "test-boot"
    with pytest.raises(SharedWeightStopped, match="STOPPED"):
        b.view()


def test_missing_store_is_not_recreated_and_wrong_identity_is_rejected(shared):
    b, clock, path, identity = shared
    with pytest.raises(SharedWeightStopped, match="BINDING"):
        SharedWeightJournal(
            path,
            "DEMO-shared",
            uuid4(),
            monotonic=clock.mono,
            wall_clock=clock.utc,
            boot=lambda: "test-boot",
        )
    b.path.unlink()
    before = set(path.iterdir())
    with pytest.raises(OSError):
        SharedWeightJournal(path, "DEMO-shared", identity)
    assert set(path.iterdir()) == before


def _competitor(folder, identity, barrier, queue):
    from datetime import UTC, datetime

    # Accelerate only the shared startup hold, then use the real common clock.
    for _ in range(100):
        try:
            b = SharedWeightJournal(
                Path(folder),
                "DEMO-processes",
                identity,
                monotonic=lambda: time.monotonic() + 70.01,
                wall_clock=lambda: datetime.now(UTC) + timedelta(seconds=70.01),
            )
            break
        except SharedWeightStopped as error:
            if str(error) != "SHARED_WRITER_BUSY":
                raise
            time.sleep(0.005)
    barrier.wait(timeout=15)
    for _ in range(100):
        try:
            b.reserve(1000, "RESCUE")
            queue.put("ACCEPTED")
            return
        except SharedWeightStopped as error:
            if str(error) != "SHARED_WRITER_BUSY":
                queue.put(str(error))
                return
            time.sleep(0.005)
    queue.put("LOCK_TIMEOUT")


def test_eight_processes_cannot_each_spend_the_same_budget(tmp_path):
    identity = uuid4()
    SharedWeightJournal.create(tmp_path, "DEMO-processes", identity)
    ctx = multiprocessing.get_context("spawn")
    barrier, queue = ctx.Barrier(8), ctx.Queue()
    processes = [
        ctx.Process(target=_competitor, args=(str(tmp_path), identity, barrier, queue))
        for _ in range(8)
    ]
    for p in processes:
        p.start()
    results = [queue.get(timeout=25) for p in processes]
    for p in processes:
        p.join(10)
        assert p.exitcode == 0
    assert results.count("ACCEPTED") == 5 and results.count("SHARED_WEIGHT_CAPACITY") == 3


def _crash_sender(folder, identity, queue):
    from datetime import UTC, datetime

    b = SharedWeightJournal(
        Path(folder),
        "DEMO-processes",
        identity,
        monotonic=lambda: time.monotonic() + 70.01,
        wall_clock=lambda: datetime.now(UTC) + timedelta(seconds=70.01),
    )
    b.reserve(5700, "RESCUE")
    queue.put("COMMITTED_BEFORE_POSSIBLE_SEND")
    while True:
        time.sleep(0.1)


def test_sigkill_preserves_all_pending_weight(tmp_path):
    from datetime import UTC, datetime

    identity = uuid4()
    SharedWeightJournal.create(tmp_path, "DEMO-processes", identity)
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    p = ctx.Process(target=_crash_sender, args=(str(tmp_path), identity, queue))
    p.start()
    assert queue.get(timeout=20) == "COMMITTED_BEFORE_POSSIBLE_SEND"
    os.kill(p.pid, signal.SIGKILL)
    p.join(10)
    assert p.exitcode == -signal.SIGKILL
    b = SharedWeightJournal(
        tmp_path,
        "DEMO-processes",
        identity,
        monotonic=lambda: time.monotonic() + 70.01,
        wall_clock=lambda: datetime.now(UTC) + timedelta(seconds=70.01),
    )
    assert b.view()["pending_weight"] == 5700
    with pytest.raises(SharedWeightStopped, match="CAPACITY"):
        b.reserve(1, "RESCUE")


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE attempts SET weight=-1",
        "UPDATE attempts SET total_at=999999",
        "UPDATE meta SET total=-1",
    ],
)
def test_invalid_persisted_accounting_cannot_grant_capacity(shared, mutation):
    import sqlite3

    b, clock = ready(shared)
    b.reserve(1, "RESCUE")
    with sqlite3.connect(b.path) as c:
        c.execute(mutation)
    with pytest.raises(SharedWeightStopped, match="STORAGE_INVALID"):
        b.reserve(1, "RESCUE")
