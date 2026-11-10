"""Real SQLite backups, independent PG commits, rollback injection and processes."""

# ruff: noqa: F811
import multiprocessing
import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import DBAPIError

from integrations.binance_usdm.demo_tp_admission import TPModelProfile
from integrations.binance_usdm.demo_tp_weight_model import MODELED_TP_ROUTE_COSTS
from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo_shared_checkpoint import (
    AnchoredSharedWeightJournal,
    SharedWeightAnchorStore,
    bindings,
    checkpoints,
)
from packages.persistence.demo_shared_weight import SharedWeightStopped
from services.execution.demo_shared_weight_budget import SharedManagedOrderWindowBudget
from tests.fixtures.shared_weight_anchor import provision_anchor
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.unit.test_durable_order_quota import QuotaClock
from tests.unit.test_quota_startup_read_only import files


@pytest.fixture
def anchored(demo_system, tmp_path):
    store, c, fake = demo_system
    anchors = provision_anchor(store)
    clock = QuotaClock()
    identity = uuid4()
    opts = dict(monotonic=clock.mono, wall_clock=clock.utc, boot=lambda: "test-boot")
    w = anchors.bootstrap(tmp_path, "DEMO-anchor", identity, **opts)
    clock.seconds = 70.001
    yield w, anchors, clock, tmp_path, identity, opts, store, c
    anchors.engine.dispose()


def rows(anchors):
    with anchors.engine.connect() as c:
        return tuple(
            c.execute(select(t).order_by(*t.primary_key.columns)).all()
            for t in (bindings, checkpoints)
        )


def reopen(a, read_only=False):
    w, anchors, clock, path, identity, opts, *_ = a
    return AnchoredSharedWeightJournal(
        path, "DEMO-anchor", identity, anchors=anchors, read_only=read_only, **opts
    )


@pytest.mark.parametrize("kind", ["reservation", "completion", "stop_flag"])
def test_valid_old_sqlite_backup_is_blocked_by_fresh_process_state(anchored, kind):
    w, anchors, clock, path, identity, opts, *_ = anchored
    if kind == "reservation":
        old = w.path.read_bytes()
        w.reserve(1000, "RESCUE")
    elif kind == "completion":
        token = w.reserve(1000, "RESCUE")
        old = w.path.read_bytes()
        w.finish(token, used_weight=1000, status=200)
    else:
        token = w.reserve(1000, "RESCUE")
        w.finish(token, used_weight=1000, status=200)
        old = w.path.read_bytes()
        w.stop_unknown()
    witness = rows(anchors)
    w.path.write_bytes(old)  # Valid backup, same UUID and inode, no hash damage.
    before = files(path)
    with pytest.raises(SharedWeightStopped, match="CHECKPOINT_MISMATCH"):
        reopen(anchored)
    assert files(path) == before and rows(anchors) == witness


def test_sqlite_commit_then_pg_commit_failure_does_not_return_transport_token(anchored):
    w, anchors, *_ = anchored
    before = rows(anchors)

    def fail(connection):
        raise RuntimeError("synthetic PG commit failure")

    event.listen(anchors.engine, "commit", fail)
    try:
        with pytest.raises(RuntimeError):
            w.reserve(5700, "RESCUE")
    finally:
        event.remove(anchors.engine, "commit", fail)
    assert rows(anchors) == before
    with sqlite3.connect(w.path) as c:
        assert c.execute("SELECT SUM(weight) FROM attempts WHERE done IS NULL").fetchone() == (
            5700,
        )
    with pytest.raises(SharedWeightStopped, match="CHECKPOINT_MISMATCH"):
        reopen(anchored)


