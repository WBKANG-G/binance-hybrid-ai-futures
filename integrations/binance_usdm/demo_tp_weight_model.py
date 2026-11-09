"""Explicit TP route cost assumptions; not an exchange upper-bound certificate.

Keep the independent offline trace oracle and synthetic meter separate.
No production caller can turn these assumptions into execution authorization.
"""

MODELED_TP_ROUTE_COSTS: dict[tuple[str, str], int] = {
    ("DELETE", "/fapi/v1/algoOrder"): 40,
    ("DELETE", "/fapi/v1/listenKey"): 40,
    ("DELETE", "/fapi/v1/order"): 40,
    ("GET", "/fapi/v1/accountConfig"): 5,
    ("GET", "/fapi/v1/algoOrder"): 1,
    ("GET", "/fapi/v1/exchangeInfo"): 1,
    ("GET", "/fapi/v1/income"): 30,
    ("GET", "/fapi/v1/klines"): 1,
    ("GET", "/fapi/v1/openAlgoOrders"): 40,
    ("GET", "/fapi/v1/openOrders"): 40,
    ("GET", "/fapi/v1/order"): 1,
    ("GET", "/fapi/v1/premiumIndex"): 1,
    ("GET", "/fapi/v1/symbolConfig"): 5,
    ("GET", "/fapi/v1/ticker/bookTicker"): 2,
    ("GET", "/fapi/v1/time"): 1,
    ("GET", "/fapi/v3/account"): 5,
    ("GET", "/fapi/v3/positionRisk"): 5,
    ("POST", "/fapi/v1/algoOrder"): 40,
    ("POST", "/fapi/v1/listenKey"): 40,
    ("POST", "/fapi/v1/order"): 40,
    ("PUT", "/fapi/v1/listenKey"): 40,
}
