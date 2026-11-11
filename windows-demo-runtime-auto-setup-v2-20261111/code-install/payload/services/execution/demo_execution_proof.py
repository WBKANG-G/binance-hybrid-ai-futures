"""Bounded signed executions for one initial fill at a single verified price."""

from decimal import Decimal
from typing import Any

from integrations.binance_usdm.demo import DemoFailure
from integrations.binance_usdm.demo_snapshot import signed_number

MAX_EXECUTIONS = 32


def execution_proof(
    rows: Any,
    *,
    order_id: str,
    side: str,
    quantity: Decimal,
    price: Decimal,
    fee_rate: Decimal,
    now_ms: int,
    max_age_ms: int,
) -> tuple[list[dict[str, str | int]], Decimal]:
    """No partial results, guessed fees, weighted-price rounding or deduplication."""
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_EXECUTIONS:
        raise DemoFailure("Initial execution set invalid")
    result: list[dict[str, str | int]] = []
    ids: set[str] = set()
    total = Decimal(0)
    commission = Decimal(0)
    for row in rows:
        if not isinstance(row, dict):
            raise DemoFailure("Initial execution set invalid")
        identifier = row.get("id")
        stamp = row.get("time")
        if (
            type(identifier) not in (int, str)
            or not str(identifier).isdigit()
            or str(identifier) in ids
            or type(row.get("orderId")) not in (int, str)
            or str(row.get("orderId")) != order_id
            or row.get("symbol") != "BTCUSDT"
            or row.get("positionSide") != "BOTH"
            or row.get("side") != side
            or row.get("commissionAsset") != "USDT"
            or type(stamp) is not int
            or not 0 <= now_ms - stamp <= max_age_ms
        ):
            raise DemoFailure("Initial execution identity/time invalid")
        qty = signed_number(row.get("qty"))
        actual_price = signed_number(row.get("price"))
        fee = signed_number(row.get("commission"))
        if (
            qty <= 0
            or actual_price != price
            or not 0 <= fee <= qty * price * fee_rate
            or signed_number(row.get("realizedPnl")) != 0
        ):
            raise DemoFailure("Initial execution amount invalid")
        ids.add(str(identifier))
        total += qty
        commission += fee
        if total > quantity:
            raise DemoFailure("Initial execution quantity exceeded")
        result.append(
            {
                "id": str(identifier),
                "time": stamp,
                "quantity": str(qty),
                "price": str(actual_price),
                "commission": str(fee),
            }
        )
    if total != quantity:
        raise DemoFailure("Initial execution quantity incomplete")
    return sorted(result, key=lambda r: (int(r["time"]), int(r["id"]))), commission
