"""Real PostgreSQL checkpoints and immutable history; simulated server start for units."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from services.execution import demo_empty_db_restart as module
from services.execution.demo_recovery import acknowledge_flat_emergency, review
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_cancelled_correction import correction  # noqa: F401
from tests.integration.test_demo_day_reconstruction import day_case  # noqa: F401
from tests.integration.test_demo_period_review import period_case  # noqa: F401
from tests.integration.test_demo_recovery_batches import history_case  # noqa: F401


@pytest.fixture
def case(history_case):  # noqa: F811
    store, c, fake = history_case
    report = review(store, c.client, store.read("DEMO-test"))
    acknowledge_flat_emergency(
        store, c.lease, c.client, uuid4(), report["review_token"], acknowledge_unresolved=True
    )
    store.release(c.lease)
    return store, c.client, fake


@pytest.fixture
def started(monkeypatch):
    value = [datetime(2026, 10, 31, 14, tzinfo=UTC)]
    monkeypatch.setattr(module, "server_started", lambda store: value[0].isoformat())
    return value


def phase(case, operation, *, prepare):
    store, client, _ = case
    lease = store.acquire("DEMO-test", uuid4())
    try:
        return module.check(store, lease, client, operation, prepare=prepare)
    finally:
        store.release(lease)
        assert module.released(store, lease)


def test_prepare_then_changed_server_start_preserves_all_ledger_history(case, started):
    store, _, fake = case
    operation = uuid4()
    before = store.read("DEMO-test")
    count = len(fake.calls)
    prepared = phase(case, operation, prepare=True)
    assert prepared["db_restart_check"] == "PREPARED_EMPTY_NO_ORDERS"
    assert not prepared["postgres_restart_observed"]
    started[0] += timedelta(seconds=1)
    result = phase(case, operation, prepare=False)
    assert result["db_restart_check"] == "EMPTY_DB_RESTART_HISTORY_PRESERVED_VERIFIED"
    assert result["postgres_restart_observed"] and result["exchange_empty_verified"]
    assert result["fence_current"] == result["fence_before"] + 1
    after = store.read("DEMO-test")
    assert module.history_digest(before, operation) == module.history_digest(after, operation)
    assert after.requests == before.requests and len(after.requests) == 31
    assert after.paused and after.risk_state.value == "NORMAL"
    assert all(m == "GET" for m, p, fields in fake.calls[count:])


def test_same_server_start_cannot_be_claimed_as_restart(case, started):
    op = uuid4()
    phase(case, op, prepare=True)
    with pytest.raises(module.DBRestartBlocked, match="POSTGRES_RESTART_NOT_OBSERVED"):
        phase(case, op, prepare=False)


def test_missing_checkpoint_is_not_recreated(case, started):
    with pytest.raises(module.DBRestartBlocked, match="CHECKPOINT_MISSING"):
        phase(case, uuid4(), prepare=False)


def test_prepare_and_completed_verify_cannot_be_repeated(case, started):
    op = uuid4()
    phase(case, op, prepare=True)
    with pytest.raises(module.DBRestartBlocked, match="OPERATION_ALREADY_EXISTS"):
        phase(case, op, prepare=True)


def test_baseline_change_blocks_without_reset(case, started):
    store, _, _ = case
    op = uuid4()
    phase(case, op, prepare=True)
    lease = store.acquire("DEMO-test", uuid4())
    store.change(
        lease,
        "TEST_BASELINE_CHANGE",
        lambda ledger: setattr(ledger, "daily_base", ledger.daily_base + Decimal("1")),
    )
    store.release(lease)
    started[0] += timedelta(seconds=1)
    with pytest.raises(module.DBRestartBlocked, match="HISTORY_CHANGED"):
        phase(case, op, prepare=False)
    assert module.tags(op)[1] not in store.read("DEMO-test").operations


def test_intervening_leader_blocks_even_if_history_unchanged(case, started):
    store, _, _ = case
    op = uuid4()
    phase(case, op, prepare=True)
    lease = store.acquire("DEMO-test", uuid4())
    store.release(lease)
    started[0] += timedelta(seconds=1)
    with pytest.raises(module.DBRestartBlocked, match="FENCE_CHANGED"):
        phase(case, op, prepare=False)


def test_remote_cash_change_blocks_without_ledger_rewrite(case, started):
    store, _, fake = case
    op = uuid4()
    phase(case, op, prepare=True)
    before = store.read("DEMO-test")
    started[0] += timedelta(seconds=1)
    fake.wallet += Decimal("1")
    with pytest.raises(module.DBRestartBlocked, match="SNAPSHOT_CHANGED"):
        phase(case, op, prepare=False)
    assert module.history_digest(before, op) == module.history_digest(store.read("DEMO-test"), op)


def test_writable_client_cannot_prepare(case, started):
    _, client, _ = case
    client.settings = client.settings.model_copy(update={"demo_trading_enabled": True})
    with pytest.raises(module.DBRestartBlocked, match="READ_ONLY_CONTEXT_REQUIRED"):
        phase(case, uuid4(), prepare=True)


def test_nonempty_local_ledger_cannot_prepare(case, started):
    store, _, _ = case
    lease = store.acquire("DEMO-test", uuid4())
    store.change(
        lease,
        "TEST_NONEMPTY",
        lambda ledger: ledger.positions.update({"BTCUSDT": Decimal("0.001")}),
    )
    store.release(lease)
    with pytest.raises(module.DBRestartBlocked, match="EMPTY_NORMAL_PAUSED_REQUIRED"):
        phase(case, uuid4(), prepare=True)


def test_postgres_timestamp_reader_is_real_timezone_aware(case):
    store, _, _ = case
    stamp = datetime.fromisoformat(module.server_started(store))
    assert stamp.tzinfo is not None


def test_verified_operation_cannot_be_replayed_as_new_success(case, started):
    op = uuid4()
    phase(case, op, prepare=True)
    started[0] += timedelta(seconds=1)
    phase(case, op, prepare=False)
    with pytest.raises(module.DBRestartBlocked, match="OPERATION_ALREADY_EXISTS"):
        phase(case, op, prepare=False)


def test_invalid_checkpoint_never_exports_raw_marker(case, started):
    store, _, _ = case
    op = uuid4()
    phase(case, op, prepare=True)
    lease = store.acquire("DEMO-test", uuid4())
    store.change(
        lease,
        "TEST_INVALID_CHECKPOINT",
        lambda ledger: ledger.operations.update({module.tags(op)[0]: "PRIVATE_URL"}),
    )
    store.release(lease)
    with pytest.raises(module.DBRestartBlocked, match="CHECKPOINT_INVALID") as blocked:
        phase(case, op, prepare=False)
    assert "PRIVATE_URL" not in str(blocked.value)


def test_remote_exposure_cannot_prepare(case, started, monkeypatch):
    original = module.operational_snapshot

    def exposure(*args):
        return original(*args).model_copy(update={"positions": {"BTCUSDT": Decimal("0.001")}})

    monkeypatch.setattr(module, "operational_snapshot", exposure)
    with pytest.raises(module.DBRestartBlocked, match="EXCHANGE_NOT_EMPTY"):
        phase(case, uuid4(), prepare=True)


def test_server_restart_during_checkpoint_cannot_leave_prepared_marker(case, monkeypatch):
    store, _, _ = case
    op = uuid4()
    old = datetime(2026, 10, 31, 14, tzinfo=UTC)
    calls = []

    def started(store):
        calls.append(1)
        return (old + timedelta(seconds=len(calls))).isoformat()

    monkeypatch.setattr(module, "server_started", started)
    with pytest.raises(module.DBRestartBlocked, match="POSTGRES_RESTART_NOT_OBSERVED"):
        phase(case, op, prepare=True)
    assert module.tags(op)[0] not in store.read("DEMO-test").operations
