"""Real PG row contention must not be mistaken for an active-lease rejection."""

import asyncio
import threading
import time
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from packages.persistence.demo import accounts
from services.execution import demo_protected_worker_check as module
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401


class LiveChild:
    def poll(self):
        return None


def hold_row(store, seconds):
    entered = threading.Event()

    def hold():
        with store.engine.begin() as conn:
            conn.execute(
                select(accounts).where(accounts.c.account_id == "DEMO-test").with_for_update()
            ).one()
            entered.set()
            time.sleep(seconds)

    thread = threading.Thread(target=hold)
    thread.start()
    assert entered.wait(5)
    return thread


def test_original_single_acquire_reproduces_sqlstate_55p03(case):  # noqa: F811
    store, old, _, _ = case
    before = module.ownership(store, "DEMO-test")
    thread = hold_row(store, 2.6)
    try:
        with pytest.raises(OperationalError) as error:
            store.acquire("DEMO-test", uuid4())
        assert getattr(error.value.orig, "sqlstate", None) == "55P03"
    finally:
        thread.join(5)
    assert module.ownership(store, "DEMO-test") == before
    store.release(old.lease)


def test_bounded_probe_waits_for_real_rejection_without_mutating_owner_or_ledger(case):  # noqa: F811
    store, old, _, _ = case
    before = module.ownership(store, "DEMO-test")
    counts = {"attempts": 0, "lock_timeouts": 0}
    thread = hold_row(store, 2.6)
    try:
        asyncio.run(module.reject_competing_leader(store, old.lease, LiveChild(), counts))
    finally:
        thread.join(5)
    assert counts == {"attempts": 2, "lock_timeouts": 1}
    assert module.ownership(store, "DEMO-test") == before
    store.release(old.lease)


def test_continuing_contention_never_counts_as_rejected_leader(case):  # noqa: F811
    store, old, _, _ = case
    before = module.ownership(store, "DEMO-test")
    counts = {"attempts": 0, "lock_timeouts": 0}
    thread = hold_row(store, 2.6)
    try:
        with pytest.raises(module.CheckBlocked, match="COMPETING_LEADER_PROBE_BUSY"):
            asyncio.run(
                module.reject_competing_leader(store, old.lease, LiveChild(), counts, timeout=0.1)
            )
    finally:
        thread.join(5)
    assert counts == {"attempts": 1, "lock_timeouts": 1}
    assert module.ownership(store, "DEMO-test") == before
    store.release(old.lease)


def test_successful_competing_acquisition_still_rejected(case, monkeypatch):  # noqa: F811
    store, old, _, _ = case
    real_acquire = store.acquire
    real_release = store.release
    released = []

    def illegal_acquire(account, owner):
        # Inject the forbidden outcome *after* the pre-check, using real PG.
        real_release(old.lease)
        return real_acquire(account, owner)

    def record_release(lease):
        released.append(lease)
        return real_release(lease)

    monkeypatch.setattr(store, "acquire", illegal_acquire)
    monkeypatch.setattr(store, "release", record_release)
    with pytest.raises(module.CheckBlocked, match="EARLY_TAKEOVER_SUCCEEDED"):
        asyncio.run(
            module.reject_competing_leader(
                store, old.lease, LiveChild(), {"attempts": 0, "lock_timeouts": 0}
            )
        )
    assert len(released) == 1
    assert module.ownership(store, "DEMO-test")["owner"] is None


@pytest.mark.parametrize("mode", ["child_exited", "owner_changed", "lease_expired"])
def test_untrusted_child_or_lease_never_probes(case, monkeypatch, mode):  # noqa: F811
    store, old, _, _ = case
    child = LiveChild()
    row = module.ownership(store, "DEMO-test")
    if mode == "child_exited":
        monkeypatch.setattr(child, "poll", lambda: 0)
    elif mode == "owner_changed":
        row["owner"] = str(uuid4())
    else:
        row["lease_until"] = store.now()
    monkeypatch.setattr(module, "ownership", lambda *_: row)
    counts = {"attempts": 0, "lock_timeouts": 0}
    with pytest.raises(module.CheckBlocked):
        asyncio.run(module.reject_competing_leader(store, old.lease, child, counts))
    assert counts == {"attempts": 0, "lock_timeouts": 0}
    store.release(old.lease)


@pytest.mark.parametrize("sqlstate", ["08006", "40P01", None])
def test_other_database_failures_not_retried(case, monkeypatch, sqlstate):  # noqa: F811
    store, old, _, _ = case

    class OriginalError(Exception):
        pass

    raw = OriginalError("PRIVATE_EXAMPLE")
    raw.sqlstate = sqlstate

    def fail(*_):
        raise OperationalError("REDACTED", {}, raw)

    monkeypatch.setattr(store, "acquire", fail)
    counts = {"attempts": 0, "lock_timeouts": 0}
    with pytest.raises(OperationalError):
        asyncio.run(module.reject_competing_leader(store, old.lease, LiveChild(), counts))
    assert counts == {"attempts": 1, "lock_timeouts": 0}
    store.release(old.lease)