def test_postsend_checkpoint_failure_blocks_all_future_access(anchored):
    w, anchors, *_ = anchored
    calls = []

    def sender(*args):
        calls.append(True)
        return RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "1"})

    budget = SharedManagedOrderWindowBudget(
        sender, TPModelProfile(0), MODELED_TP_ROUTE_COSTS, bridge=SimpleNamespace(), shared=w
    )
    commits = [0]

    def fail_second(connection):
        commits[0] += 1
        if commits[0] == 2:
            raise RuntimeError("synthetic finish commit failure")

    event.listen(anchors.engine, "commit", fail_second)
    try:
        with pytest.raises(RuntimeError):
            budget("GET", "/fapi/v1/time", SecretStr(""), None)
    finally:
        event.remove(anchors.engine, "commit", fail_second)
    assert calls == [True] and budget.untrusted
    with pytest.raises(SharedWeightStopped, match="CHECKPOINT_MISMATCH"):
        reopen(anchored)


def rewind(anchors, scope, revision):
    # Simulate an independently restored PG backup, bypassing immutable triggers.
    with anchors.engine.begin() as c:
        c.execute(text("ALTER TABLE demo_shared_weight_checkpoints DISABLE TRIGGER USER"))
        c.execute(
            checkpoints.delete().where(
                checkpoints.c.scope == scope, checkpoints.c.revision > revision
            )
        )
        c.execute(text("ALTER TABLE demo_shared_weight_checkpoints ENABLE TRIGGER USER"))


def test_old_pg_checkpoint_cannot_launder_newer_sqlite_state(anchored):
    w, anchors, *_ = anchored
    w.reserve(1000, "RESCUE")
    rewind(anchors, "DEMO-anchor", 0)
    before = rows(anchors)
    with pytest.raises(SharedWeightStopped, match="CHECKPOINT_MISMATCH"):
        reopen(anchored)
    assert rows(anchors) == before


@pytest.mark.parametrize("table", [bindings, checkpoints])
@pytest.mark.parametrize("operation", ["update", "delete"])
def test_checkpoint_records_are_database_enforced_append_only(anchored, table, operation):
    w, anchors, *_ = anchored
    before = rows(anchors)
    with pytest.raises(DBAPIError, match="shared_weight_append_only"), anchors.engine.begin() as c:
        query = (
            table.delete()
            if operation == "delete"
            else table.update().values(policy="x")
            if table is bindings
            else table.update().values(digest="x")
        )
        c.execute(query)
    assert rows(anchors) == before


def test_read_only_validation_does_not_append_update_or_prune(anchored):
    w, anchors, clock, path, *_ = anchored
    token = w.reserve(100, "RESCUE")
    w.finish(token, used_weight=100, status=200)
    clock.seconds += 61
    before, disk = rows(anchors), files(path)
    r = reopen(anchored, read_only=True)
    assert r.view()["pressure"] == 0
    assert rows(anchors) == before and files(path) == disk


def test_existing_unbound_file_is_not_adopted_or_reset(anchored):
    w, anchors, clock, path, identity, opts, *_ = anchored
    with anchors.engine.begin() as c:
        for t in (checkpoints, bindings):
            c.execute(text(f"ALTER TABLE {t.name} DISABLE TRIGGER USER"))
            c.execute(t.delete())
            c.execute(text(f"ALTER TABLE {t.name} ENABLE TRIGGER USER"))
    before = files(path)
    with pytest.raises(SharedWeightStopped, match="CHECKPOINT_MISSING"):
        reopen(anchored)
    with pytest.raises(FileExistsError):
        anchors.bootstrap(path, "DEMO-anchor", identity, **opts)
    assert files(path) == before and rows(anchors) == ([], [])


def test_account_transaction_rollback_does_not_refund_independent_shared_reservation(anchored):
    w, anchors, clock, path, identity, opts, store, c = anchored
    before = store.read(c.lease.account_id)

    def failed(ledger):
        w.reserve(1000, "RESCUE")
        raise RuntimeError("synthetic domain effect rollback")

    with pytest.raises(RuntimeError):
        store.change(c.lease, "TEST_DOMAIN_ROLLBACK", failed)
    assert store.read(c.lease.account_id) == before
    assert reopen(anchored).view()["pending_weight"] == 1000


