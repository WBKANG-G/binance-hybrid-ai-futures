"""Offline request contracts only. These objects cannot transmit orders."""

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from integrations.binance_usdm.filters import SymbolRules
from packages.domain.contracts import AlgoOrder, EntryOrder


@dataclass(frozen=True)
class OrderIntent:
    path: Literal["/fapi/v1/order", "/fapi/v1/algoOrder"]
    params: tuple[tuple[str, str], ...]


def client_id(value: str) -> str:
    if not re.fullmatch(r"[.A-Z:/a-z0-9_-]{1,36}", value):
        raise ValueError("Invalid client identifier")
    return value


def build_entry(order: EntryOrder, rules: SymbolRules, *, mark_price: Decimal) -> OrderIntent:
    order = EntryOrder.model_validate(order.model_dump())
    if order.symbol != rules.symbol:
        raise ValueError("Symbol/filter mismatch")
    rules.validate_entry(order.quantity, order.limit_price, mark_price)
    return OrderIntent(
        "/fapi/v1/order",
        tuple(
            {
                "symbol": order.symbol,
                "side": order.side,
                "type": "LIMIT",
                "quantity": format(order.quantity, "f"),
                "price": format(order.limit_price, "f"),
                "timeInForce": order.time_in_force,
                "newClientOrderId": client_id(order.client_order_id),
            }.items()
        ),
    )


def build_protection(
    order: AlgoOrder, rules: SymbolRules, *, working_type: Literal["MARK_PRICE", "CONTRACT_PRICE"]
) -> OrderIntent:
    order = AlgoOrder.model_validate(order.model_dump())
    if order.symbol != rules.symbol or working_type not in {"MARK_PRICE", "CONTRACT_PRICE"}:
        raise ValueError("Invalid protection policy")
    rules.validate_price(order.trigger_price)
    return OrderIntent(
        "/fapi/v1/algoOrder",
        tuple(
            {
                "symbol": order.symbol,
                "side": order.side,
                "algoType": "CONDITIONAL",
                "type": order.order_type,
                "triggerPrice": format(order.trigger_price, "f"),
                "closePosition": "true",
                "clientAlgoId": client_id(order.client_algo_id),
                "workingType": working_type,
            }.items()
        ),
    )
