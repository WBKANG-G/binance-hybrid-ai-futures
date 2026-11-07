"""GET-only evidence for legacy cancelled entries carrying impossible local exposure.

Only a temporary history-analysis copy is adjusted. Stored orders, unresolved
requests, risk baselines and locks are never repaired or acknowledged here.
"""

from decimal import Decimal

from integrations.binance_usdm.demo import DemoClient, DemoFailure
from integrations.binance_usdm.demo_snapshot import signed_number, snapshot
from packages.domain.contracts import OrderState, PositionState
from packages.persistence.demo import DemoStore
from services.execution.demo_history_review import review_flat_history
from services.execution.demo_models import DemoLedger, DemoSnapshot


class CancelledHistoryBlocked(DemoFailure):
    pass


def _confirm_cancelled(ledger: DemoLedger, remote: DemoSnapshot, keys: list[str]) -> None:
    if (
        any(remote.positions.values())
        or any(r["status"] in {"NEW", "PARTIALLY_FILLED"} for r in remote.orders.values())
        or any(r["algoStatus"] == "NEW" for r in remote.algos.values())
        or remote.leverage.get("BTCUSDT") != 2
        or remote.margin_types.get("BTCUSDT") != "ISOLATED"
    ):
        raise CancelledHistoryBlocked("CURRENT_EXPOSURE_OR_SETTINGS_MISMATCH")
    for key in keys:
        order = ledger.orders[key]
        row = remote.orders.get(order.client_id)
        if (
            row is None
            or row["status"] != "CANCELED"
            or row["clientOrderId"] != order.client_id
            or order.server_order_id is None
            or row["orderId"] != order.server_order_id
            or row["symbol"] != "BTCUSDT"
            or row["symbol"] != order.signal.symbol
            or row["side"] != ("BUY" if order.signal.direction == "LONG" else "SELL")
            or row["positionSide"] != "BOTH"
            or row["type"] != "LIMIT"
            or row["reduceOnly"] != "false"
            or signed_number(row["origQty"]) != order.quantity
            or signed_number(row["executedQty"]) != 0
            or signed_number(row["price"]) != order.signal.suggested_limit_price
        ):
            raise CancelledHistoryBlocked("CANCELLED_ZERO_FILL_NOT_CONFIRMED")


def review_cancelled_history(
    store: DemoStore, client: DemoClient, ledger: DemoLedger
) -> dict[str, object]:
    if (
        client.settings.demo_trading_enabled
        or client.settings.margin_mode != "ISOLATED"
        or client.settings.symbols != ("BTCUSDT",)
        or ledger.margin_mode != "ISOLATED"
        or ledger.symbols != ("BTCUSDT",)
        or ledger.policy != client.settings.policy
        or not ledger.paused
        or ledger.risk_state.value != "EMERGENCY"
        or ledger.last_reconciled is None
        or ledger.value.as_of != ledger.last_reconciled
        or any(ledger.positions.values())
    ):
        raise CancelledHistoryBlocked("LEGACY_HISTORY_CONTEXT_MISMATCH")
    keys = [key for key, order in ledger.orders.items() if order.open_quantity]
    if not keys or any(
        order.state != OrderState.CANCELED
        or order.filled != 0
        or order.remaining != 0
        or order.signal.symbol != "BTCUSDT"
        or order.open_quantity != order.quantity
        or order.position_state != PositionState.EXIT_PENDING
        or order.exit_state != "UNKNOWN"
        or order.protected
        for key in keys
        for order in [ledger.orders[key]]
    ):
        raise CancelledHistoryBlocked("LEGACY_CANCELLED_SHAPE_MISMATCH")
    original = ledger.model_dump_json()
    first = snapshot(client, ledger, store.now())
    _confirm_cancelled(ledger, first, keys)
    second = snapshot(client, ledger, store.now())
    _confirm_cancelled(ledger, second, keys)
    if first.fingerprint != second.fingerprint:
        raise CancelledHistoryBlocked("CANCELLED_HISTORY_SNAPSHOT_CHANGED")
    # This projection is solely for GET-only history evidence. No coordinator,
    # recovery acknowledgement, lease or store.change receives it.
    projection = ledger.model_copy(deep=True)
    for key in keys:
        projection.orders[key].open_quantity = Decimal(0)
    report = review_flat_history(store, client, projection)
    final = snapshot(client, ledger, store.now())
    _confirm_cancelled(ledger, final, keys)
    if first.fingerprint != final.fingerprint:
        raise CancelledHistoryBlocked("CANCELLED_HISTORY_SNAPSHOT_CHANGED")
    if ledger.model_dump_json() != original:
        raise CancelledHistoryBlocked("LOCAL_INPUT_CHANGED")
    report["legacy_observation_anchor_utc"] = report.pop("local_flat_anchor_utc")
    report.update(
        review="LEGACY_CANCELLED_HISTORY_EVIDENCE_ONLY",
        confirmed_cancelled_zero_fill_orders=len(keys),
        validated_exchange_fingerprint=final.fingerprint,
        local_cancelled_exposure_records_unchanged=True,
        local_ledger_repaired=False,
        unresolved_requests_acknowledged=False,
        risk_state=ledger.risk_state.value,
        paused=ledger.paused,
    )
    return report
