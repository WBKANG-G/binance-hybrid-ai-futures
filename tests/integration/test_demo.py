"""PostgreSQL + synthetic HTTPS contract injection. Actual DEMO checks are separate."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import create_engine, text

from integrations.binance_usdm.demo import DemoClient, DemoCredentials, DemoFailure, DemoSettings
from packages.domain.contracts import OrderState, PositionState, RiskState
from packages.persistence.demo import DemoStore
from services.execution.demo import DemoCoordinator
from services.execution.demo_models import DemoLedger
from services.risk.paper import AccountValue, DemoPolicy, RiskRejected, period_keys
from tests.fixtures.demo import FakeDemo
from tests.integration.test_paper import signal, snapshot

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required")
NOW = datetime(2026, 10, 19, 7, tzinfo=UTC)


def settings():
    raw = yaml.safe_load(Path("configs/paper-sandbox.example.yaml").read_text())
    raw["mode"] = "DEMO"
    return DemoSettings(policy=DemoPolicy.model_validate(raw), demo_trading_enabled=True)


@pytest.fixture
def system():
    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("PostgreSQL required")
    url = os.environ["TEST_DATABASE_URL"]
    admin = create_engine(url)
    schema = "phase4_" + uuid4().hex
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"}, pool_size=8)
    with engine.begin() as conn:
        cfg = Config("alembic.ini")
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    fake = FakeDemo(NOW)
    store = DemoStore(engine, clock=lambda: fake.at)
    day, week = period_keys(NOW, 0)
    store.create(
        "DEMO-test",
        DemoLedger(
            policy=settings().policy,
            value=AccountValue(as_of=NOW, initial="1"),
            daily_base="1",
            weekly_base="1",
            high_water="1",
            daily_key=day,
            weekly_key=week,
        ),
    )
    lease = store.acquire("DEMO-test", uuid4())
    client = DemoClient(
        settings(),
        DemoCredentials(SecretStr("synthetic-key"), SecretStr("synthetic-secret")),
        send=fake,
        clock_ms=lambda: int(fake.at.timestamp() * 1000),
    )
    client.sync_clock()
    coordinator = DemoCoordinator(store, lease, client)
    coordinator.refresh_metadata()
    coordinator.connected()
    yield store, coordinator, fake
    engine.dispose()
    with admin.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


def enter(system):
    store, c, fake = system
    sig = signal(environment="DEMO")
    order = c.reserve(sig, snapshot())
    c.submit(str(sig.signal_id))
    return sig, order


def posts(fake, path="/fapi/v1/order"):
    return [p for m, s, p in fake.calls if m == "POST" and s == path]


def test_demo_lost_response_is_queried_without_resubmit(system):
    store, c, fake = system
    fake.lost_submit = True
    sig, o = enter(system)
    c.submit(str(sig.signal_id))
    c.reconcile()
    assert len(posts(fake)) == 1
    assert store.read("DEMO-test").orders[str(sig.signal_id)].state == OrderState.ACKNOWLEDGED


def test_not_found_after_unknown_preserves_full_reservation(system):
    store, c, fake = system
    fake.lost_submit = True
    fake.hidden_order = True
    sig, o = enter(system)
    state = store.read("DEMO-test")
    assert not state.reconciled and state.orders[str(sig.signal_id)].remaining == o.quantity
    assert state.orders[str(sig.signal_id)].state == OrderState.UNKNOWN
    c.submit(str(sig.signal_id))
    assert len(posts(fake)) == 1


def test_partial_fill_immediately_has_separate_verified_stop_and_tp(system):
    store, c, fake = system
    sig, o = enter(system)
    qty = (o.quantity * D(".3") // D(".001")) * D(".001")
    fake.fill(o.client_id, qty)
    c.reconcile()
    order = store.read("DEMO-test").orders[str(sig.signal_id)]
    assert order.protected and order.open_quantity == qty and order.remaining == o.quantity - qty
    assert order.algo_id.startswith("DS-") and order.tp_algo_id.startswith("DT-")
    assert len(posts(fake, "/fapi/v1/algoOrder")) == 2
    for intent in posts(fake, "/fapi/v1/algoOrder"):
        assert (
            intent["closePosition"] == "true"
            and "quantity" not in intent
            and "reduceOnly" not in intent
        )
        assert intent["workingType"] == "MARK_PRICE" and intent["priceProtect"] == "FALSE"
    c.cancel(str(sig.signal_id))
    final = store.read("DEMO-test").orders[str(sig.signal_id)]
    assert final.remaining == 0 and final.open_quantity == qty and final.protected


def test_duplicate_and_old_event_reconcile_cumulative_fill_once(system):
    store, c, fake = system
    sig, o = enter(system)
    fake.fill(o.client_id, o.quantity)
    event = {"e": "ORDER_TRADE_UPDATE", "E": int(NOW.timestamp() * 1000)}
    c.event(event)
    c.event(event)
    assert store.read("DEMO-test").orders[str(sig.signal_id)].filled == o.quantity
    assert len(posts(fake, "/fapi/v1/algoOrder")) == 2


def test_cancel_timeout_keeps_budget_and_late_fill_gets_protection(system):
    store, c, fake = system
    sig, o = enter(system)
    fake.lost_cancel = True
    c.cancel(str(sig.signal_id))
    assert store.read("DEMO-test").orders[str(sig.signal_id)].remaining == o.quantity
    fake.fill(o.client_id, D(".001"))
    c.reconcile()
    assert store.read("DEMO-test").orders[str(sig.signal_id)].protected
    fake.lost_cancel = False
    c.cancel(str(sig.signal_id))
    assert store.read("DEMO-test").orders[str(sig.signal_id)].remaining == 0


def test_stop_failure_blocks_and_attempts_reduce_only_exit(system):
    store, c, fake = system
    sig, o = enter(system)
    fake.reject_stop = True
    fake.defer_exit = True
    fake.fill(o.client_id, o.quantity)
    c.reconcile()
    ledger = store.read("DEMO-test")
    order = ledger.orders[str(sig.signal_id)]
    assert ledger.risk_state == RiskState.EMERGENCY and not order.protected
    assert order.exit_id.startswith("DX-")
    assert posts(fake)[-1]["reduceOnly"] == "true" and posts(fake)[-1]["type"] == "MARKET"
    with pytest.raises(RiskRejected):
        c.reserve(signal(environment="DEMO", symbol="ETHUSDT"), snapshot())


def test_tp_fill_verified_flat_before_leftover_stop_removed(system):
    store, c, fake = system
    sig, o = enter(system)
    fake.fill(o.client_id, o.quantity)
    c.reconcile()
    order = store.read("DEMO-test").orders[str(sig.signal_id)]
    fake.trigger(order.tp_algo_id)
    assert fake.algos[order.algo_id]["algoStatus"] == "NEW"
    c.reconcile()
    assert store.read("DEMO-test").orders[str(sig.signal_id)].position_state == PositionState.CLOSED
    assert fake.algos[order.algo_id]["algoStatus"] == "CANCELED"


def test_emergency_ack_not_flat_and_duplicate_operation_no_second_exit(system):
    store, c, fake = system
    sig, o = enter(system)
    fake.fill(o.client_id, o.quantity)
    c.reconcile()
    fake.defer_exit = True
    operation = uuid4()
    assert c.command(operation, "EMERGENCY_FLATTEN") == "UNKNOWN"
    assert c.command(operation, "EMERGENCY_FLATTEN") == "UNKNOWN"
    exits = [p for p in posts(fake) if p["type"] == "MARKET"]
    assert len(exits) == 1
    order = store.read("DEMO-test").orders[str(sig.signal_id)]
    assert fake.algos[order.algo_id]["algoStatus"] == "NEW"
    fake.close(order.exit_id)
    assert c.command(operation, "EMERGENCY_FLATTEN") == "VERIFIED_FLAT"


def test_restart_fences_old_leader_and_recovers_pending_order(system):
    store, c, fake = system
    sig, o = enter(system)
    with pytest.raises(RiskRejected):
        store.acquire("DEMO-test", uuid4())
    fake.at += timedelta(seconds=61)
    c.client.sync_clock()
    lease = store.acquire("DEMO-test", uuid4())
    replacement = DemoCoordinator(store, lease, c.client)
    replacement.refresh_metadata()
    with pytest.raises(RiskRejected):
        replacement.reserve(signal(environment="DEMO"), snapshot())
    with pytest.raises(RiskRejected):
        c.reconcile()
    replacement.connected()
    assert len(posts(fake)) == 1
    assert store.read("DEMO-test").orders[str(sig.signal_id)].remaining == o.quantity


def test_concurrent_same_signal_has_one_post(system):
    store, c, fake = system
    sig = signal(environment="DEMO")

    def execute(_):
        order = c.reserve(sig, snapshot())
        c.submit(str(sig.signal_id))
        return order.client_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(execute, range(2)))
    assert len(set(ids)) == 1 and len(posts(fake)) == 1


def test_ws_disconnect_blocks_new_entry_rest_reconcile_does_not_fake_ws_reconnect(system):
    store, c, fake = system
    c.disconnect()
    c.reconcile()
    with pytest.raises(RiskRejected):
        c.reserve(signal(environment="DEMO"), snapshot())
    c.connected()
    c.reserve(signal(environment="DEMO"), snapshot())


def test_external_manual_position_change_does_not_get_silently_adopted(system):
    store, c, fake = system
    fake.positions["ETHUSDT"] = D(1)
    fake.prices["ETHUSDT"] = D(100)
    c.reconcile()
    assert not store.read("DEMO-test").reconciled
    with pytest.raises(RiskRejected):
        c.reserve(signal(environment="DEMO"), snapshot())


def test_demo_rejects_paper_signal_and_unsafe_configured_leverage(system):
    store, c, fake = system
    with pytest.raises(RiskRejected):
        c.reserve(signal(), snapshot())
    fake.leverage = "20"
    c.reconcile()
    with pytest.raises(RiskRejected):
        c.reserve(signal(environment="DEMO"), snapshot())


def test_equity_includes_unrealized_and_external_transfer_adjustment(system):
    store, c, fake = system
    fake.wallet += D("5000")
    fake.incomes = [{"incomeType": "TRANSFER", "tranId": "1", "asset": "USDT", "income": "5000"}]
    c.reconcile()
    assert store.read("DEMO-test").value.adjusted_equity == 10000
    c.reconcile()
    assert store.read("DEMO-test").value.net_deposits == 5000
    fake.wallet -= D("501")
    c.reconcile()
    assert store.read("DEMO-test").risk_state == RiskState.DAILY_LOCK


def test_definitive_rejection_releases_unfilled_reservation(system):
    store, c, fake = system
    fake.reject_submit = True
    sig, o = enter(system)
    order = store.read("DEMO-test").orders[str(sig.signal_id)]
    assert order.state == OrderState.REJECTED and order.remaining == 0


def test_expired_lease_blocks_before_transport(system):
    store, c, fake = system
    fake.at += timedelta(seconds=61)
    count = len(fake.calls)
    with pytest.raises(RiskRejected):
        c.reconcile()
    assert len(fake.calls) == count


def test_failed_algo_queries_still_attempt_emergency_reduce_via_minimal_position_read(
    system, monkeypatch
):
    from integrations.binance_usdm.rest import RESTResponse

    store, c, fake = system
    sig, o = enter(system)
    fake.fill(o.client_id, o.quantity)
    original = fake.__call__

    def broken(method, path, query, key):
        if path == "/fapi/v1/openAlgoOrders":
            return RESTResponse(503, b'{"code":-1007}', {})
        return original(method, path, query, key)

    c.client.send = broken
    with pytest.raises(DemoFailure):
        c.reconcile()
    state = store.read("DEMO-test")
    assert state.risk_state == RiskState.EMERGENCY and not state.reconciled
    assert any(p.get("reduceOnly") == "true" for p in posts(fake))


def test_pause_cancels_unfilled_entries_while_preserving_sl(system):
    store, c, fake = system
    sig, o = enter(system)
    fake.fill(o.client_id, D(".001"))
    c.reconcile()
    order = store.read("DEMO-test").orders[str(sig.signal_id)]
    c.command(uuid4(), "PAUSE_ENTRIES")
    order = store.read("DEMO-test").orders[str(sig.signal_id)]
    assert order.remaining == 0 and order.protected and order.open_quantity == D(".001")
    assert fake.algos[order.algo_id]["algoStatus"] == "NEW"
    assert not any(p["type"] == "MARKET" for p in posts(fake))


def test_db_failure_stops_before_any_transport_request(system, monkeypatch):
    from sqlalchemy.exc import OperationalError

    store, c, fake = system
    sig = signal(environment="DEMO")
    c.reserve(sig, snapshot())
    count = len(fake.calls)

    def offline(*args, **kwargs):
        raise OperationalError("offline", {}, Exception("offline"))

    monkeypatch.setattr(store, "change", offline)
    with pytest.raises(OperationalError):
        c.submit(str(sig.signal_id))
    assert len(fake.calls) == count


def test_release_allows_immediate_successor_but_old_owner_cannot_release_new_owner(system):
    store, c, fake = system
    store.release(c.lease)
    lease = store.acquire("DEMO-test", uuid4())
    with pytest.raises(RiskRejected):
        store.release(c.lease)
    replacement = DemoCoordinator(store, lease, c.client)
    replacement.refresh_metadata()
    replacement.connected()
    assert store.read("DEMO-test").reconciled


def test_wrong_stop_direction_never_counts_as_protected(system):
    store, c, fake = system
    sig, o = enter(system)
    fake.fill(o.client_id, o.quantity)
    c.reconcile()
    order = store.read("DEMO-test").orders[str(sig.signal_id)]
    fake.algos[order.algo_id]["side"] = "BUY"
    fake.defer_exit = True
    c.reconcile()
    state = store.read("DEMO-test")
    assert (
        not state.orders[str(sig.signal_id)].protected and state.risk_state == RiskState.EMERGENCY
    )
    assert len([p for p in posts(fake, "/fapi/v1/algoOrder") if p["type"] == "STOP_MARKET"]) == 1


def test_repeated_operation_conflict_does_not_change_command(system):
    store, c, fake = system
    operation = uuid4()
    c.command(operation, "SAFE_STOP")
    with pytest.raises(ValueError):
        c.command(operation, "EMERGENCY_FLATTEN")
    assert store.read("DEMO-test").operations[str(operation)] == "SAFE_STOP"


def test_demo_snapshot_status_becomes_stale_and_never_enables_api_orders(system):
    from services.execution.demo_status import demo_status

    store, c, fake = system
    ledger = store.read("DEMO-test")
    status = demo_status(ledger, NOW)
    assert status["private_stream_fresh"] is True and status["is_stale"] is False
    status = demo_status(ledger, NOW + timedelta(seconds=31))
    assert status["is_stale"] and not status["private_stream_fresh"]
    assert status["demo_trading_enabled"] is False and status["api_order_routes_enabled"] is False


def test_tp_after_partial_entry_cancels_remaining_entry_before_sl_cleanup(system):
    store, c, fake = system
    sig, o = enter(system)
    fake.fill(o.client_id, D(".001"))
    c.reconcile()
    order = store.read("DEMO-test").orders[str(sig.signal_id)]
    fake.trigger(order.tp_algo_id)
    c.reconcile()
    final = store.read("DEMO-test").orders[str(sig.signal_id)]
    assert final.remaining == 0 and final.position_state == PositionState.CLOSED
    deletes = [path for method, path, p in fake.calls if method == "DELETE"]
    assert deletes.index("/fapi/v1/order") < deletes.index("/fapi/v1/algoOrder")


def test_changed_price_protect_never_counts_as_verified_stop(system):
    store, c, fake = system
    sig, o = enter(system)
    fake.fill(o.client_id, o.quantity)
    c.reconcile()
    order = store.read("DEMO-test").orders[str(sig.signal_id)]
    fake.algos[order.algo_id]["priceProtect"] = True
    fake.defer_exit = True
    c.reconcile()
    assert not store.read("DEMO-test").orders[str(sig.signal_id)].protected
    assert store.read("DEMO-test").risk_state == RiskState.EMERGENCY


def test_verified_exit_fill_with_residual_quantity_uses_new_intent_not_unknown_retry(system):
    store, c, fake = system
    sig, o = enter(system)
    fake.fill(o.client_id, o.quantity)
    c.reconcile()
    fake.defer_exit = True
    c.command(uuid4(), "EMERGENCY_FLATTEN")
    first = store.read("DEMO-test").orders[str(sig.signal_id)].exit_id
    fake.close(first)
    fake.positions["BTCUSDT"] = D(".001")
    fake.prices["BTCUSDT"] = D("100")
    c.reconcile()
    final = store.read("DEMO-test").orders[str(sig.signal_id)]
    assert final.exit_id != first and final.exit_generation == 2
    assert len([p for p in posts(fake) if p["type"] == "MARKET"]) == 2


def test_tp_is_queried_and_verified_and_wrong_tp_pauses_without_removing_sl(system):
    store, c, fake = system
    sig, o = enter(system)
    fake.fill(o.client_id, o.quantity)
    c.reconcile()
    ledger = store.read("DEMO-test")
    order = ledger.orders[str(sig.signal_id)]
    assert (
        ledger.requests[order.algo_id] == "VERIFIED"
        and ledger.requests[order.tp_algo_id] == "VERIFIED"
    )
    fake.algos[order.tp_algo_id]["workingType"] = "CONTRACT_PRICE"
    c.reconcile()
    ledger = store.read("DEMO-test")
    assert ledger.paused and not ledger.reconciled and ledger.orders[str(sig.signal_id)].protected
    assert fake.algos[order.algo_id]["algoStatus"] == "NEW"


def test_read_only_preflight_never_stages_protection_or_exit_mutations(system):
    from integrations.binance_usdm.demo import DemoSettings

    store, c, fake = system
    sig, o = enter(system)
    fake.fill(o.client_id, o.quantity)
    c.client.settings = DemoSettings.model_validate(
        c.client.settings.model_dump() | {"demo_trading_enabled": False}
    )
    c.reconcile()
    order = store.read("DEMO-test").orders[str(sig.signal_id)]
    assert order.algo_id is None and order.exit_id is None
    assert len(posts(fake)) == 1 and len(posts(fake, "/fapi/v1/algoOrder")) == 0
    with pytest.raises(RiskRejected):
        c.reserve(signal(environment="DEMO"), snapshot())


def test_explicit_resume_only_unpauses_normal_fresh_reconciled_account(system):
    store, c, fake = system
    c.command(uuid4(), "SAFE_STOP")
    operation = uuid4()
    assert c.resume_entries(operation) == "ENTRIES_READY_FOR_NEW_SIGNAL_GATES"
    assert not store.read("DEMO-test").paused
    c.reserve(signal(environment="DEMO"), snapshot())
    with pytest.raises(ValueError):
        c.command(operation, "EMERGENCY_FLATTEN")


def test_resume_never_clears_hard_lock_or_missing_private_connection(system):
    store, c, fake = system
    c.disconnect()
    c.reconcile()
    with pytest.raises(RiskRejected):
        c.resume_entries(uuid4())
    c.connected()
    fake.wallet -= D("501")
    c.reconcile()
    with pytest.raises(RiskRejected):
        c.resume_entries(uuid4())
    assert store.read("DEMO-test").risk_state == RiskState.DAILY_LOCK
