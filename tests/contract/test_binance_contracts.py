from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.filters import SymbolRules
from integrations.binance_usdm.orders import build_entry, build_protection
from integrations.binance_usdm.rest import (
    BinanceREST,
    DisabledTrading,
    prepare_signed_query,
    sign_payload,
)
from integrations.binance_usdm.streams import normalize_private, stream_url
from packages.domain.contracts import AlgoOrder, EntryOrder
from tests.fixtures.binance import exchange_info


def rules():
    return SymbolRules.from_exchange_info(exchange_info(), "BTCUSDT")


def entry(**changes):
    data = dict(
        client_order_id="entry-001",
        signal_id=uuid4(),
        environment="PAPER",
        account_id="synthetic",
        symbol="BTCUSDT",
        side="BUY",
        quantity="0.05",
        limit_price="100",
    )
    return EntryOrder(**(data | changes))


def test_hmac_matches_published_standard_vector():
    assert (
        sign_payload(SecretStr("key"), "The quick brown fox jumps over the lazy dog")
        == "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8"
    )


def test_signed_query_is_canonical_and_secrets_not_repr():
    prepared = prepare_signed_query(
        {"symbol": "BTCUSDT", "origClientOrderId": "space /"},
        api_key=SecretStr("synthetic-sensitive-key"),
        secret=SecretStr("synthetic-sensitive-secret"),
        timestamp_ms=123,
        recv_window=5000,
    )
    query = prepared.query.get_secret_value()
    assert query.startswith(
        "origClientOrderId=space+%2F&recvWindow=5000&symbol=BTCUSDT&timestamp=123&signature="
    )
    assert "synthetic-sensitive" not in repr(prepared)
    assert prepared.api_key.get_secret_value() == "synthetic-sensitive-key"


@pytest.mark.parametrize("field", ["signature", "timestamp", "recvWindow"])
def test_caller_cannot_inject_reserved_signing_fields(field):
    with pytest.raises(ValueError):
        prepare_signed_query(
            {field: "1"}, api_key=SecretStr("k"), secret=SecretStr("s"), timestamp_ms=123
        )


def test_order_parameters_use_separate_endpoints():
    normal = build_entry(entry(), rules(), mark_price=Decimal("100"))
    assert normal.path == "/fapi/v1/order"
    assert dict(normal.params) == {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "LIMIT",
        "quantity": "0.05",
        "price": "100",
        "timeInForce": "GTC",
        "newClientOrderId": "entry-001",
    }
    algo = AlgoOrder(
        client_algo_id="stop-1",
        position_id=uuid4(),
        environment="PAPER",
        account_id="synthetic",
        symbol="BTCUSDT",
        side="SELL",
        order_type="STOP_MARKET",
        trigger_price="99",
    )
    intent = build_protection(algo, rules(), working_type="MARK_PRICE")
    values = dict(intent.params)
    assert intent.path == "/fapi/v1/algoOrder"
    assert values["algoType"] == "CONDITIONAL" and values["type"] == "STOP_MARKET"
    assert values["closePosition"] == "true" and values["clientAlgoId"] == "stop-1"
    assert (
        "quantity" not in values and "reduceOnly" not in values and "newClientOrderId" not in values
    )


def test_order_send_is_rejected_before_transport():
    calls = []
    client = BinanceREST(transport=lambda *args: calls.append(args))
    with pytest.raises(DisabledTrading):
        client.submit(build_entry(entry(), rules(), mark_price=Decimal("100")))
    assert calls == []


@pytest.mark.parametrize(
    "quantity,price",
    [
        ("0.0501", "100"),
        ("0.0001", "100"),
        ("0.05", "100.01"),
        ("0.01", "100"),
        ("0.05", "111"),
        ("0.05", "89"),
    ],
)
def test_tick_step_notional_and_percent_price_filters(quantity, price):
    with pytest.raises(ValueError):
        build_entry(entry(quantity=quantity, limit_price=price), rules(), mark_price=Decimal("100"))


def test_filters_use_tick_and_step_not_precision():
    r = rules()
    assert r.floor_quantity(Decimal("0.050999")) == Decimal("0.050")
    assert r.floor_price(Decimal("100.19")) == Decimal("100.1")
    broken = exchange_info()
    broken["symbols"][0]["filters"] = []
    with pytest.raises(ValueError):
        SymbolRules.from_exchange_info(broken, "BTCUSDT")
    halted = exchange_info()
    halted["symbols"][0]["status"] = "BREAK"
    with pytest.raises(ValueError):
        SymbolRules.from_exchange_info(halted, "BTCUSDT")


@pytest.mark.parametrize(
    "stream,route",
    [
        ("btcusdt@depth@100ms", "public"),
        ("btcusdt@bookTicker", "public"),
        ("btcusdt@kline_1m", "market"),
        ("btcusdt@markPrice", "market"),
    ],
)
def test_routed_stream_urls(stream, route):
    assert stream_url([stream]) == f"wss://fstream.binance.com/{route}/stream?streams={stream}"


def test_mixed_routes_unknown_stream_and_private_key_not_in_repr():
    with pytest.raises(ValueError):
        stream_url(["btcusdt@depth", "btcusdt@kline_1m"])
    with pytest.raises(ValueError):
        stream_url(["btcusdt@unknown"])
    private = stream_url([], listen_key=SecretStr("synthetic-listen-key"))
    assert isinstance(private, SecretStr)
    assert private.get_secret_value() == "wss://fstream.binance.com/private/ws/synthetic-listen-key"
    assert "synthetic-listen-key" not in repr(private)


def test_private_order_events_do_not_mix_algo_and_regular_ids():
    regular = normalize_private(
        {
            "e": "ORDER_TRADE_UPDATE",
            "E": 1800000000000,
            "o": {"s": "BTCUSDT", "i": 42, "c": "entry-1", "X": "PARTIALLY_FILLED", "z": "0.01"},
        }
    )
    assert regular.order_id == "42" and regular.algo_id is None
    algo = normalize_private(
        {
            "e": "ALGO_UPDATE",
            "E": 1800000000000,
            "o": {"s": "BTCUSDT", "aid": 99, "caid": "stop-1", "X": "NEW"},
        }
    )
    assert algo.algo_id == "99" and algo.order_id is None


@pytest.mark.parametrize(
    "timestamp,window", [(True, 5000), (1.5, 5000), (123, True), (123, 5001), (123, 0)]
)
def test_invalid_signature_time_types_rejected(timestamp, window):
    with pytest.raises(ValueError):
        prepare_signed_query(
            {},
            api_key=SecretStr("synthetic"),
            secret=SecretStr("synthetic"),
            timestamp_ms=timestamp,
            recv_window=window,
        )


def test_bypassing_a_frozen_model_cannot_create_an_invalid_order_contract():
    malformed = entry().model_copy(update={"order_type": "MARKET"})
    with pytest.raises(ValueError):
        build_entry(malformed, rules(), mark_price=Decimal("100"))
    algo = AlgoOrder(
        client_algo_id="stop-1",
        position_id=uuid4(),
        environment="PAPER",
        account_id="synthetic",
        symbol="BTCUSDT",
        side="SELL",
        order_type="STOP_MARKET",
        trigger_price="99",
    )
    with pytest.raises(ValueError):
        build_protection(
            algo.model_copy(update={"order_type": "MARKET"}), rules(), working_type="MARK_PRICE"
        )
