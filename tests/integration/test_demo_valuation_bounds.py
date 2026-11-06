"""Valuation drift never relaxes structural checks or improves observed risk bounds."""

from decimal import Decimal as D

import pytest

from integrations.binance_usdm.demo import DemoFailure
from integrations.binance_usdm.demo_snapshot import snapshot
from services.execution.demo_reconciliation import coherent_snapshot
from tests.integration.test_demo import enter
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_isolated import configure


def observations(demo_system):  # noqa: F811
    system, _ = configure(demo_system)
    store, c, fake = system
    _, order = enter(system)
    fake.fill(order.client_id, order.quantity)
    ledger = store.read("DEMO-test")
    first = snapshot(c.client, ledger, store.now())
    return ledger, first, c


@pytest.mark.parametrize(
    "change",
    [
        "quantity",
        "wallet",
        "leverage",
        "collateral",
        "entry_price",
        "order_status",
        "transfer",
        "missing_margin",
    ],
)
def test_real_state_changes_still_fail_closed(demo_system, change):  # noqa: F811
    ledger, first, c = observations(demo_system)
    second = first.model_copy(deep=True)
    second.fingerprint = "different"
    second.equity -= D("0.01")
    if change == "quantity":
        second.positions["BTCUSDT"] += D("0.001")
    elif change == "wallet":
        second.wallet_balance -= D("0.01")
    elif change == "leverage":
        second.leverage["BTCUSDT"] = D("3")
    elif change == "collateral":
        second.isolated_wallets["BTCUSDT"] += D("1")
    elif change == "entry_price":
        second.entry_prices["BTCUSDT"] += D("0.1")
    elif change == "order_status":
        next(iter(second.orders.values()))["status"] = "PARTIALLY_FILLED"
    elif change == "transfer":
        second.transfers["TRANSFER:unexpected"] = "1"
    else:
        second.isolated_margins.pop("BTCUSDT")
    with pytest.raises(DemoFailure):
        coherent_snapshot(first, second, ledger)


def test_uses_worst_equity_margin_and_long_liquidation(demo_system):  # noqa: F811
    ledger, first, c = observations(demo_system)
    second = first.model_copy(deep=True)
    second.fingerprint = "different"
    second.equity -= D("1")
    second.available_balance += D("1")
    second.margin_used += D("1")
    second.isolated_margins["BTCUSDT"] += D("1")
    second.liquidation_prices["BTCUSDT"] += D("1")
    result = coherent_snapshot(first, second, ledger)
    assert result.equity == second.equity
    assert result.available_balance == first.available_balance
    assert result.margin_used == second.margin_used
    assert result.isolated_margins == first.isolated_margins
    assert result.liquidation_prices == second.liquidation_prices
    assert result.fingerprint == second.fingerprint


def test_flat_account_does_not_relax_new_entry_admission(demo_system):  # noqa: F811
    system, _ = configure(demo_system)
    store, c, _ = system
    ledger = store.read("DEMO-test")
    first = snapshot(c.client, ledger, store.now())
    second = first.model_copy(
        update={"equity": first.equity - D("0.01"), "fingerprint": "different"}
    )
    with pytest.raises(DemoFailure):
        coherent_snapshot(first, second, ledger)


def test_short_uses_lower_liquidation_bound(demo_system):  # noqa: F811
    ledger, first, c = observations(demo_system)
    order = next(iter(ledger.orders.values()))
    order.signal = order.signal.model_copy(
        update={"direction": "SHORT", "initial_stop_price": D("120"), "target_price": D("90")}
    )
    first.positions["BTCUSDT"] = -abs(first.positions["BTCUSDT"])
    first.liquidation_prices["BTCUSDT"] = D("150")
    second = first.model_copy(deep=True)
    second.fingerprint = "different"
    second.liquidation_prices["BTCUSDT"] = D("149")
    result = coherent_snapshot(first, second, ledger)
    assert result.liquidation_prices["BTCUSDT"] == D("149")


@pytest.mark.parametrize("field", ["equity", "margin_used", "liquidation_prices"])
def test_adverse_bound_still_triggers_risk_lock(demo_system, field):  # noqa: F811
    ledger, first, c = observations(demo_system)
    second = first.model_copy(deep=True)
    second.fingerprint = "different"
    if field == "equity":
        first.equity = D("1")
    elif field == "margin_used":
        first.margin_used = first.equity * 2
    else:
        first.liquidation_prices["BTCUSDT"] = D("1000")
    bounded = coherent_snapshot(first, second, ledger)
    # Validate real coordinator risk decisions, not only the chosen min/max.
    c._merge(ledger, bounded)
    assert ledger.risk_state.value in {"EMERGENCY", "DAILY_LOCK", "WEEKLY_LOCK", "DRAWDOWN_LOCK"}


def test_higher_observed_equity_preserves_drawdown_high_water(demo_system):  # noqa: F811
    ledger, first, c = observations(demo_system)
    first.equity += D("100")
    second = first.model_copy(update={"equity": first.equity - D("1"), "fingerprint": "different"})
    result = coherent_snapshot(first, second, ledger)
    assert result.equity == second.equity
    assert ledger.high_water == first.equity
