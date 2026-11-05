import importlib.util
import json
from decimal import Decimal as D
from pathlib import Path

import pytest
import yaml
from pydantic import SecretStr

from integrations.binance_usdm.demo import (
    DemoCredentials,
    DemoFailure,
    DemoRateLimited,
    DemoSettings,
)
from integrations.binance_usdm.filters import SymbolRules
from integrations.binance_usdm.rest import RESTResponse

spec = importlib.util.spec_from_file_location(
    "public_probe", Path(__file__).with_name("check_public_liquidity.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
NOW = 1000000


def metadata():
    from tests.fixtures.binance import exchange_info

    raw = exchange_info()
    for f in raw["symbols"][0]["filters"]:
        if f["filterType"] == "LOT_SIZE":
            f.update(minQty="0.0001", stepSize="0.0001")
        if f["filterType"] == "MIN_NOTIONAL":
            f["notional"] = "50"
    return raw


def book(**changes):
    return dict(
        symbol="BTCUSDT",
        bidPrice="79999.9",
        askPrice="80000.0",
        bidQty="0.01",
        askQty="0.0003",
        time=NOW,
        **changes,
    )


def rules():
    return SymbolRules.from_exchange_info(metadata(), "BTCUSDT")


def test_thin_top_is_only_public_filter_candidate_not_partial_fill_proof():
    result = m.describe_book(book(), rules(), NOW)
    assert result["minimum_entry_quantity_estimate"] == "0.0007"
    assert D(result["minimum_notional_estimate_usdt"]) == 56
    assert result["public_minimum_fits_test_cap"] and result["within_cap_thin_top_candidate"]
    assert result["partial_fill_guaranteed"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"symbol": "ETHUSDT"},
        {"time": NOW - 5001},
        {"time": NOW + 1},
        {"time": True},
        {"bidQty": "NaN"},
        {"askQty": "0"},
        {"askQty": "0.00005"},
        {"askPrice": "79999.8"},
        {"askPrice": "80000.05"},
    ],
)
def test_malformed_stale_future_or_wrong_book_is_rejected(change):
    raw = book()
    raw.update(change)
    with pytest.raises((ValueError, DemoFailure)):
        m.describe_book(raw, rules(), NOW)


def test_minimum_over_cap_does_not_create_a_candidate():
    raw = book()
    raw.update(bidPrice="59999.9", askPrice="60000.0")
    row = m.describe_book(raw, rules(), NOW)
    assert not row["public_minimum_fits_test_cap"] and not row["within_cap_thin_top_candidate"]


def make_client(*, rate=False):
    cfg = DemoSettings.model_validate(
        yaml.safe_load(Path("configs/demo-isolated.example.yaml").read_text())
    )
    calls = []

    def send(method, path, query, api_key):
        calls.append((method, path, query.get_secret_value(), api_key))
        if path == "/fapi/v1/time":
            data = {"serverTime": NOW}
        elif path == "/fapi/v1/exchangeInfo":
            data = metadata()
        elif rate:
            return RESTResponse(429, b'{"code":-1003}', {})
        else:
            data = book()
        return RESTResponse(200, json.dumps(data).encode(), {})

    return m.PublicOnlyClient(
        cfg,
        DemoCredentials(SecretStr("NOT_A_REAL_KEY"), SecretStr("NOT_A_REAL_SECRET")),
        send=send,
        clock_ms=lambda: NOW,
    ), calls


def test_bounded_sampling_never_sends_key_signature_or_account_route():
    c, calls = make_client()
    pauses = []
    report = m.observe(c, monotonic=lambda: 0, pause=pauses.append)
    assert report["sample_count"] == report["within_cap_thin_top_samples"] == 12
    assert len(calls) == 14 and len(pauses) == 11
    assert all(
        method == "GET" and key is None and "signature" not in query
        for method, _, query, key in calls
    )
    assert report["signed_requests"] == report["order_writes"] == 0
    assert report["database_accessed"] is False and report["keys_read"] is False
    assert report["signal_generated"] is False and report["partial_fill_guaranteed"] is False


@pytest.mark.parametrize(
    "method,path",
    [("GET", "/fapi/v3/account"), ("POST", "/fapi/v1/order"), ("DELETE", "/fapi/v1/algoOrder")],
)
def test_private_and_order_routes_rejected_before_transport(method, path):
    c, calls = make_client()
    with pytest.raises(ValueError):
        c.request(method, path, {})
    assert not calls


def test_unexpected_public_route_and_symbol_rejected_before_transport():
    c, calls = make_client()
    with pytest.raises(ValueError):
        c.public("/fapi/v1/ticker/bookTicker", {"symbol": "ETHUSDT"})
    with pytest.raises(ValueError):
        c.public("/fapi/v1/klines", {})
    assert not calls


def test_rate_limit_stops_on_first_book_without_retry():
    c, calls = make_client(rate=True)
    with pytest.raises(DemoRateLimited):
        m.observe(c, monotonic=lambda: 0, pause=lambda _: None)
    assert len(calls) == 3


def test_deadline_prevents_another_request_after_inflight_call_finishes():
    c, calls = make_client()
    tick = [0]
    original = c.public

    def public(path, params=None):
        result = original(path, params)
        if path == "/fapi/v1/ticker/bookTicker":
            tick[0] = 31
        return result

    c.public = public
    report = m.observe(
        c, monotonic=lambda: tick[0], pause=lambda _: pytest.fail("must not wait after deadline")
    )
    assert report["sample_count"] == 1 and len(calls) == 3


def test_maximum_lot_constraint_cannot_be_a_candidate():
    from dataclasses import replace

    row = m.describe_book(book(), replace(rules(), max_qty=D("0.0004")), NOW)
    assert not row["public_minimum_fits_test_cap"]
    assert not row["within_cap_thin_top_candidate"]


def test_private_session_cannot_be_created_even_if_client_config_write_enabled():
    c, calls = make_client()
    c.settings = c.settings.model_copy(update={"demo_trading_enabled": True})
    with pytest.raises(ValueError):
        c.listen_key("POST")
    assert not calls
