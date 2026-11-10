"""Assembly/teardown ownership with real PostgreSQL and both real local stores."""

# ruff: noqa: F811
import asyncio
from uuid import uuid4

import pytest

from integrations.binance_usdm.demo import DemoClient, transport
from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo_order_quota import DurableOrderQuota, LocalQuotaStopped
from packages.persistence.demo_shared_weight import SharedWeightJournal
from services.execution import demo_managed_runtime as runtime
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_managed_order_quota import managed  # noqa: F401
from tests.integration.test_quota_startup_review import snapshot
from tests.unit.test_quota_startup_read_only import files


@pytest.fixture
def assembly(managed):
    store, c, bridge, proxy, q, clock, path = managed
    q.close()

    def isolated(ledger):
        ledger.margin_mode = "ISOLATED"
        ledger.symbols = ("BTCUSDT",)

    store.change(c.lease, "TEST_ISOLATED_SCOPE", isolated)
    folder = path / "shared"
    folder.mkdir(mode=0o700)
    identity = uuid4()
    w = SharedWeightJournal.create(
        folder,
        "DEMO-weight",
        identity,
        monotonic=clock.mono,
        wall_clock=clock.utc,
        boot=lambda: "test-boot",
    )
    clock.seconds += 70.01
    cfg = c.client.settings.model_copy(
        update={"demo_trading_enabled": False, "margin_mode": "ISOLATED", "symbols": ("BTCUSDT",)}
    )
    source = DemoClient(cfg, c.client.credentials, send=c.client.send, clock_ms=c.client.clock_ms)
    source.offset, source.synced_at = c.client.offset, c.client.synced_at
    calls = []

    def sender(method, path, query, key):
        calls.append((method, path))
        return RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "1"})

    storage = runtime.RuntimeStorage(path, folder, "DEMO-weight", identity)
    clocks = runtime.RuntimeClocks(clock.mono, clock.utc, lambda: "test-boot")
    return store, c, q, clock, w, source, sender, calls, storage, clocks


def open_session(a, **kwargs):
    store, c, q, clock, w, source, sender, calls, storage, clocks = a
    return runtime.assemble_managed_runtime(
        store, c.lease, source, sender, storage, clocks=clocks, **kwargs
    )


def test_default_refusal_precedes_any_database_or_filesystem_access(monkeypatch):
    def forbidden(*args):
        raise AssertionError("Unexpected state access")

    monkeypatch.setattr(runtime, "_current_ledger", forbidden)
    with pytest.raises(LocalQuotaStopped, match="REAL_EXECUTION_DISABLED"):
        runtime.assemble_managed_runtime(None, None, None, None, None)


def test_success_owns_cloned_client_and_close_revokes_before_any_new_charge(assembly):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    session = open_session(assembly, allow_synthetic_model_execution=True)
    assert session.coordinator.client is not source
    assert session.coordinator.client.send is session.budget
    assert source.send is c.client.send and not source.settings.demo_trading_enabled
    assert not calls and session.journal.owns_process_lock()
    session.close()
    session.close()
    before, disk = snapshot(store), files(storage.order_directory)
    with pytest.raises(LocalQuotaStopped, match="CLOSED"):
        session.coordinator.client.sync_clock()
    with pytest.raises(LocalQuotaStopped, match="CLOSED"):
        session.budget.require_proof(1)
    assert not calls and snapshot(store) == before and files(storage.order_directory) == disk
    assert not session.journal.owns_process_lock()


@pytest.mark.parametrize(
    "kind",
    [
        "pending_weight",
        "pending_order",
        "missing_weight",
        "wrong_uuid",
        "wrong_fence",
        "period",
        "live",
    ],
)
def test_refusal_preserves_both_stores_and_database(assembly, kind, monkeypatch):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    if kind == "pending_weight":
        w.reserve(1, "RESCUE")
    if kind == "pending_order":
        journal = DurableOrderQuota(
            storage.order_directory,
            c.lease.account_id,
            q.journal_id,
            monotonic=clock.mono,
            wall_clock=clock.utc,
        )
        from services.execution.demo_managed_order_quota import ManagedQuotaBridge

        b = ManagedQuotaBridge(store, c.lease, journal)
        clock.seconds += 70.01
        b.reserve(uuid4())
        journal.close()
    if kind == "missing_weight":
        w.path.unlink()
    if kind == "wrong_uuid":
        assembly = list(assembly)
        assembly[8] = runtime.RuntimeStorage(
            storage.order_directory, storage.shared_directory, storage.shared_scope, uuid4()
        )
    if kind == "wrong_fence":
        import copy

        assembly = list(assembly)
        wrong = copy.copy(c)
        wrong.lease = c.lease.model_copy(update={"fence": c.lease.fence + 1})
        assembly[1] = wrong
    if kind == "period":
        store.change(
            c.lease, "TEST_BAD_DAY", lambda ledger: setattr(ledger, "daily_key", "2026-01-01")
        )
    if kind == "live":
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    before, disk = snapshot(store), files(storage.order_directory)
    with pytest.raises(LocalQuotaStopped):
        open_session(assembly, allow_synthetic_model_execution=True)
    assert not calls and snapshot(store) == before and files(storage.order_directory) == disk


