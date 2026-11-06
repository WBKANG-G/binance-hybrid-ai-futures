"""Read-only previews use authenticated snapshots but cannot create orders or signals."""

from decimal import Decimal as D

import pytest

from integrations.binance_usdm.demo import DemoFailure, DemoSettings
from services.execution.demo_preview import preview
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_isolated import configure


@pytest.fixture
def case(demo_system):  # noqa: F811
    isolated, controls = configure(demo_system)
    store, c, fake = isolated
    c.client.settings = DemoSettings.model_validate(
        c.client.settings.model_dump() | {"demo_trading_enabled": False}
    )
    send = c.client.send
    market = {"age": 0, "symbol": "BTCUSDT", "bid": "100", "ask": "100.1", "mark": "100"}

    def transport(method, path, query, key):
        result = send(method, path, query, key)
        if path in {"/fapi/v1/ticker/bookTicker", "/fapi/v1/premiumIndex"}:
            data = dict(
                symbol=market["symbol"], time=int(fake.at.timestamp() * 1000) - market["age"]
            )
            if path.endswith("bookTicker"):
                data.update(bidPrice=market["bid"], askPrice=market["ask"])
            else:
                data.update(markPrice=market["mark"])
            result = fake.response(data)
        return result

    c.client.send = transport
    return store, c, fake, controls, market


def test_preview_validates_both_directions_without_any_mutations(case):
    store, c, fake, _, _ = case
    before = len(fake.calls)
    result = preview(c.client, store.read("DEMO-test"))
    assert result["preview"] == "READ_ONLY_NOT_EXECUTABLE"
    assert result["entry_signal_supplied"] is False
    assert result["orders_enabled"] is False
    assert result["algo_order_write_support"] == "UNVERIFIED"
    assert [r["direction"] for r in result["illustrations"]] == ["LONG", "SHORT"]
    for row in result["illustrations"]:
        assert D(row["quantity"]) * D(row["limit_price"]) >= D(result["min_notional_usdt"])
        assert D(row["quantity"]) % D(result["step_size"]) == 0
        assert D(row["estimated_stop_loss_including_costs_usdt"]) > 0
    assert not store.read("DEMO-test").orders
    assert all(method == "GET" for method, path, params in fake.calls[before:])


@pytest.mark.parametrize("age", [5001, -1])
def test_preview_rejects_stale_and_future_quotes(case, age):
    store, c, _, _, market = case
    market["age"] = age
    with pytest.raises(DemoFailure, match="timestamp"):
        preview(c.client, store.read("DEMO-test"))


def test_preview_rejects_wrong_market_symbol(case):
    store, c, _, _, market = case
    market["symbol"] = "ETHUSDT"
    with pytest.raises(DemoFailure, match="symbol mismatch"):
        preview(c.client, store.read("DEMO-test"))


def test_preview_rejects_crossed_book(case):
    store, c, _, _, market = case
    market["bid"] = "101"
    with pytest.raises(DemoFailure, match="spread gate"):
        preview(c.client, store.read("DEMO-test"))


def test_preview_rejects_enabled_client_before_requests(case):
    store, c, fake, _, _ = case
    c.client.settings = DemoSettings.model_validate(
        c.client.settings.model_dump() | {"demo_trading_enabled": True}
    )
    before = len(fake.calls)
    with pytest.raises(DemoFailure, match="writes disabled"):
        preview(c.client, store.read("DEMO-test"))
    assert len(fake.calls) == before


def test_preview_rejects_account_change(case):
    store, c, fake, _, _ = case
    fake.wallet += D("1")
    with pytest.raises(DemoFailure, match="Account changed"):
        preview(c.client, store.read("DEMO-test"))


def test_preview_rejects_open_position(case):
    store, c, fake, _, _ = case
    fake.positions["BTCUSDT"] = D("1")
    fake.prices["BTCUSDT"] = D("100")
    with pytest.raises(DemoFailure, match="empty DEMO account"):
        preview(c.client, store.read("DEMO-test"))


def test_preview_rejects_insufficient_available_margin(case):
    store, c, _, controls, _ = case
    controls["available"] = "0"
    c.reconcile()
    with pytest.raises(DemoFailure, match="risk/margin budget"):
        preview(c.client, store.read("DEMO-test"))


def test_preview_withholds_result_when_account_changes_during_quotes(case):
    store, c, fake, _, _ = case
    send = c.client.send

    def changed(method, path, query, key):
        result = send(method, path, query, key)
        if path == "/fapi/v1/premiumIndex":
            fake.wallet += D("1")
        return result

    c.client.send = changed
    with pytest.raises(DemoFailure, match="Account changed during preview"):
        preview(c.client, store.read("DEMO-test"))


def test_preview_rechecks_quote_age_after_final_snapshot(case, monkeypatch):
    from datetime import timedelta

    import services.execution.demo_preview as module

    store, c, fake, _, _ = case
    original = module.snapshot
    calls = []

    def delayed(*args):
        calls.append(True)
        if len(calls) == 2:
            fake.at += timedelta(seconds=6)
        return original(*args)

    monkeypatch.setattr(module, "snapshot", delayed)
    with pytest.raises(DemoFailure, match="expired during final"):
        preview(c.client, store.read("DEMO-test"))
