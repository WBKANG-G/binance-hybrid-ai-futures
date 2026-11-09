"""Filter arithmetic uses tickSize/stepSize, never display precision."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any


def number(value: object, *, allow_zero: bool = False) -> Decimal:
    if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
        raise ValueError("Expected decimal string")
    try:
        value = Decimal(value)
    except InvalidOperation:
        raise ValueError("Invalid decimal amount") from None
    if not value.is_finite() or value < 0 or (value == 0 and not allow_zero):
        raise ValueError("Invalid filter amount")
    return value


@dataclass(frozen=True)
class SymbolRules:
    symbol: str
    min_price: Decimal
    max_price: Decimal
    tick_size: Decimal
    min_qty: Decimal
    max_qty: Decimal
    step_size: Decimal
    min_notional: Decimal
    multiplier_down: Decimal
    multiplier_up: Decimal

    @classmethod
    def from_exchange_info(cls, data: dict[str, Any], symbol: str) -> "SymbolRules":
        matches = [item for item in data.get("symbols", []) if item.get("symbol") == symbol]
        if len(matches) != 1:
            raise ValueError("Symbol metadata missing or ambiguous")
        item = matches[0]
        if (
            item.get("status"),
            item.get("contractType"),
            item.get("quoteAsset"),
            item.get("marginAsset"),
        ) != ("TRADING", "PERPETUAL", "USDT", "USDT"):
            raise ValueError("Symbol is not eligible for USDT perpetual analysis")
        filters = {f["filterType"]: f for f in item["filters"]}
        try:
            p = filters["PRICE_FILTER"]
            q = filters["LOT_SIZE"]
            n = filters["MIN_NOTIONAL"]
            r = filters["PERCENT_PRICE"]
            result = cls(
                symbol,
                number(p["minPrice"], allow_zero=True),
                number(p["maxPrice"]),
                number(p["tickSize"]),
                number(q["minQty"]),
                number(q["maxQty"]),
                number(q["stepSize"]),
                number(n["notional"]),
                number(r["multiplierDown"]),
                number(r["multiplierUp"]),
            )
        except (KeyError, TypeError):
            raise ValueError("Required symbol filters missing") from None
        if (
            result.min_price > result.max_price
            or result.min_qty > result.max_qty
            or result.multiplier_down > result.multiplier_up
        ):
            raise ValueError("Invalid filter bounds")
        return result

    def floor_price(self, value: Decimal) -> Decimal:
        with localcontext() as ctx:
            ctx.prec = 80
            return (number(value) // self.tick_size) * self.tick_size

    def floor_quantity(self, value: Decimal) -> Decimal:
        with localcontext() as ctx:
            ctx.prec = 80
            return (number(value) // self.step_size) * self.step_size

    def validate_price(self, price: Decimal) -> None:
        value = number(price)
        with localcontext() as ctx:
            ctx.prec = 80
            if not self.min_price <= value <= self.max_price or value % self.tick_size != 0:
                raise ValueError("Price violates PRICE_FILTER")

    def validate_entry(self, quantity: Decimal, price: Decimal, mark_price: Decimal) -> None:
        self.validate_price(price)
        quantity = number(quantity)
        mark_price = number(mark_price)
        with localcontext() as ctx:
            ctx.prec = 80
            if not self.min_qty <= quantity <= self.max_qty or quantity % self.step_size != 0:
                raise ValueError("Quantity violates LOT_SIZE")
            if quantity * price < self.min_notional:
                raise ValueError("Entry violates MIN_NOTIONAL")
            if not mark_price * self.multiplier_down <= price <= mark_price * self.multiplier_up:
                raise ValueError("Entry violates PERCENT_PRICE")
