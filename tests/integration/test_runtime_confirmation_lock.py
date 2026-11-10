"""Lost order-journal ownership stops scheduling without repairing its records."""

# ruff: noqa: F811
import asyncio
import os

import pytest

from integrations.binance_usdm.demo_tp_window_budget import ProofDeferred
from packages.persistence.demo_order_quota import LocalQuotaStopped
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_managed_order_quota import managed  # noqa: F401
from tests.integration.test_managed_runtime_assembly import assembly  # noqa: F401
from tests.integration.test_quota_startup_review import snapshot
from tests.integration.test_runtime_confirmation_deadline import scheduler
from tests.unit.test_quota_startup_read_only import files


def lose_lock(q, fault):
    backup = q.lock_path.with_suffix(".held-backup")
    if fault == "closed":
        q.close()
        return lambda: None
    os.replace(q.lock_path, backup)
    if fault == "replaced":
        q.lock_path.write_bytes(b"synthetic replacement")
        q.lock_path.chmod(0o600)

    def restore():
        if q.lock_path.exists():
            q.lock_path.unlink()
        os.replace(backup, q.lock_path)

    return restore


@pytest.mark.parametrize("fault", ["closed", "missing", "replaced"])
@pytest.mark.parametrize("timing", ["before", "waiting"])
def test_lost_order_lock_stops_before_more_scheduler_work(assembly, monkeypatch, fault, timing):
    session, c, state = scheduler(assembly, monkeypatch, "normal")

    def restore():
        return None

    captured = []

    def inject():
        nonlocal restore
        restore = lose_lock(session.journal, fault)
        assert not session.journal.owns_process_lock()
        captured.append(
            (snapshot(assembly[0]), files(assembly[8].order_directory), list(state["calls"]))
        )

    if timing == "before":
        inject()
    else:
        attempted = []

        def require(cost):
            state["calls"].append("admit")
            if not attempted:
                attempted.append(True)
                raise ProofDeferred

        monkeypatch.setattr(session.budget, "require_proof", require)
        pause = c._confirmation_sleep

        async def wait(delay):
            await pause(delay)
            inject()

        c._confirmation_sleep = wait
    try:
        with pytest.raises(LocalQuotaStopped, match="ORDER_LOCK"):
            asyncio.run(c.confirm_exit_budgeted())
        domain, disk, calls = captured[0]
        assert snapshot(assembly[0]) == domain and files(assembly[8].order_directory) == disk
        assert state["calls"] == calls and c.budgeted_flat_confirmation == "UNKNOWN"
    finally:
        restore()
        session.close()


@pytest.mark.parametrize("fault", ["owner", "fence", "expired"])
def test_original_postgres_fence_rejects_leader_loss_after_resource_wait(
    assembly, monkeypatch, fault
):
    from datetime import timedelta
    from uuid import uuid4

    from sqlalchemy import update

    from packages.persistence.demo import DemoStore, accounts
    from services.risk.paper import RiskRejected

    session, c, state = scheduler(assembly, monkeypatch, "normal")
    flow = uuid4()
    assembly[3].seconds += 70.01
    c.quota_bridge.reserve(flow)

    def renew(lease):
        state["calls"].append("renew")
        DemoStore.renew(c.store, lease)

    monkeypatch.setattr(c.store, "renew", renew)
    tried = []

    def require(cost):
        state["calls"].append("admit")
        if not tried:
            tried.append(True)
            raise ProofDeferred

    monkeypatch.setattr(session.budget, "require_proof", require)
    saved = []
    pause = c._confirmation_sleep

    async def wait(delay):
        await pause(delay)
        values = (
            {"owner": str(uuid4())}
            if fault == "owner"
            else {"fence": c.lease.fence + 1}
            if fault == "fence"
            else {"lease_until": assembly[0].now() - timedelta(seconds=1)}
        )
        with assembly[0].engine.begin() as connection:
            connection.execute(
                update(accounts).where(accounts.c.account_id == c.lease.account_id).values(**values)
            )
        saved.append((snapshot(assembly[0]), files(assembly[8].order_directory)))

    c._confirmation_sleep = wait
    try:
        with pytest.raises(RiskRejected):
            asyncio.run(c.confirm_exit_budgeted())
        assert state["calls"] == ["renew", "admit", "sleep", "renew"]
        assert snapshot(assembly[0]) == saved[0][0]
        assert files(assembly[8].order_directory) == saved[0][1]
        assert session.journal.flow_records()[str(flow)]["seal"] is None
        assert session.journal.view()["slots_60s"] == 6
        assert c.budgeted_flat_confirmation == "UNKNOWN"
    finally:
        session.close()


def test_database_file_loss_is_not_misreported_as_process_lock_loss(assembly, monkeypatch):
    session, c, state = scheduler(assembly, monkeypatch, "normal")
    q = session.journal
    backup = q.path.with_suffix(".database-backup")
    os.replace(q.path, backup)
    try:
        assert q.owns_process_lock()
        # Scheduling stubs only: real proof/exit journal-integrity checks are
        # unchanged. This must not invent financial flatness or broaden a check.
        assert asyncio.run(c.confirm_exit_budgeted()) == "UNKNOWN"
        assert state["calls"].count("proof") == 4
        assert c.confirmation_diagnostics()["binding_rejections"] == 0
    finally:
        os.replace(backup, q.path)
        session.close()


def test_closed_lock_refusal_preserves_precommitted_unsealed_flow(assembly, monkeypatch):
    from uuid import uuid4

    from packages.persistence.demo_order_quota import DurableOrderQuota

    session, c, state = scheduler(assembly, monkeypatch, "normal")
    flow = uuid4()
    assembly[3].seconds += 70.01
    c.quota_bridge.reserve(flow)
    q = session.journal
    records = q.flow_records()
    q.close()
    before = snapshot(assembly[0]), files(assembly[8].order_directory)
    try:
        with pytest.raises(LocalQuotaStopped, match="ORDER_LOCK"):
            asyncio.run(c.confirm_exit_budgeted())
        assert not state["calls"] and snapshot(assembly[0]) == before[0]
        assert files(assembly[8].order_directory) == before[1]
        reader = DurableOrderQuota(
            assembly[8].order_directory,
            c.lease.account_id,
            q.journal_id,
            read_only=True,
            monotonic=assembly[3].mono,
            wall_clock=assembly[3].utc,
        )
        try:
            assert reader.flow_records() == records and records[str(flow)]["seal"] is None
        finally:
            reader.close()
    finally:
        session.close()


def test_worker_detects_lock_loss_after_async_dispatch(assembly, monkeypatch):
    from services.execution import demo_runtime_confirmation as confirmation

    session, c, state = scheduler(assembly, monkeypatch, "normal")
    original = confirmation.settled_call

    async def dispatch(function, *args):
        session.journal.close()
        return await original(function, *args)

    monkeypatch.setattr(confirmation, "settled_call", dispatch)
    try:
        with pytest.raises(LocalQuotaStopped, match="ORDER_LOCK"):
            asyncio.run(c.confirm_exit_budgeted())
        assert not state["calls"]
    finally:
        session.close()