def test_standard_transport_refused_before_database(assembly):
    a = list(assembly)
    a[6] = transport
    with pytest.raises(LocalQuotaStopped, match="SYNTHETIC_CONTEXT"):
        open_session(a, allow_synthetic_model_execution=True)


def test_constructor_failure_releases_order_lock_without_erasing_session(assembly, monkeypatch):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly

    def failed(*a, **k):
        raise ValueError("private-path secret-key")

    monkeypatch.setattr(runtime, "OwnedSharedBudget", failed)
    with pytest.raises(LocalQuotaStopped, match="ASSEMBLY_FAILED") as e:
        open_session(assembly, allow_synthetic_model_execution=True)
    assert "private-path" not in str(e.value) and not calls
    reader = DurableOrderQuota(
        storage.order_directory, c.lease.account_id, q.journal_id, read_only=True
    )
    assert reader.flow_records() == {}
    reader.close()


def test_single_use_and_cancellation_disable_client_and_preserve_records(assembly, monkeypatch):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    session = open_session(assembly, allow_synthetic_model_execution=True)
    clock.seconds += 70.01

    async def fake_run(coordinator, operation):
        assert coordinator.client.settings.demo_trading_enabled
        raise asyncio.CancelledError()

    monkeypatch.setattr(runtime, "run_managed_quota_candidate", fake_run)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(session.run(uuid4()))
    assert not session.coordinator.client.settings.demo_trading_enabled
    with pytest.raises(LocalQuotaStopped, match="SINGLE_USE"):
        asyncio.run(session.run(uuid4()))
    session.close()
    assert not calls and w.view()["pending_attempts"] == 0


def test_close_rejected_while_running_and_local_cleanup_retry_is_safe(assembly, monkeypatch):
    session = open_session(assembly, allow_synthetic_model_execution=True)
    assembly[3].seconds += 70.01

    async def fake_run(coordinator, operation):
        with pytest.raises(LocalQuotaStopped, match="STILL_RUNNING"):
            session.close()
        return {"synthetic": True}

    monkeypatch.setattr(runtime, "run_managed_quota_candidate", fake_run)
    assert asyncio.run(session.run(uuid4())) == {"synthetic": True}
    original = session.journal.close

    def failed():
        raise OSError("synthetic close failure")

    monkeypatch.setattr(session.journal, "close", failed)
    with pytest.raises(OSError):
        session.close()
    assert session.budget.revoked
    monkeypatch.setattr(session.journal, "close", original)
    session.close()
    assert not session.journal.owns_process_lock()


def test_failure_close_preserves_unsealed_reservation_and_blocks_next_assembly(
    assembly, monkeypatch
):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    session = open_session(assembly, allow_synthetic_model_execution=True)
    clock.seconds += 70.01

    async def failed(coordinator, operation):
        coordinator.quota_bridge.reserve(operation)
        raise TimeoutError("synthetic ambiguous session failure")

    monkeypatch.setattr(runtime, "run_managed_quota_candidate", failed)
    with pytest.raises(TimeoutError):
        asyncio.run(session.run(uuid4()))
    session.close()
    before, disk = snapshot(store), files(storage.order_directory)
    with pytest.raises(LocalQuotaStopped, match="PENDING_ORDER_FLOW"):
        open_session(assembly, allow_synthetic_model_execution=True)
    assert snapshot(store) == before and files(storage.order_directory) == disk and not calls
    reader = DurableOrderQuota(
        storage.order_directory, c.lease.account_id, q.journal_id, read_only=True
    )
    assert len(reader.flow_records()) == 1
    assert reader.view()["retained_model_slots_60s"] == 6
    reader.close()


def test_lease_lost_after_assembly_is_rechecked_before_http(assembly, monkeypatch):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    session = open_session(assembly, allow_synthetic_model_execution=True)
    clock.seconds += 70.01
    store.release(c.lease)
    before, disk = snapshot(store), files(storage.order_directory)
    with pytest.raises(LocalQuotaStopped, match="CURRENT_LEASE"):
        asyncio.run(session.run(uuid4()))
    assert not calls and not session.coordinator.client.settings.demo_trading_enabled
    assert snapshot(store) == before and files(storage.order_directory) == disk
    session.close()


def test_period_change_after_assembly_stops_before_http(assembly):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    session = open_session(assembly, allow_synthetic_model_execution=True)
    clock.seconds += 70.01
    store.change(
        c.lease, "TEST_PERIOD_CHANGE", lambda ledger: setattr(ledger, "daily_key", "2026-01-01")
    )
    before, disk = snapshot(store), files(storage.order_directory)
    with pytest.raises(LocalQuotaStopped, match="PERIOD_KEYS"):
        asyncio.run(session.run(uuid4()))
    assert not calls and snapshot(store) == before and files(storage.order_directory) == disk
    session.close()
