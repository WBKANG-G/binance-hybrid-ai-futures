"""Strict position consistency with conservative mark-dependent valuation bounds."""

from integrations.binance_usdm.demo import DemoFailure
from integrations.binance_usdm.demo_snapshot import signed_number
from services.execution.demo_models import DemoLedger, DemoSnapshot

# Immutable wallet balance, collateral allocation, fills, orders, mode, leverage,
# entry price and transfers remain in the exact structural comparison.
VALUATION_FIELDS = {
    "as_of",
    "fingerprint",
    "equity",
    "available_balance",
    "margin_used",
    "isolated_margins",
    "liquidation_prices",
}


def coherent_snapshot(
    first: DemoSnapshot, second: DemoSnapshot, ledger: DemoLedger
) -> DemoSnapshot:
    if first.fingerprint == second.fingerprint:
        return second
    error = DemoFailure("DEMO REST snapshots changed mid-reconciliation")
    if (
        ledger.margin_mode != "ISOLATED"
        or first.wallet_balance is None
        or second.wallet_balance is None
    ):
        raise error
    a = first.model_dump(exclude=VALUATION_FIELDS)
    b = second.model_dump(exclude=VALUATION_FIELDS)
    if a != b:
        raise error
    # Do not relax flat/new-entry admission, fabricated hash changes, missing
    # collateral or unowned/manual exposure. Normal merge still checks identity.
    active = {s: q for s, q in second.positions.items() if q}
    if not active or all(
        getattr(first, key) == getattr(second, key)
        for key in VALUATION_FIELDS - {"as_of", "fingerprint"}
    ):
        raise error
    if (
        first.isolated_margins.keys() != second.isolated_margins.keys()
        or first.liquidation_prices.keys() != second.liquidation_prices.keys()
    ):
        raise error
    for symbol, quantity in active.items():
        owners = [
            o
            for o in ledger.orders.values()
            if o.signal.symbol == symbol and o.position_state.value != "CLOSED"
        ]
        if len(owners) != 1:
            raise error
        order = owners[0]
        row = second.orders.get(order.client_id)
        if not row or quantity * (1 if order.signal.direction == "LONG" else -1) <= 0:
            raise error
        if abs(quantity) != signed_number(row["executedQty"]) or abs(quantity) > order.quantity:
            raise error
        if any(
            snap.isolated_wallets.get(symbol, 0) <= 0
            or snap.isolated_margins.get(symbol, 0) <= 0
            or snap.liquidation_prices.get(symbol, 0) <= 0
            for snap in (first, second)
        ):
            raise error
    # Retain the latest exchange fingerprint for subsequent exact admission
    # checks while evaluating the least favorable values observed in this pair.
    # A transient higher equity is still a drawdown high-water observation.
    ledger.high_water = max(ledger.high_water, first.equity, second.equity)
    return second.model_copy(
        update={
            "equity": min(first.equity, second.equity),
            "available_balance": min(first.available_balance, second.available_balance),
            "margin_used": max(first.margin_used, second.margin_used),
            "isolated_margins": {
                s: min(first.isolated_margins[s], second.isolated_margins[s])
                for s in second.isolated_margins
            },
            "liquidation_prices": {
                s: (max if active.get(s, 0) > 0 else min)(
                    first.liquidation_prices[s], second.liquidation_prices[s]
                )
                for s in second.liquidation_prices
            },
        }
    )