def test_checkpoint_history_gaps_block_even_when_latest_digest_matches(anchored):
    w, anchors, *_ = anchored
    t = w.reserve(100, "RESCUE")
    w.finish(t, used_weight=100, status=200)
    with anchors.engine.begin() as c:
        c.execute(text("ALTER TABLE demo_shared_weight_checkpoints DISABLE TRIGGER USER"))
        c.execute(
            checkpoints.delete().where(
                checkpoints.c.scope == "DEMO-anchor", checkpoints.c.revision == 1
            )
        )
        c.execute(text("ALTER TABLE demo_shared_weight_checkpoints ENABLE TRIGGER USER"))
    with pytest.raises(SharedWeightStopped, match="HISTORY_INVALID"):
        reopen(anchored)


def competitor(url, schema, path, identity, barrier, queue):
    engine = create_engine(url, connect_args={"options": "-csearch_path=" + schema})
    anchors = SharedWeightAnchorStore(engine)
    try:
        w = AnchoredSharedWeightJournal(
            Path(path),
            "DEMO-processes",
            identity,
            anchors=anchors,
            monotonic=lambda: time.monotonic() + 70.01,
            wall_clock=lambda: datetime.now(UTC) + timedelta(seconds=70.01),
        )
        barrier.wait(timeout=20)
        try:
            w.reserve(1000, "RESCUE")
            queue.put("ACCEPTED")
        except SharedWeightStopped as error:
            queue.put(str(error))
    finally:
        engine.dispose()


def test_eight_processes_share_checkpoint_and_cannot_double_spend(anchored):
    w, anchors, clock, path, *_ = anchored
    identity = uuid4()
    anchors.bootstrap(path, "DEMO-processes", identity)
    with anchors.engine.connect() as c:
        schema = c.execute(text("SELECT current_schema()")).scalar_one()
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(8)
    queue = ctx.Queue()
    workers = [
        ctx.Process(
            target=competitor,
            args=(os.environ["TEST_DATABASE_URL"], schema, str(path), identity, barrier, queue),
        )
        for _ in range(8)
    ]
    for worker in workers:
        worker.start()
    result = [queue.get(timeout=30) for _ in workers]
    for worker in workers:
        worker.join(10)
        assert worker.exitcode == 0
    assert result.count("ACCEPTED") == 5 and result.count("SHARED_WEIGHT_CAPACITY") == 3


def killed_between_commits(url, schema, path, identity):
    import signal

    engine = create_engine(url, connect_args={"options": "-csearch_path=" + schema})
    anchor = SharedWeightAnchorStore(engine)
    w = AnchoredSharedWeightJournal(
        Path(path),
        "DEMO-kill",
        identity,
        anchors=anchor,
        monotonic=lambda: time.monotonic() + 70.01,
        wall_clock=lambda: datetime.now(UTC) + timedelta(seconds=70.01),
    )

    def kill(connection):
        os.kill(os.getpid(), signal.SIGKILL)

    event.listen(engine, "commit", kill)
    w.reserve(5700, "RESCUE")
    raise AssertionError("process should be killed before reserve returns")


def test_sigkill_between_sqlite_and_pg_commit_preserves_charge_and_blocks_restart(anchored):
    import signal

    w, anchors, clock, path, *_ = anchored
    identity = uuid4()
    original = anchors.bootstrap(path, "DEMO-kill", identity)
    with anchors.engine.connect() as c:
        schema = c.execute(text("SELECT current_schema()")).scalar_one()
    child = multiprocessing.get_context("spawn").Process(
        target=killed_between_commits,
        args=(os.environ["TEST_DATABASE_URL"], schema, str(path), identity),
    )
    child.start()
    child.join(25)
    assert child.exitcode == -signal.SIGKILL
    with sqlite3.connect(original.path) as c:
        assert c.execute("SELECT SUM(weight) FROM attempts WHERE done IS NULL").fetchone() == (
            5700,
        )
    with pytest.raises(SharedWeightStopped, match="CHECKPOINT_MISMATCH"):
        AnchoredSharedWeightJournal(
            path,
            "DEMO-kill",
            identity,
            anchors=anchors,
            monotonic=lambda: time.monotonic() + 70.01,
            wall_clock=lambda: datetime.now(UTC) + timedelta(seconds=70.01),
        )
