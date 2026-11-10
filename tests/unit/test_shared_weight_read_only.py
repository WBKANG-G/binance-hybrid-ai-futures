"""Read-only shared pressure view never prunes or records a new clock/session."""

# ruff: noqa: F811

import sqlite3
from uuid import uuid4

import pytest

from packages.persistence.demo_shared_weight import (
    STARTUP_HOLD,
    WINDOW,
    SharedWeightJournal,
    SharedWeightStopped,
)
from tests.unit.test_demo_shared_weight import shared  # noqa: F401
from tests.unit.test_quota_startup_read_only import files


def reader(shared):
    b, clock, path, identity = shared
    return SharedWeightJournal(
        path,
        "DEMO-shared",
        identity,
        read_only=True,
        monotonic=clock.mono,
        wall_clock=clock.utc,
        boot=lambda: "test-boot",
    )


def test_read_only_view_reports_expired_completed_weight_without_deleting_rows(shared):
    b, clock, path, identity = shared
    clock.seconds = STARTUP_HOLD
    token = b.reserve(100, "RESCUE")
    b.finish(token, used_weight=100, status=200)
    clock.seconds += WINDOW + 0.01
    before = files(path)
    r = reader(shared)
    assert r.view()["pressure"] == 0
    with sqlite3.connect(b.path) as c:
        assert c.execute("SELECT COUNT(*) FROM attempts").fetchone() == (1,)
    assert files(path) == before


def test_read_only_view_keeps_unknown_pending_weight_and_rejects_mutations(shared):
    b, clock, path, identity = shared
    clock.seconds = STARTUP_HOLD
    token = b.reserve(300, "RESCUE")
    clock.seconds += 3600
    before = files(path)
    r = reader(shared)
    assert r.view()["pending_weight"] == 300
    for mutate in [
        lambda: r.reserve(1, "RESCUE"),
        lambda: r.finish(token, used_weight=1, status=200),
        r.stop_unknown,
    ]:
        with pytest.raises(SharedWeightStopped, match="READ_ONLY"):
            mutate()
    assert files(path) == before


def test_read_only_fault_does_not_persist_stop_flag(shared):
    b, clock, path, identity = shared
    clock.wall_delta = 3600
    before = files(path)
    with pytest.raises(SharedWeightStopped, match="CLOCK_OR_BOOT"):
        reader(shared)
    assert files(path) == before
    clock.wall_delta = 0
    assert b.view()["pending_attempts"] == 0


@pytest.mark.parametrize("missing", ["lock", "database"])
def test_read_only_open_never_recreates_missing_files(shared, missing):
    b, clock, path, identity = shared
    (b.lock_path if missing == "lock" else b.path).unlink()
    before = files(path)
    with pytest.raises(OSError):
        reader(shared)
    assert files(path) == before


def test_read_only_creation_is_rejected_before_any_file(tmp_path):
    with pytest.raises(SharedWeightStopped, match="CANNOT_CREATE"):
        SharedWeightJournal.create(tmp_path, "DEMO-new", uuid4(), read_only=True)
    assert not list(tmp_path.iterdir())
