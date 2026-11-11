"""Narrow modeled request shapes. SDK documentation is not a venue cost certificate."""

import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl

from pydantic import SecretStr

from packages.persistence.demo_order_quota import quota_require

CONTRACT_VERSION = "ISOLATED_BTC_TP_QUERY_V1"
PUBLIC = {
    "/fapi/v1/time": {},
    "/fapi/v1/exchangeInfo": {},
    "/fapi/v1/klines": {"symbol": "BTCUSDT", "interval": "1m", "limit": "2"},
    "/fapi/v1/premiumIndex": {"symbol": "BTCUSDT"},
    "/fapi/v1/ticker/bookTicker": {"symbol": "BTCUSDT"},
}
GLOBAL_GET = frozenset(
    {
        "/fapi/v1/accountConfig",
        "/fapi/v1/symbolConfig",
        "/fapi/v3/account",
        "/fapi/v3/positionRisk",
        "/fapi/v1/openOrders",
        "/fapi/v1/openAlgoOrders",
    }
)
SIGNING = frozenset({"timestamp", "recvWindow", "signature"})


def require_shape(ok: bool) -> None:
    quota_require(ok, "RUNTIME_QUERY_CONTRACT_REJECTED")


def identifier(value: str) -> bool:
    return re.fullmatch(r"[.A-Z:/a-z0-9_-]{1,36}", value) is not None


def integer(value: str, *, positive: bool = False) -> bool:
    return (
        bool(value)
        and value.isascii()
        and value.isdecimal()
        and len(value) <= 20
        and (not positive or int(value) > 0)
    )


def amount(value: str) -> bool:
    return (
        1 <= len(value) <= 32
        and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value) is not None
        and Decimal(value) > 0
    )


def require_public_query(path: str, params: Mapping[str, str] | None) -> None:
    values = dict(params or {})
    require_shape(path in PUBLIC and values == PUBLIC[path])


def require_signed_query(method: str, path: str, params: Mapping[str, str] | None) -> None:
    p = dict(params or {})
    require_shape(
        all(
            isinstance(k, str) and isinstance(v, str) and k.isascii() and v.isascii()
            for k, v in p.items()
        )
    )
    if method == "GET" and path in GLOBAL_GET:
        require_shape(not p)
    elif method == "GET" and path == "/fapi/v1/income":
        require_shape(set(p) == {"startTime", "endTime", "limit", "page"})
        require_shape(
            integer(p["startTime"])
            and integer(p["endTime"])
            and int(p["startTime"]) <= int(p["endTime"])
            and p["limit"] == "1000"
            and p["page"] in {str(i) for i in range(1, 11)}
        )
    elif method == "GET" and path == "/fapi/v1/order":
        require_shape(set(p) in ({"symbol", "origClientOrderId"}, {"symbol", "orderId"}))
        require_shape(
            p["symbol"] == "BTCUSDT"
            and (
                identifier(p["origClientOrderId"])
                if "origClientOrderId" in p
                else integer(p["orderId"], positive=True)
            )
        )
    elif method == "DELETE" and path == "/fapi/v1/order":
        require_shape(
            set(p) == {"symbol", "origClientOrderId"}
            and p["symbol"] == "BTCUSDT"
            and identifier(p["origClientOrderId"])
        )
    elif method in {"GET", "DELETE"} and path == "/fapi/v1/algoOrder":
        require_shape(set(p) == {"clientAlgoId"} and identifier(p["clientAlgoId"]))
    elif method == "POST" and path == "/fapi/v1/order":
        entry = {"symbol", "side", "type", "quantity", "price", "timeInForce", "newClientOrderId"}
        exit_fields = {"symbol", "side", "type", "quantity", "reduceOnly", "newClientOrderId"}
        require_shape(set(p) in (entry, exit_fields))
        require_shape(
            p["symbol"] == "BTCUSDT"
            and p["side"] in {"BUY", "SELL"}
            and amount(p["quantity"])
            and identifier(p["newClientOrderId"])
        )
        if set(p) == entry:
            require_shape(
                p["type"] == "LIMIT" and p["timeInForce"] in {"GTC", "GTX"} and amount(p["price"])
            )
        else:
            require_shape(p["type"] == "MARKET" and p["reduceOnly"] == "true")
    elif method == "POST" and path == "/fapi/v1/algoOrder":
        require_shape(
            set(p)
            == {
                "symbol",
                "side",
                "algoType",
                "type",
                "triggerPrice",
                "closePosition",
                "clientAlgoId",
                "workingType",
                "priceProtect",
            }
        )
        require_shape(
            p["symbol"] == "BTCUSDT"
            and p["side"] in {"BUY", "SELL"}
            and p["algoType"] == "CONDITIONAL"
            and p["type"] in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
            and amount(p["triggerPrice"])
            and p["closePosition"] == "true"
            and identifier(p["clientAlgoId"])
            and p["workingType"] == "MARK_PRICE"
            and p["priceProtect"] == "FALSE"
        )
    else:
        require_shape(False)


def require_wire_query(method: str, path: str, query: SecretStr, key: SecretStr | None) -> None:
    raw = query.get_secret_value()
    require_shape(
        len(raw) <= 4096 and raw.isascii() and re.search(r"%(?![0-9a-fA-F]{2})", raw) is None
    )
    try:
        pairs = parse_qsl(
            raw, keep_blank_values=True, strict_parsing=True, errors="strict", max_num_fields=32
        )
    except (ValueError, UnicodeError):
        require_shape(False)
        return
    p = dict(pairs)
    require_shape(len(pairs) == len(p) and all(k.isascii() and v.isascii() for k, v in pairs))
    if method == "GET" and path in PUBLIC:
        require_shape(key is None)
        require_public_query(path, p)
    elif method in {"POST", "PUT", "DELETE"} and path == "/fapi/v1/listenKey":
        require_shape(not p and key is not None and bool(key.get_secret_value()))
    else:
        require_shape(key is not None and bool(key.get_secret_value()) and SIGNING <= p.keys())
        require_shape(
            integer(p["timestamp"])
            and p["recvWindow"] == "5000"
            and re.fullmatch(r"[a-f0-9]{64}", p["signature"]) is not None
        )
        require_signed_query(method, path, {k: v for k, v in p.items() if k not in SIGNING})


def contract_report() -> dict[str, Any]:
    return dict(
        version=CONTRACT_VERSION,
        scope="OWNED_ISOLATED_BTC_TP_QUERY_SHAPES_ONLY",
        sdk_source_commit="f75b4a52c5aebf52b1c6ad671b0ce53b96f8e179",
        request_cost_upper_bounds_proven=False,
        execution_approved=False,
        remaining_execution_blockers=[
            "ACCOUNT_ORDER_USAGE_UNKNOWN",
            "EXTERNAL_IP_CAPACITY_UNRESERVED",
            "BOOK_TICKER_HEADER_NONAUTHORITATIVE",
            "VENUE_COST_UPPER_BOUND_NOT_PROVEN",
            "DEFAULT_REAL_EXECUTION_DISABLED",
        ],
    )
