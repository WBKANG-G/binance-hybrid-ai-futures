"""Quantity caps survive durable DEMO execution, protection and recovery."""

from decimal import Decimal as D

import pytest

from integrations.binance_usdm.filters import SymbolRules
from services.execution.demo import DemoCoordinator
from services.risk.paper import RiskRejected, capped_quantity
from tests.fixtures.binance import exchange_info
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_isolated import configure
from tests.integration.test_paper import signal, snapshot


@pytest.fixture
def case(demo_system):  # noqa: F811
    return configure(demo_system)


def test_cap_limits_actual_post_and_survives_restart(case):
    (store, c, fake), _ = case
    sig = signal(environment="DEMO")
    order = c.reserve(sig, snapshot(), quantity_cap=D("0.05"))
    assert order.quantity == D("0.05")
    restart = DemoCoordinator(store, c.lease, c.client)
    restart.refresh_metadata()
    restart.connected()
    restart.submit(str(sig.signal_id))
    assert D(posts(fake)[0]["quantity"]) == D("0.05")
    assert store.read("DEMO-test").entry_quantity_caps[str(sig.signal_id)] == D("0.05")
    restart.submit(str(sig.signal_id))
    assert len(posts(fake)) == 1


@pytest.mark.parametrize("cap", [D("0"), D("-1"), D("NaN"), D("Infinity"), D("0.0501"), 0.05])
def test_invalid_caps_never_reserve_or_post(case, cap):
    (store, c, fake), _ = case
    with pytest.raises(ValueError):
        c.reserve(signal(environment="DEMO"), snapshot(), quantity_cap=cap)
    assert not store.read("DEMO-test").orders
    assert not posts(fake)


def test_cap_cannot_round_up_to_satisfy_minimum_notional(case):
    (store, c, fake), _ = case
    with pytest.raises(ValueError, match="MIN_NOTIONAL"):
        c.reserve(signal(environment="DEMO"), snapshot(), quantity_cap=D("0.001"))
    assert not store.read("DEMO-test").orders
    assert not posts(fake)


def test_same_signal_cannot_change_or_remove_cap(case):
    (store, c, fake), _ = case
    sig = signal(environment="DEMO")
    original = c.reserve(sig, snapshot(), quantity_cap=D("0.05"))
    assert c.reserve(sig, snapshot(), quantity_cap=D("0.050")).quantity == original.quantity
    for cap in [None, D("0.06"), D("0.04")]:
        with pytest.raises(RiskRejected, match="Conflicting DEMO quantity cap"):
            c.reserve(sig, snapshot(), quantity_cap=cap)
    assert store.read("DEMO-test").orders[str(sig.signal_id)].quantity == original.quantity
    assert not posts(fake)


def test_large_cap_never_increases_risk_quantity():
    rules = SymbolRules.from_exchange_info(exchange_info(), "BTCUSDT")
    assert capped_quantity(D("1"), D("1000"), rules) == D("1")


def test_capped_partial_fill_protects_actual_fill_and_cancelled_remainder(case):
    (store, c, fake), _ = case
    sig = signal(environment="DEMO")
    order = c.reserve(sig, snapshot(), quantity_cap=D("0.05"))
    c.submit(str(sig.signal_id))
    fake.fill(order.client_id, D("0.02"))
    c.reconcile()
    partial = store.read("DEMO-test").orders[str(sig.signal_id)]
    assert partial.open_quantity == D("0.02") and partial.protected
    c.cancel(str(sig.signal_id))
    final = store.read("DEMO-test").orders[str(sig.signal_id)]
    assert final.remaining == 0 and final.open_quantity == D("0.02") and final.protected


def test_capped_stop_failure_reduces_only_actual_exposure(case):
    (store, c, fake), _ = case
    sig = signal(environment="DEMO")
    order = c.reserve(sig, snapshot(), quantity_cap=D("0.05"))
    c.submit(str(sig.signal_id))
    fake.fill(order.client_id, order.quantity)
    fake.reject_stop = True
    c.reconcile()
    assert fake.positions["BTCUSDT"] == 0
    exits = [p for p in posts(fake) if p["type"] == "MARKET"]
    assert (
        len(exits) == 1
        and exits[0]["reduceOnly"] == "true"
        and D(exits[0]["quantity"]) <= D("0.05")
    )


def test_unknown_capped_submit_is_not_retried(case):
    (store, c, fake), _ = case
    sig = signal(environment="DEMO")
    c.reserve(sig, snapshot(), quantity_cap=D("0.05"))
    fake.lost_submit = True
    c.submit(str(sig.signal_id))
    c.submit(str(sig.signal_id))
    assert len(posts(fake)) == 1 and D(posts(fake)[0]["quantity"]) == D("0.05")
