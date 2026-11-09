"""Real PostgreSQL fault injection. Separate committed schemas support concurrent writers."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
import yaml
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import OperationalError

from integrations.binance_usdm.filters import SymbolRules
from packages.domain.config import RISK_FIELDS, load_config
from packages.domain.contracts import OrderState, PositionState, RiskState, Signal
from packages.persistence.paper import PaperStore, events, outbox
from services.execution.models import Ledger
from services.execution.paper import PaperCoordinator
from services.execution.simulator import PaperExchange
from services.risk.paper import (
    AccountValue,
    GateSnapshot,
    PaperPolicy,
    RiskRejected,
    period_keys,
    size,
)
from tests.fixtures.binance import exchange_info

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required")
NOW = datetime(2026, 10, 19, 7, tzinfo=UTC)


def policy(**risk_changes):
    raw = yaml.safe_load(Path("configs/paper-sandbox.example.yaml").read_text())
    raw["risk"].update(risk_changes)
    return PaperPolicy.model_validate(raw)


def snapshot(**changes):
    values = dict(
        market_at=NOW,
        account_at=NOW,
        correlation_at=NOW,
        bid="100",
        ask="100.1",
        mark="100",
        correlation_group="CRYPTO",
        evaluation="RULES_ONLY",
    )
    values.update(changes)
    return GateSnapshot.model_validate(values)


def signal(symbol="BTCUSDT", **changes):
    values = dict(
        signal_id=uuid4(),
        environment="PAPER",
        symbol=symbol,
        strategy="TREND_FOLLOWING",
        direction="LONG",
        signal_closed_candle_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(seconds=20),
        suggested_limit_price="100",
        initial_stop_price="90",
        target_price="120",
        risk_profile_version="paper-synthetic-v1",
    )
    values.update(changes)
    return Signal.model_validate(values)


def rules(symbol="BTCUSDT"):
    result = SymbolRules.from_exchange_info(exchange_info(), "BTCUSDT")
    from dataclasses import replace

    return replace(result, symbol=symbol)


@pytest.fixture
def system(request):
    url = os.environ["TEST_DATABASE_URL"]
    admin = create_engine(url)
    schema = "phase3_" + uuid4().hex
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"}, pool_size=8)
    with engine.begin() as conn:
        cfg = Config("alembic.ini")
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    store = PaperStore(engine)
    value = AccountValue(as_of=NOW, initial="10000")
    day, week = period_keys(NOW, 0)
    store.create(
        "PAPER-test",
        Ledger(
            policy=policy(**getattr(request, "param", {})),
            value=value,
            daily_base="10000",
            weekly_base="10000",
            high_water="10000",
            daily_key=day,
            weekly_key=week,
        ),
    )
    lease = store.acquire("PAPER-test", uuid4(), NOW)
    venue = PaperExchange(store, lease)
    coordinator = PaperCoordinator(store, lease, venue)
    coordinator.reconcile(NOW)
    yield store, coordinator, venue
    engine.dispose()
    with admin.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


def enter(system, sig=None):
    store, coordinator, venue = system
    sig = sig or signal()
    order = coordinator.reserve(sig, snapshot(), rules(sig.symbol), NOW)
    coordinator.submit(str(sig.signal_id), NOW)
    return sig, order


def test_t01_lost_submit_response_query_not_retry(system):
    store, c, v = system
    v.lose_submit_response = True
    sig, o = enter(system)
    assert store.read("PAPER-test").orders[str(sig.signal_id)].state == OrderState.UNKNOWN
    c.submit(str(sig.signal_id), NOW)
    c.reconcile(NOW)
    assert v.query(NOW).submit_count == 1
    assert store.read("PAPER-test").orders[str(sig.signal_id)].state == OrderState.ACKNOWLEDGED


def test_duplicate_signal_race_has_one_intent_and_one_submit(system):
    store, c, v = system
    sig = signal()
    barrier = Barrier(2)

    def reserve_submit(_):
        barrier.wait()
        o = c.reserve(sig, snapshot(), rules(), NOW)
        c.submit(str(sig.signal_id), NOW)
        return o.client_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(reserve_submit, range(2)))
    assert len(set(ids)) == 1
    assert len(store.read("PAPER-test").orders) == 1
    assert v.query(NOW).submit_count == 1


def test_t02_t03_t04_partial_cancel_duplicate_reverse_delivery(system):
    store, c, v = system
    sig, o = enter(system)
    quantity = rules().floor_quantity(o.quantity * D("0.3"))
    v.fill(o.client_id, "fill1", quantity, D("100"), NOW)
    v.fill(o.client_id, "fill1", quantity, D("100"), NOW)
    c.reconcile(NOW)
    partial = store.read("PAPER-test").orders[str(sig.signal_id)]
    assert partial.filled == quantity and partial.protected
    assert partial.pending_risk == (o.quantity - quantity) * o.loss_per_unit
    c.cancel(str(sig.signal_id), NOW)
    # Replayed old stream notification triggers fresh query; no event can roll back DB state.
    c.reconcile(NOW)
    final = store.read("PAPER-test").orders[str(sig.signal_id)]
    assert final.filled == quantity and final.remaining == 0 and final.protected
    assert final.state == OrderState.CANCELED
    assert final.actual_risk == quantity * o.loss_per_unit
    assert len(final.seen_fills) == 1


def test_cancel_timeout_keeps_reservation_and_protects_late_fill(system):
    store, c, v = system
    sig, o = enter(system)
    v.lose_cancel_response = True
    c.cancel(str(sig.signal_id), NOW)
    assert store.read("PAPER-test").orders[str(sig.signal_id)].remaining == o.quantity
    v.fill(o.client_id, "late", rules().floor_quantity(o.quantity * D("0.3")), D("100"), NOW)
    c.reconcile(NOW)
    assert store.read("PAPER-test").orders[str(sig.signal_id)].protected
    v.lose_cancel_response = False
    c.cancel(str(sig.signal_id), NOW)
    assert store.read("PAPER-test").orders[str(sig.signal_id)].remaining == 0


def test_t05_stop_failure_blocks_and_reduces_without_claiming_ack_is_fill(system):
    store, c, v = system
    sig, o = enter(system)
    v.fail_stop = True
    v.defer_exit = True
    v.fill(o.client_id, "partial", rules().floor_quantity(o.quantity * D("0.3")), D("100"), NOW)
    c.reconcile(NOW)
    state = store.read("PAPER-test")
    order = state.orders[str(sig.signal_id)]
    assert state.risk_state == RiskState.EMERGENCY and not state.reconciled
    assert not order.protected and order.exit_id
    assert order.charged_risk >= order.open_quantity * order.average_price
    with pytest.raises(RiskRejected):
        c.reserve(signal("ETHUSDT"), snapshot(), rules("ETHUSDT"), NOW)
    c.reconcile(NOW)
    assert v.query(NOW).exit_count == 1
    assert store.read("PAPER-test").orders[str(sig.signal_id)].exit_state == "UNKNOWN"
    v.settle_exit(order.exit_id, o.client_id, NOW)
    c.reconcile(NOW)
    assert (
        store.read("PAPER-test").orders[str(sig.signal_id)].position_state == PositionState.CLOSED
    )
    assert store.read("PAPER-test").risk_state == RiskState.EMERGENCY


def test_t06_t08_t15_flatten_is_idempotent_and_stop_retained_until_flat(system):
    store, c, v = system
    sig, o = enter(system)
    v.fill(o.client_id, "all", o.quantity, D("100"), NOW)
    c.reconcile(NOW)
    protected = store.read("PAPER-test").orders[str(sig.signal_id)]
    v.defer_exit = True
    assert c.command("operation1", "EMERGENCY_FLATTEN", NOW) == "UNKNOWN"
    assert c.command("operation1", "EMERGENCY_FLATTEN", NOW) == "UNKNOWN"
    assert v.query(NOW).exit_count == 1
    assert v.query(NOW).stops[protected.algo_id].active
    exit_id = store.read("PAPER-test").orders[str(sig.signal_id)].exit_id
    v.settle_exit(exit_id, o.client_id, NOW)
    assert c.command("operation1", "EMERGENCY_FLATTEN", NOW) == "VERIFIED_FLAT"
    assert not v.query(NOW).stops[protected.algo_id].active
    with pytest.raises(ValueError):
        c.command("operation1", "SAFE_STOP", NOW)


def test_t07_t10_restart_new_fence_old_writer_rejected(system):
    store, c, v = system
    sig, o = enter(system)
    with pytest.raises(RiskRejected):
        store.acquire("PAPER-test", uuid4(), NOW)
    later = NOW + timedelta(seconds=31)
    lease = store.acquire("PAPER-test", uuid4(), later)
    replacement = PaperCoordinator(store, lease, PaperExchange(store, lease))
    with pytest.raises(RiskRejected):
        replacement.reserve(signal("ETHUSDT"), snapshot(), rules("ETHUSDT"), later)
    with pytest.raises(RiskRejected):
        v.submit(o, later)
    replacement.reconcile(later)
    assert replacement.exchange.query(later).submit_count == 1
    # Expired signal isn't reissued after recovery.
    replacement.expire(later)
    assert store.read("PAPER-test").orders[str(sig.signal_id)].state == OrderState.CANCELED


def test_db_down_never_reaches_venue(system, monkeypatch):
    store, c, v = system
    sig, o = enter(system)
    calls = []
    monkeypatch.setattr(v, "submit", lambda *args: calls.append(args))

    def down(*args, **kwargs):
        raise OperationalError("offline", {}, Exception("offline"))

    monkeypatch.setattr(store, "change", down)
    with pytest.raises(OperationalError):
        c.submit(str(sig.signal_id), NOW)
    assert calls == []


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"unrealized": "-501"}, RiskState.DAILY_LOCK),
        ({"realized": "-1001"}, RiskState.WEEKLY_LOCK),
        ({"unrealized": "-1501"}, RiskState.DRAWDOWN_LOCK),
        ({"fees": "501"}, RiskState.DAILY_LOCK),
        ({"funding": "-501"}, RiskState.DAILY_LOCK),
    ],
)
def test_t09_losses_include_unrealized_fees_and_funding(system, changes, expected):
    store, c, v = system
    v.mark_account(AccountValue(as_of=NOW, initial="10000", **changes), NOW)
    c.reconcile(NOW)
    with pytest.raises(RiskRejected):
        c.reserve(signal(), snapshot(), rules(), NOW)
    assert store.read("PAPER-test").risk_state == expected


def test_deposit_does_not_hide_loss_or_count_withdrawal_as_loss(system):
    store, c, v = system
    v.mark_account(AccountValue(as_of=NOW, initial="10000", net_deposits="-1000"), NOW)
    c.reconcile(NOW)
    assert store.read("PAPER-test").risk_state == RiskState.NORMAL
    v.mark_account(
        AccountValue(as_of=NOW, initial="10000", unrealized="-501", net_deposits="5000"), NOW
    )
    c.reconcile(NOW)
    assert store.read("PAPER-test").risk_state == RiskState.DAILY_LOCK


def test_t13_cross_symbol_concurrent_reservation_never_exceeds_budget(system):
    store, c, v = system
    barrier = Barrier(4)

    def reserve(i):
        sym = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"][i]
        barrier.wait()
        try:
            c.reserve(signal(sym), snapshot(), rules(sym), NOW)
            return True
        except RiskRejected:
            return False

    with ThreadPoolExecutor(max_workers=4) as executor:
        accepted = list(executor.map(reserve, range(4)))
    ledger = store.read("PAPER-test")
    assert sum(accepted) == 2
    assert sum(o.charged_risk for o in ledger.orders.values()) <= D("200")
    assert sum(o.notional for o in ledger.orders.values()) <= D("15000")


@pytest.mark.parametrize(
    "change",
    [
        {"market_at": NOW - timedelta(seconds=31)},
        {"account_at": NOW - timedelta(seconds=31)},
        {"correlation_at": NOW - timedelta(seconds=31)},
        {"market_at": NOW + timedelta(seconds=1)},
        {"evaluation": "HOLD"},
        {"ask": "103"},
        {"bid": "101"},
    ],
)
def test_t11_t14_freshness_and_hold_fail_closed(system, change):
    store, c, v = system
    with pytest.raises(ValueError):
        c.reserve(signal(), snapshot(**change), rules(), NOW)
    assert v.query(NOW).submit_count == 0


def test_t17_unmanaged_position_blocks_entry(system):
    store, c, v = system
    store.venue_change(
        c.lease,
        NOW,
        "MANUAL_POSITION",
        lambda venue: venue.manual_positions.update({"BTCUSDT": D("1")}),
    )
    c.reconcile(NOW)
    assert not store.read("PAPER-test").reconciled
    with pytest.raises(RiskRejected):
        c.reserve(signal(), snapshot(), rules(), NOW)


def test_pause_cancels_only_entries_safe_stop_preserves_stop(system):
    store, c, v = system
    sig, o = enter(system)
    v.fill(o.client_id, "part", rules().floor_quantity(o.quantity * D("0.3")), D("100"), NOW)
    c.reconcile(NOW)
    assert c.command("safe", "SAFE_STOP", NOW) == "ENTRIES_BLOCKED"
    assert store.read("PAPER-test").orders[str(sig.signal_id)].remaining > 0
    c.command("pause", "PAUSE_ENTRIES", NOW)
    order = store.read("PAPER-test").orders[str(sig.signal_id)]
    assert order.protected and order.remaining == 0 and order.open_quantity > 0
    assert v.query(NOW).exit_count == 0


def test_decimal_sizing_filters_version_and_simulation_policy_isolation(system):
    store, c, v = system
    sig = signal()
    quantity, loss = size(policy(), sig, snapshot(), D("10000"), rules(), NOW)
    assert quantity * loss <= D("100") and quantity % rules().step_size == 0
    assert (quantity + rules().step_size) * loss > D("100")
    with pytest.raises(ValueError):
        size(policy(), signal(risk_profile_version="other"), snapshot(), D("10000"), rules(), NOW)
    for filename in ["configs/paper.example.yaml", "configs/live.example.yaml"]:
        config = load_config(Path(filename))
        assert all(getattr(config.risk, key) is None for key in RISK_FIELDS)
    with pytest.raises(ValueError):
        PaperPolicy.model_validate({"mode": "LIVE"})


def test_audit_and_outbox_committed_atomically(system):
    store, c, v = system
    enter(system)
    with store.engine.connect() as conn:
        count = conn.execute(select(func.count()).select_from(events)).scalar_one()
        assert count > 0
        assert count == conn.execute(select(func.count()).select_from(outbox)).scalar_one()
    before = store.read("PAPER-test").model_dump()

    def fail(ledger):
        ledger.paused = True
        raise RuntimeError("crash before commit")

    with pytest.raises(RuntimeError):
        store.change(c.lease, NOW, "SHOULD_ROLLBACK", fail)
    assert store.read("PAPER-test").model_dump() == before
    with store.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(events)).scalar_one() == count


def test_kst_day_and_fixed_week_boundaries():
    before = datetime(2026, 10, 18, 14, 59, 59, tzinfo=UTC)
    after = before + timedelta(seconds=1)
    assert period_keys(before, 0) == ("2026-10-18", "2026-10-12")
    assert period_keys(after, 0) == ("2026-10-19", "2026-10-19")


def test_t06_tp_closes_before_sl_cleanup_and_books_realized_fees(system):
    store, c, v = system
    sig, o = enter(system)
    v.fill(o.client_id, "entry", o.quantity, D("100"), NOW)
    c.reconcile(NOW)
    algo = store.read("PAPER-test").orders[str(sig.signal_id)].algo_id
    entry_fees = o.quantity * D("100") * policy().fee_rate
    assert v.query(NOW).value.fees == entry_fees
    v.mark("BTCUSDT", D("121"), NOW)
    remote = v.query(NOW)
    assert remote.stops[algo].active and remote.orders[o.client_id].open_quantity == 0
    assert remote.value.realized == o.quantity * D("20")
    assert remote.value.fees == entry_fees + o.quantity * D("120") * policy().fee_rate
    c.reconcile(NOW)
    assert not v.query(NOW).stops[algo].active
    assert (
        store.read("PAPER-test").orders[str(sig.signal_id)].position_state == PositionState.CLOSED
    )


def test_stop_gap_can_exceed_planned_risk_and_triggers_loss_lock(system):
    store, c, v = system
    sig, o = enter(system)
    v.fill(o.client_id, "entry", o.quantity, D("100"), NOW)
    c.reconcile(NOW)
    v.mark("BTCUSDT", D("40"), NOW)
    c.reconcile(NOW)
    state = store.read("PAPER-test")
    assert state.value.realized == -o.quantity * D("60")
    assert state.risk_state == RiskState.DAILY_LOCK
    assert state.orders[str(sig.signal_id)].open_quantity == 0


def test_short_fill_recalculates_risk_and_excess_forces_emergency(system):
    store, c, v = system
    sig = signal(direction="SHORT", initial_stop_price="110", target_price="80")
    sig, o = enter(system, sig)
    v.fill(o.client_id, "entry", o.quantity, D("121"), NOW)
    c.reconcile(NOW)
    assert store.read("PAPER-test").risk_state == RiskState.EMERGENCY
    assert v.query(NOW).exit_count == 1
    c.reconcile(NOW)
    assert store.read("PAPER-test").orders[str(sig.signal_id)].open_quantity == 0


def test_protection_ack_with_wrong_direction_is_never_protected(system, monkeypatch):
    store, c, v = system
    sig, o = enter(system)
    original = v.protect

    def wrong(order, now):
        original(order, now)
        store.venue_change(
            c.lease,
            now,
            "WRONG_STOP",
            lambda venue: setattr(next(iter(venue.stops.values())), "side", "BUY"),
        )

    monkeypatch.setattr(v, "protect", wrong)
    v.defer_exit = True
    v.fill(o.client_id, "entry", o.quantity, D("100"), NOW)
    c.reconcile(NOW)
    state = store.read("PAPER-test")
    assert (
        not state.orders[str(sig.signal_id)].protected and state.risk_state == RiskState.EMERGENCY
    )


def test_no_entry_between_unknown_remote_fill_and_reconciliation(system):
    store, c, v = system
    sig, o = enter(system)
    v.fill(o.client_id, "entry", rules().floor_quantity(o.quantity * D("0.3")), D("100"), NOW)
    with pytest.raises(RiskRejected, match="reconciliation"):
        c.reserve(signal("ETHUSDT"), snapshot(), rules("ETHUSDT"), NOW)
    c.reconcile(NOW)
    assert store.read("PAPER-test").orders[str(sig.signal_id)].protected
    c.reserve(signal("ETHUSDT"), snapshot(), rules("ETHUSDT"), NOW)


def test_policy_cannot_be_changed_by_execution_or_ai_callback(system):
    store, c, v = system

    def tamper(ledger):
        ledger.policy = policy(risk_per_trade_pct="50")

    with pytest.raises(RiskRejected, match="immutable"):
        store.change(c.lease, NOW, "AI_POLICY_MUTATION", tamper)
    assert store.read("PAPER-test").policy.limit("risk_per_trade_pct") == 1


def test_reserved_order_expires_without_venue_submission(system):
    store, c, v = system
    sig = signal()
    c.reserve(sig, snapshot(), rules(), NOW)
    c.expire(NOW + timedelta(seconds=21))
    order = store.read("PAPER-test").orders[str(sig.signal_id)]
    assert order.remaining == 0 and order.state == OrderState.REJECTED
    assert v.query(NOW).submit_count == 0


def test_conflicting_signal_and_fill_ids_rejected(system):
    store, c, v = system
    sig, o = enter(system)
    with pytest.raises(RiskRejected):
        c.reserve(sig.model_copy(update={"target_price": D("130")}), snapshot(), rules(), NOW)
    v.fill(o.client_id, "entry", rules().floor_quantity(o.quantity * D("0.3")), D("100"), NOW)
    with pytest.raises(ValueError):
        v.fill(o.client_id, "entry", rules().floor_quantity(o.quantity * D("0.2")), D("100"), NOW)
    with pytest.raises(ValueError):
        v.fill(o.client_id, "next", o.quantity, D("100"), NOW)
    with pytest.raises(ValueError):
        v.fill(o.client_id, "next", rules().floor_quantity(o.quantity * D("0.1")), D("101"), NOW)


@pytest.mark.parametrize(
    "system",
    [
        {"max_single_notional_pct": "1"},
        {"max_total_notional_pct": "1"},
        {"max_margin_usage_pct": "1"},
        {"max_correlated_risk_pct": "0.1"},
    ],
    indirect=True,
)
def test_each_portfolio_cap_rejects_before_any_submit(system):
    store, c, v = system
    with pytest.raises(RiskRejected, match="cap"):
        c.reserve(signal(), snapshot(), rules(), NOW)
    assert v.query(NOW).submit_count == 0


def test_offline_replay_cli_contract_and_artifacts(system, tmp_path, monkeypatch):
    import json
    import sys

    from scripts.run_paper import main

    store, c, v = system
    # Use the isolated committed PostgreSQL schema, just as a fresh CLI process would.
    from scripts import run_paper

    monkeypatch.setattr(run_paper, "create_engine", lambda *args, **kwargs: store.engine)
    output = tmp_path / "report"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_paper",
            "--demo",
            "--policy",
            "configs/paper-sandbox.example.yaml",
            "--account",
            "PAPER-cli",
            "--output",
            str(output),
        ],
    )
    main()
    report = json.loads((output / "report.json").read_text())
    assert report["live_trading_enabled"] is False and report["binance_connected"] is False
    assert report["orders"] == 1 and report["filled_orders"] == 1 and report["open_positions"] == 0
    assert report["reconciled"] is True
    with pytest.raises(FileExistsError):
        main()
    assert store.read("PAPER-cli").orders


def test_fill_event_during_stop_verification_blocks_new_entry(system, monkeypatch):
    store, c, v = system
    sig, o = enter(system)
    quantity = rules().floor_quantity(o.quantity * D("0.3"))
    v.fill(o.client_id, "part", quantity, D("100"), NOW)
    original = v.protect

    def racing(order, now):
        original(order, now)
        v.fill(o.client_id, "late", quantity, D("100"), NOW)

    monkeypatch.setattr(v, "protect", racing)
    c.reconcile(NOW)
    assert not store.read("PAPER-test").reconciled
    with pytest.raises(RiskRejected):
        c.reserve(signal("ETHUSDT"), snapshot(), rules("ETHUSDT"), NOW)
    monkeypatch.setattr(v, "protect", original)
    c.reconcile(NOW)
    assert store.read("PAPER-test").reconciled


def test_stale_before_submit_does_not_send_an_order(system):
    store, c, v = system
    sig = signal(expires_at=NOW + timedelta(minutes=5))
    c.reserve(sig, snapshot(), rules(), NOW)
    # Within leader TTL but a deliberately older market observation.
    store.change(
        c.lease,
        NOW,
        "OLDER_MARKET",
        lambda ledger: setattr(
            ledger.orders[str(sig.signal_id)],
            "snapshot",
            snapshot(market_at=NOW - timedelta(seconds=29)),
        ),
    )
    c.submit(str(sig.signal_id), NOW + timedelta(seconds=2))
    assert v.query(NOW).submit_count == 0 and not store.read("PAPER-test").reconciled


def test_readonly_status_marks_old_replay_stale(system):
    from services.execution.status import paper_status

    store, c, v = system
    assert paper_status(store.read("PAPER-test"), NOW)["is_stale"] is False
    status = paper_status(store.read("PAPER-test"), NOW + timedelta(seconds=31))
    assert status["is_stale"] is True and status["reconciliation_required"] is True
    assert status["worker_enabled"] is False and status["orders_enabled"] is False


def test_changing_correlation_label_cannot_bypass_aggregate_cap(system):
    store, c, v = system
    for index, symbol in enumerate(["BTCUSDT", "ETHUSDT"]):
        c.reserve(signal(symbol), snapshot(correlation_group=f"group-{index}"), rules(symbol), NOW)
    with pytest.raises(RiskRejected, match="cap"):
        c.reserve(
            signal("SOLUSDT"),
            snapshot(correlation_group="invented-independent"),
            rules("SOLUSDT"),
            NOW,
        )


def test_hold_preserves_existing_verified_stop(system):
    store, c, v = system
    sig, o = enter(system)
    v.fill(o.client_id, "entry", o.quantity, D("100"), NOW)
    c.reconcile(NOW)
    protected = store.read("PAPER-test").orders[str(sig.signal_id)]
    with pytest.raises(RiskRejected, match="HOLD"):
        c.reserve(signal("ETHUSDT"), snapshot(evaluation="HOLD"), rules("ETHUSDT"), NOW)
    assert v.query(NOW).stops[protected.algo_id].active
    assert store.read("PAPER-test").orders[str(sig.signal_id)].protected


def test_process_crash_after_commit_before_ack_is_recovered_without_resubmit(system, monkeypatch):
    store, c, v = system
    sig = signal()
    c.reserve(sig, snapshot(), rules(), NOW)
    original = v.submit

    def crash(order, now):
        original(order, now)
        raise SystemExit("simulated process loss")

    monkeypatch.setattr(v, "submit", crash)
    with pytest.raises(SystemExit):
        c.submit(str(sig.signal_id), NOW)
    assert store.read("PAPER-test").orders[str(sig.signal_id)].state == OrderState.SUBMITTING
    later = NOW + timedelta(seconds=31)
    newlease = store.acquire("PAPER-test", uuid4(), later)
    replacement = PaperCoordinator(store, newlease, PaperExchange(store, newlease))
    replacement.reconcile(later)
    replacement.submit(str(sig.signal_id), later)
    assert replacement.exchange.query(later).submit_count == 1


def test_kst_rollover_requires_exact_opening_value_and_preserves_hard_lock(system):
    from services.execution.paper import account_risk

    store, c, v = system
    boundary = datetime(2026, 10, 19, 15, tzinfo=UTC)
    ledger = store.read("PAPER-test")
    ledger.value = AccountValue(as_of=boundary + timedelta(seconds=1), initial="10000")
    account_risk(ledger, boundary + timedelta(seconds=1))
    assert ledger.risk_state == RiskState.UNHEALTHY
    ledger = store.read("PAPER-test")
    ledger.risk_state = RiskState.DAILY_LOCK
    ledger.value = AccountValue(as_of=boundary, initial="10000")
    account_risk(ledger, boundary)
    assert ledger.daily_key == "2026-10-20" and ledger.risk_state == RiskState.DAILY_LOCK


def test_stale_valuation_never_clears_emergency_or_loss_lock(system):
    from services.execution.paper import account_risk

    store, c, v = system
    for risk in [
        RiskState.EMERGENCY,
        RiskState.DAILY_LOCK,
        RiskState.WEEKLY_LOCK,
        RiskState.DRAWDOWN_LOCK,
    ]:
        ledger = store.read("PAPER-test")
        ledger.risk_state = risk
        account_risk(ledger, NOW + timedelta(seconds=31))
        assert ledger.risk_state == risk and not ledger.reconciled


@pytest.mark.parametrize("system", [{"max_single_notional_pct": "10"}], indirect=True)
def test_actual_fill_exceeding_notional_cap_is_reduced_even_when_stop_risk_falls(system):
    store, c, v = system
    sig = signal(direction="SHORT", initial_stop_price="110", target_price="80")
    sig, o = enter(system, sig)
    v.fill(o.client_id, "entry", o.quantity, D("109"), NOW)
    c.reconcile(NOW)
    assert store.read("PAPER-test").risk_state == RiskState.EMERGENCY
    assert v.query(NOW).exit_count == 1
