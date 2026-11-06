"""Narrow initial DEMO-validation fill proof, not a relaxation of coherent_snapshot."""

import hashlib
import json
from collections.abc import Callable
from decimal import Decimal

from integrations.binance_usdm.demo import DemoClient, DemoFailure
from integrations.binance_usdm.demo_snapshot import signed_number
from packages.domain.contracts import OrderState, PositionState, RiskState, TradingMode
from services.execution.demo_execution_proof import execution_proof
from services.execution.demo_models import DemoLedger, DemoSnapshot, InitialFillSettleReason
from services.execution.demo_reconciliation import coherent_snapshot


class InitialFillBlocked(DemoFailure):
    def __init__(self, code: InitialFillSettleReason):
        self.code = code
        super().__init__("Initial fill evidence not verified")


def settle_initial_fill(
    first: DemoSnapshot,
    second: DemoSnapshot,
    ledger: DemoLedger,
    client: DemoClient,
    third_read: Callable[[], DemoSnapshot],
) -> tuple[DemoSnapshot, str, str]:
    candidates = [
        (key, order)
        for key, order in ledger.orders.items()
        if order.remaining or order.open_quantity
    ]
    if (
        not client.settings.demo_trading_enabled
        or client.settings.policy != ledger.policy
        or client.settings.margin_mode != "ISOLATED"
        or client.settings.symbols != ("BTCUSDT",)
        or ledger.margin_mode != "ISOLATED"
        or ledger.symbols != ("BTCUSDT",)
        or ledger.risk_state != RiskState.NORMAL
        or ledger.paused
        or any(ledger.positions.values())
        or len(candidates) != 1
    ):
        raise InitialFillBlocked("NOT_ELIGIBLE")
    key, order = candidates[0]
    if (
        order.signal.environment != TradingMode.DEMO
        or order.signal.purpose != "DEMO_VALIDATION"
        or order.signal.symbol != "BTCUSDT"
        or order.state not in {OrderState.SUBMITTING, OrderState.ACKNOWLEDGED, OrderState.UNKNOWN}
        or order.position_state != PositionState.ENTRY_PENDING
        or order.filled
        or order.open_quantity
        or order.remaining != order.quantity
        or order.algo_id
        or order.tp_algo_id
        or order.exit_id
        or ledger.entry_quantity_caps.get(key) != order.quantity
        or order.notional > Decimal(100)
    ):
        raise InitialFillBlocked("NOT_ELIGIBLE")
    third = third_read()  # Exactly one extra observation; never submit/retry an order.
    probe = ledger.model_copy(deep=True)
    try:
        bounded = coherent_snapshot(second, third, probe)
    except DemoFailure:
        raise InitialFillBlocked("UNSTABLE_THIRD") from None

    def active(snap: DemoSnapshot) -> dict[str, Decimal]:
        return {symbol: quantity for symbol, quantity in snap.positions.items() if quantity}

    def prices(snap: DemoSnapshot) -> dict[str, Decimal]:
        return {symbol: snap.entry_prices[symbol] for symbol in active(snap)}

    direction = 1 if order.signal.direction == "LONG" else -1
    row = third.orders.get(order.client_id)
    if not row:
        raise InitialFillBlocked("ORDER_PROOF_MISMATCH")
    quantity = signed_number(row["executedQty"])
    expected_status = "FILLED" if quantity == order.quantity else "PARTIALLY_FILLED"
    if (
        not 0 < quantity <= order.quantity
        or active(third) != {"BTCUSDT": quantity * direction}
        or prices(third) != {"BTCUSDT": signed_number(row["avgPrice"])}
        or row["status"] != expected_status
        or (direction == 1 and signed_number(row["avgPrice"]) > order.signal.suggested_limit_price)
        or (direction == -1 and signed_number(row["avgPrice"]) < order.signal.suggested_limit_price)
        or (order.server_order_id is not None and row["orderId"] != order.server_order_id)
    ):
        raise InitialFillBlocked("POSITION_PROOF_MISMATCH")
    immutable = {
        "clientOrderId": order.client_id,
        "orderId": row["orderId"],
        "symbol": "BTCUSDT",
        "side": "BUY" if direction == 1 else "SELL",
        "type": "LIMIT",
        "reduceOnly": "false",
        "positionSide": "BOTH",
        "origQty": str(order.quantity),
        "price": str(order.signal.suggested_limit_price),
    }
    other = {k: v for k, v in first.orders.items() if k != order.client_id}
    if any(v["status"] in {"NEW", "PARTIALLY_FILLED"} for v in other.values()):
        raise InitialFillBlocked("SNAPSHOT_PROOF_MISMATCH")
    for snap in (first, second, third):
        own = snap.orders.get(order.client_id)
        if (
            not own
            or any(
                (signed_number(own[k]) != signed_number(value))
                if k in {"origQty", "price"}
                else own[k] != value
                for k, value in immutable.items()
            )
            or signed_number(own["executedQty"]) not in {Decimal(0), quantity}
            or own["status"]
            != ("NEW" if signed_number(own["executedQty"]) == 0 else expected_status)
            or {k: v for k, v in snap.orders.items() if k != order.client_id} != other
            or snap.algos != first.algos
            or any(
                v["algoStatus"] not in {"CANCELED", "EXPIRED", "FINISHED", "REJECTED"}
                for v in snap.algos.values()
            )
            or snap.margin_types != ledger.margin_types
            or snap.leverage != ledger.verified_leverage
            or snap.transfers != ledger.income_seen
            or active(snap) not in ({}, {"BTCUSDT": quantity * direction})
        ):
            raise InitialFillBlocked("SNAPSHOT_PROOF_MISMATCH")
        if active(snap):
            if (
                prices(snap) != prices(third)
                or snap.isolated_wallets != third.isolated_wallets
                or snap.isolated_wallets.get("BTCUSDT", 0) <= 0
                or snap.isolated_margins.get("BTCUSDT", 0) <= 0
                or snap.liquidation_prices.get("BTCUSDT", 0) <= 0
            ):
                raise InitialFillBlocked("COLLATERAL_PROOF_MISMATCH")
    try:
        fills = client.request(
            "GET",
            "/fapi/v1/userTrades",
            {"symbol": "BTCUSDT", "orderId": row["orderId"], "limit": "1000"},
        )
    except DemoFailure:
        raise InitialFillBlocked("PROOF_QUERY_FAILED") from None
    try:
        executions, commission = execution_proof(
            fills,
            order_id=row["orderId"],
            side=immutable["side"],
            quantity=quantity,
            price=signed_number(row["avgPrice"]),
            fee_rate=ledger.policy.fee_rate,
            now_ms=client.clock_ms() + client.offset,
            max_age_ms=ledger.policy.max_snapshot_age_seconds * 1000,
        )
    except (DemoFailure, ValueError, TypeError):
        raise InitialFillBlocked("EXECUTIONS_PROOF_MISMATCH") from None
    before_cash = ledger.value.equity
    after_cash = before_cash - commission
    if (
        third.wallet_balance != after_cash
        or first.wallet_balance not in {before_cash, after_cash}
        or second.wallet_balance not in {before_cash, after_cash}
        or (first.wallet_balance == after_cash and second.wallet_balance != after_cash)
    ):
        raise InitialFillBlocked("CASH_PROOF_MISMATCH")
    # Require an observable initial fill/fee transition, not arbitrary drift of
    # a previously open position or fabricated fingerprint changes.
    if not (
        active(first) != active(second)
        or first.orders[order.client_id]["executedQty"]
        != second.orders[order.client_id]["executedQty"]
        or first.wallet_balance != second.wallet_balance
    ):
        raise InitialFillBlocked("NO_INITIAL_TRANSITION")
    active_views = [s for s in (first, second, third) if active(s)]
    bounded = bounded.model_copy(
        update={
            "equity": min(s.equity for s in (first, second, third)),
            "available_balance": min(s.available_balance for s in (first, second, third)),
            "margin_used": max(s.margin_used for s in (first, second, third)),
            "isolated_margins": {
                "BTCUSDT": min(s.isolated_margins["BTCUSDT"] for s in active_views)
            },
            "liquidation_prices": {
                "BTCUSDT": (max if direction == 1 else min)(
                    s.liquidation_prices["BTCUSDT"] for s in active_views
                )
            },
        }
    )
    ledger.high_water = max(
        ledger.high_water, probe.high_water, first.equity, second.equity, third.equity
    )
    evidence = hashlib.sha256(
        json.dumps(
            {
                "order": key,
                "executions": executions,
                "quantity": str(quantity),
                "price": str(row["avgPrice"]),
                "commission": str(commission),
                "first": first.fingerprint,
                "second": second.fingerprint,
                "third": third.fingerprint,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return bounded, key, evidence
