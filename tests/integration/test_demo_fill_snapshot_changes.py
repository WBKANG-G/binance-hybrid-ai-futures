"""Reproduce post-fill reconciliation failures with changing exchange responses."""

import json
from decimal import Decimal

import pytest

from integrations.binance_usdm.demo import DemoFailure
from tests.integration.test_demo import enter, posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_isolated import configure


@pytest.mark.parametrize("field", ["unrealized", "isolatedMargin", "liquidationPrice"])
def test_post_fill_market_value_change_now_gets_verified_protection(demo_system, field):  # noqa: F811
    system, _ = configure(demo_system)
    store, c, fake = system
    sig, order = enter(system)
    fake.fill(order.client_id, order.quantity)
    original = c.client.send
    reads = 0

    def moving(method, path, query, key):
        nonlocal reads
        response = original(method, path, query, key)
        target = "/fapi/v3/account" if field == "unrealized" else "/fapi/v3/positionRisk"
        if method == "GET" and path == target:
            reads += 1
            if reads == 2:
                data = json.loads(response.body)
                if field == "unrealized":
                    data["totalUnrealizedProfit"] = "0.01"
                else:
                    for row in data:
                        if row["symbol"] == "BTCUSDT":
                            row[field] = str(Decimal(row[field]) + Decimal("0.01"))
                return fake.response(data, response.status)
        return response

    c.client.send = moving
    c.reconcile()
    ledger = store.read("DEMO-test")
    current = ledger.orders[str(sig.signal_id)]
    assert current.filled == order.quantity and current.open_quantity == order.quantity
    assert current.protected and current.tp_algo_id
    assert len(posts(fake, "/fapi/v1/algoOrder")) == 2
    assert len([p for p in posts(fake) if p["type"] == "LIMIT"]) == 1
    assert ledger.reconciliation_failure_code == "NONE"


def test_unchanged_post_fill_values_create_verified_sl_and_tp(demo_system):  # noqa: F811
    system, _ = configure(demo_system)
    store, c, fake = system
    sig, order = enter(system)
    fake.fill(order.client_id, order.quantity)
    c.reconcile()
    current = store.read("DEMO-test").orders[str(sig.signal_id)]
    assert current.filled == order.quantity
    assert current.protected and current.tp_algo_id
    assert len(posts(fake, "/fapi/v1/algoOrder")) == 2


@pytest.mark.parametrize("field", ["isolatedMargin", "isolatedWallet"])
def test_missing_post_fill_collateral_also_requires_safe_diagnosis(demo_system, field):  # noqa: F811
    system, _ = configure(demo_system)
    store, c, fake = system
    _, order = enter(system)
    fake.fill(order.client_id, order.quantity)
    original = c.client.send

    def incomplete(method, path, query, key):
        response = original(method, path, query, key)
        if method == "GET" and path == "/fapi/v3/positionRisk":
            data = json.loads(response.body)
            for row in data:
                row.pop(field, None)
            return fake.response(data, response.status)
        return response

    c.client.send = incomplete
    with pytest.raises(DemoFailure, match="Invalid/incomplete DEMO snapshot"):
        c.reconcile()
    ledger = store.read("DEMO-test")
    assert ledger.reconciliation_failure_code == "SNAPSHOT_INVALID"
    assert ledger.risk_state.value == "EMERGENCY" and ledger.paused
    assert fake.positions["BTCUSDT"] == 0
    assert not posts(fake, "/fapi/v1/algoOrder")
    assert len([p for p in posts(fake) if p["type"] == "LIMIT"]) == 1
