"""Pure shape contract tests; fake credentials sign offline, no network."""

from urllib.parse import urlencode

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.rest import prepare_signed_query
from packages.persistence.demo_order_quota import LocalQuotaStopped
from services.execution.demo_query_contract import (
    contract_report,
    require_public_query,
    require_signed_query,
    require_wire_query,
)

KEY = SecretStr("synthetic-key")


def signed(p):
    return prepare_signed_query(
        p, api_key=KEY, secret=SecretStr("synthetic-secret"), timestamp_ms=100
    ).query


@pytest.mark.parametrize(
    "path,params",
    [
        ("/fapi/v1/time", {}),
        ("/fapi/v1/exchangeInfo", {}),
        ("/fapi/v1/klines", {"symbol": "BTCUSDT", "interval": "1m", "limit": "2"}),
        ("/fapi/v1/premiumIndex", {"symbol": "BTCUSDT"}),
        ("/fapi/v1/ticker/bookTicker", {"symbol": "BTCUSDT"}),
    ],
)
def test_exact_public_forms_are_accepted(path, params):
    require_public_query(path, params)
    require_wire_query("GET", path, SecretStr(urlencode(params)), None)


@pytest.mark.parametrize("limit", ["100", "1500", "02", "2.0", "-1", ""])
def test_other_kline_limits_are_rejected(limit):
    with pytest.raises(LocalQuotaStopped, match="QUERY_CONTRACT"):
        require_public_query("/fapi/v1/klines", dict(symbol="BTCUSDT", interval="1m", limit=limit))


@pytest.mark.parametrize(
    "path",
    [
        "/fapi/v1/accountConfig",
        "/fapi/v1/symbolConfig",
        "/fapi/v3/account",
        "/fapi/v3/positionRisk",
        "/fapi/v1/openOrders",
        "/fapi/v1/openAlgoOrders",
    ],
)
def test_global_signed_reads_cannot_silently_become_symbol_scoped(path):
    require_signed_query("GET", path, {})
    require_wire_query("GET", path, signed({}), KEY)
    with pytest.raises(LocalQuotaStopped):
        require_signed_query("GET", path, {"symbol": "BTCUSDT"})


@pytest.mark.parametrize(
    "path,p",
    [
        ("/fapi/v1/order", dict(symbol="BTCUSDT", origClientOrderId="DE-original")),
        ("/fapi/v1/order", dict(symbol="BTCUSDT", orderId="28668119491")),
        ("/fapi/v1/algoOrder", dict(clientAlgoId="DS-original")),
        ("/fapi/v1/income", dict(startTime="0", endTime="100", limit="1000", page="1")),
        ("/fapi/v1/income", dict(startTime="0", endTime="100", limit="1000", page="10")),
    ],
)
def test_bounded_signed_proof_reads_are_accepted(path, p):
    require_signed_query("GET", path, p)
    require_wire_query("GET", path, signed(p), KEY)


@pytest.mark.parametrize(
    "raw",
    [
        "symbol=BTCUSDT&symbol=BTCUSDT&interval=1m&limit=2",
        "symbol=BTCUSDT&%73ymbol=BTCUSDT&interval=1m&limit=2",
        "symbol=BTCUSDT&interval=1m&limit=2&limit=1500",
        "symbol=BTCUSDT&interval=1m&limit=%ZZ",
        "symbol=BTCUSDT&interval=1m&limit=%FF",
        "symbol=BTCUSDT&interval=1m&limit=2&broken",
        "symbol=BTCUSDT&interval=1m&limit=2&extra=",
        "symbol=BTCUSDT&interval=1m&limit=2&" + "x=1&" * 40,
    ],
)
def test_malformed_duplicate_or_unknown_wire_parameters_are_rejected(raw):
    with pytest.raises(LocalQuotaStopped, match="QUERY_CONTRACT"):
        require_wire_query("GET", "/fapi/v1/klines", SecretStr(raw), None)


@pytest.mark.parametrize(
    "field,value", [("recvWindow", "60000"), ("signature", "bad"), ("timestamp", "-1")]
)
def test_signing_envelope_is_single_and_bounded(field, value):
    q = dict(timestamp="100", recvWindow="5000", signature="a" * 64)
    q[field] = value
    with pytest.raises(LocalQuotaStopped):
        require_wire_query("GET", "/fapi/v3/account", SecretStr(urlencode(q)), KEY)


@pytest.mark.parametrize(
    "method,path,p",
    [
        (
            "POST",
            "/fapi/v1/order",
            dict(
                symbol="BTCUSDT",
                side="BUY",
                type="LIMIT",
                quantity="0.0007",
                price="100",
                timeInForce="GTC",
                newClientOrderId="DE-id",
            ),
        ),
        (
            "POST",
            "/fapi/v1/order",
            dict(
                symbol="BTCUSDT",
                side="SELL",
                type="MARKET",
                reduceOnly="true",
                quantity="0.0003",
                newClientOrderId="DX-id",
            ),
        ),
        (
            "POST",
            "/fapi/v1/algoOrder",
            dict(
                symbol="BTCUSDT",
                side="SELL",
                algoType="CONDITIONAL",
                type="STOP_MARKET",
                triggerPrice="90",
                closePosition="true",
                clientAlgoId="DS-id",
                workingType="MARK_PRICE",
                priceProtect="FALSE",
            ),
        ),
        ("DELETE", "/fapi/v1/order", dict(symbol="BTCUSDT", origClientOrderId="DE-id")),
        ("DELETE", "/fapi/v1/algoOrder", dict(clientAlgoId="DS-id")),
    ],
)
def test_original_order_and_risk_reduction_forms_remain_accepted(method, path, p):
    require_signed_query(method, path, p)
    require_wire_query(method, path, signed(p), KEY)
    bad = dict(p, extra="unexpected")
    with pytest.raises(LocalQuotaStopped):
        require_signed_query(method, path, bad)


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_listen_key_uses_api_key_only_and_no_query(method):
    require_wire_query(method, "/fapi/v1/listenKey", SecretStr(""), KEY)
    with pytest.raises(LocalQuotaStopped):
        require_wire_query(method, "/fapi/v1/listenKey", SecretStr("listenKey=private"), KEY)


def test_contract_success_is_never_a_production_cost_certificate():
    report = contract_report()
    assert not report["execution_approved"] and not report["request_cost_upper_bounds_proven"]
    assert "BOOK_TICKER_HEADER_NONAUTHORITATIVE" in report["remaining_execution_blockers"]
