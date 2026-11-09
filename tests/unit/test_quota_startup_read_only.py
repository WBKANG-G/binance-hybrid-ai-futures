"""Real read-only SQLite and flock checks; no credentials or network."""

import hashlib
import sqlite3
from uuid import uuid4

import pytest

from packages.persistence.demo_order_quota import DurableOrderQuota, LocalQuotaStopped
from tests.unit.test_durable_order_quota import QuotaClock, roles


def files(directory):
    return {
        str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
        if p.is_file()
        else "DIRECTORY"
        for p in directory.rglob("*")
    }


def test_read_only_open_never_starts_session_or_modifies_files(tmp_path):
    identity = uuid4()
    writer = DurableOrderQuota.create(tmp_path, "DEMO-test", identity)
    writer.start_session(uuid4(), 1)
    writer.close()
    before = files(tmp_path)
    reader = DurableOrderQuota(tmp_path, "DEMO-test", identity, read_only=True)
    try:
        assert reader.session_id is None and not reader.owns_process_lock()
        assert reader.view()["slots_10s"] is None
        with pytest.raises(LocalQuotaStopped, match="READ_ONLY"):
            reader.start_session(uuid4(), 2)
        with pytest.raises(LocalQuotaStopped, match="READ_ONLY"):
            reader._append("SESSION", "forbidden", {})
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.connection.execute("INSERT INTO events DEFAULT VALUES")
        assert reader.session_id is None
        assert files(tmp_path) == before
    finally:
        reader.close()
    assert files(tmp_path) == before


def test_two_readers_share_lock_and_exclude_writer(tmp_path):
    identity = uuid4()
    writer = DurableOrderQuota.create(tmp_path, "DEMO-test", identity)
    writer.close()
    a = DurableOrderQuota(tmp_path, "DEMO-test", identity, read_only=True)
    b = DurableOrderQuota(tmp_path, "DEMO-test", identity, read_only=True)
    try:
        with pytest.raises(LocalQuotaStopped, match="BUSY"):
            DurableOrderQuota(tmp_path, "DEMO-test", identity)
    finally:
        a.close()
        b.close()
    DurableOrderQuota(tmp_path, "DEMO-test", identity).close()


def test_active_writer_excludes_reader_without_changing_journal(tmp_path):
    identity = uuid4()
    writer = DurableOrderQuota.create(tmp_path, "DEMO-test", identity)
    before = files(tmp_path)
    try:
        with pytest.raises(LocalQuotaStopped, match="BUSY"):
            DurableOrderQuota(tmp_path, "DEMO-test", identity, read_only=True)
        assert files(tmp_path) == before
        assert writer.owns_process_lock()
    finally:
        writer.close()


@pytest.mark.parametrize("missing", ["all", "lock", "journal"])
def test_missing_storage_is_never_created_by_read_only_open(tmp_path, missing):
    identity = uuid4()
    if missing != "all":
        writer = DurableOrderQuota.create(tmp_path, "DEMO-test", identity)
        path = writer.lock_path if missing == "lock" else writer.path
        writer.close()
        path.unlink()
    before = files(tmp_path)
    with pytest.raises((LocalQuotaStopped, OSError)):
        DurableOrderQuota(tmp_path, "DEMO-test", identity, read_only=True)
    assert files(tmp_path) == before


def test_read_only_bootstrap_is_rejected_before_files_are_created(tmp_path):
    with pytest.raises(LocalQuotaStopped, match="CANNOT_CREATE"):
        DurableOrderQuota.create(tmp_path, "DEMO-test", uuid4(), read_only=True)
    assert not list(tmp_path.iterdir())


def test_reader_preserves_pending_claim_and_held_credit(tmp_path):
    clock = QuotaClock()
    identity, flow = uuid4(), uuid4()
    writer = DurableOrderQuota.create(
        tmp_path, "DEMO-test", identity, monotonic=clock.mono, wall_clock=clock.utc
    )
    writer.start_session(uuid4(), 1)
    clock.seconds = 70.001
    writer.reserve(flow, roles())
    writer.claim(flow, "ENTRY", "DE-a")
    writer.close()
    reader = DurableOrderQuota(tmp_path, "DEMO-test", identity, read_only=True)
    try:
        state = reader.flow_records()[str(flow)]
        assert set(state["claims"]) == {"ENTRY"} and state["seal"] is None
        assert reader.view()["retained_model_slots_60s"] == 6
        assert reader.view()["slots_60s"] is None
    finally:
        reader.close()
