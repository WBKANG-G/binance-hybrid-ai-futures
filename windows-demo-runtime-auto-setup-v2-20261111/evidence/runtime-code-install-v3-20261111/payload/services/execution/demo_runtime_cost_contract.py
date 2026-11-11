"""Pinned documented cost coverage for approved shapes; never venue certification."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from packages.persistence.demo_order_quota import quota_require
from services.execution import demo_query_contract as query

POLICY = "ISOLATED_BTC_TP_DOCUMENTED_COST_V1"
SDK_COMMIT = "f75b4a52c5aebf52b1c6ad671b0ce53b96f8e179"


@dataclass(frozen=True)
class CostRule:
    method: str
    path: str
    modeled_weight: int
    documented_ip_weight: int
    sdk_file: str
    sdk_method: str


# Audited independently of the operational model and the synthetic trace oracle.
# Mutations retain the existing conservative 40 even where SDK IP cost is 0 or 1.
RULES = (
    CostRule("DELETE", "/fapi/v1/algoOrder", 40, 1, "trade_api.py", "cancel_algo_order"),
    CostRule(
        "DELETE", "/fapi/v1/listenKey", 40, 1, "user_data_streams_api.py", "close_user_data_stream"
    ),
    CostRule("DELETE", "/fapi/v1/order", 40, 1, "trade_api.py", "cancel_order"),
    CostRule(
        "GET", "/fapi/v1/accountConfig", 5, 5, "account_api.py", "futures_account_configuration"
    ),
    CostRule("GET", "/fapi/v1/algoOrder", 1, 1, "trade_api.py", "query_algo_order"),
    CostRule("GET", "/fapi/v1/exchangeInfo", 1, 1, "market_data_api.py", "exchange_information"),
    CostRule("GET", "/fapi/v1/income", 30, 30, "account_api.py", "get_income_history"),
    CostRule("GET", "/fapi/v1/klines", 1, 1, "market_data_api.py", "kline_candlestick_data"),
    CostRule(
        "GET", "/fapi/v1/openAlgoOrders", 40, 40, "trade_api.py", "current_all_algo_open_orders"
    ),
    CostRule("GET", "/fapi/v1/openOrders", 40, 40, "trade_api.py", "current_all_open_orders"),
    CostRule("GET", "/fapi/v1/order", 1, 1, "trade_api.py", "query_order"),
    CostRule("GET", "/fapi/v1/premiumIndex", 1, 1, "market_data_api.py", "mark_price"),
    CostRule("GET", "/fapi/v1/symbolConfig", 5, 5, "account_api.py", "symbol_configuration"),
    CostRule(
        "GET", "/fapi/v1/ticker/bookTicker", 2, 2, "market_data_api.py", "symbol_order_book_ticker"
    ),
    CostRule("GET", "/fapi/v1/time", 1, 1, "market_data_api.py", "check_server_time"),
    CostRule("GET", "/fapi/v3/account", 5, 5, "account_api.py", "account_information_v3"),
    CostRule("GET", "/fapi/v3/positionRisk", 5, 5, "trade_api.py", "position_information_v3"),
    CostRule("POST", "/fapi/v1/algoOrder", 40, 0, "trade_api.py", "new_algo_order"),
    CostRule(
        "POST", "/fapi/v1/listenKey", 40, 1, "user_data_streams_api.py", "start_user_data_stream"
    ),
    CostRule("POST", "/fapi/v1/order", 40, 0, "trade_api.py", "new_order"),
    CostRule(
        "PUT", "/fapi/v1/listenKey", 40, 1, "user_data_streams_api.py", "keepalive_user_data_stream"
    ),
)

_PUBLIC = (
    ("/fapi/v1/time", ()),
    ("/fapi/v1/exchangeInfo", ()),
    ("/fapi/v1/klines", (("interval", "1m"), ("limit", "2"), ("symbol", "BTCUSDT"))),
    ("/fapi/v1/premiumIndex", (("symbol", "BTCUSDT"),)),
    ("/fapi/v1/ticker/bookTicker", (("symbol", "BTCUSDT"),)),
)
_GLOBAL = frozenset(
    {
        "/fapi/v1/accountConfig",
        "/fapi/v1/symbolConfig",
        "/fapi/v3/account",
        "/fapi/v3/positionRisk",
        "/fapi/v1/openOrders",
        "/fapi/v1/openAlgoOrders",
    }
)


def require_runtime_cost_contract(costs: Mapping[tuple[str, str], int]) -> None:
    quota_require(
        query.CONTRACT_VERSION == "ISOLATED_BTC_TP_QUERY_V1"
        and query.PUBLIC == {path: dict(params) for path, params in _PUBLIC}
        and query.GLOBAL_GET == _GLOBAL
        and query.SIGNING == frozenset({"timestamp", "recvWindow", "signature"}),
        "RUNTIME_COST_CONTRACT_QUERY_POLICY_MISMATCH",
    )
    quota_require(
        set(costs) == {(rule.method, rule.path) for rule in RULES}
        and all(
            type(costs[(rule.method, rule.path)]) is int
            and costs[(rule.method, rule.path)] == rule.modeled_weight
            and rule.modeled_weight >= rule.documented_ip_weight
            for rule in RULES
        ),
        "RUNTIME_COST_CONTRACT_MODEL_MISMATCH",
    )


def cost_contract_report(costs: Mapping[tuple[str, str], int]) -> dict[str, Any]:
    require_runtime_cost_contract(costs)
    return dict(
        version=POLICY,
        query_policy=query.CONTRACT_VERSION,
        sdk_source_commit=SDK_COMMIT,
        scope="PINNED_DOCUMENTATION_FOR_APPROVED_QUERY_SHAPES_ONLY",
        documented_routes_covered=len(RULES),
        model_matches_reviewed_policy=True,
        conservative_mutation_weight_retained=40,
        actual_order_usage_verified=False,
        external_ip_capacity_reserved=False,
        venue_request_cost_upper_bounds_proven=False,
        execution_approved=False,
    )
