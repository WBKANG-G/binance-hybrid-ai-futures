"""Bounded GET-only history evidence, never a daily baseline or lock reset."""

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, DecimalException
from zoneinfo import ZoneInfo

from integrations.binance_usdm.demo import DemoClient, DemoFailure
from integrations.binance_usdm.demo_snapshot import signed_number, snapshot
from packages.domain.contracts import OrderState
from packages.persistence.demo import DemoStore
from services.execution.demo_day_review import income_window
from services.execution.demo_models import DemoLedger
from services.execution.demo_recovery_budget import AuditBudgetExceeded, RecoveryReadClient

CODES = frozenset(
    {
        "INVALID_HISTORY_WINDOW",
        "REQUEST_AUDIT_TIME_BUDGET",
        "REQUEST_AUDIT_QUERY_BUDGET",
        "REQUEST_AUDIT_CLOCK_BUDGET",
        "HISTORY_QUERY_LIMIT",
        "INVALID_TRADE_RESPONSE",
        "TRADE_WINDOW_SATURATED",
        "INVALID_TRADE_IDENTITY",
        "INVALID_TRADE_TIME",
        "INVALID_TRADE_NUMBER",
        "CONFLICTING_TRADE_ID",
        "HISTORY_CHANGED",
        "ACCOUNT_CHANGED",
        "CURRENT_EXPOSURE",
        "INVALID_LOCAL_FLAT_ANCHOR",
        "READ_ONLY_CONTEXT_REQUIRED",
        "ANCHOR_AFTER_BOUNDARY",
        "WALLET_MISSING",
    }
)


class HistoryBlocked(DemoFailure):
    def __init__(self, code: str):
        super().__init__(code if code in CODES else "HISTORY_EVIDENCE_BLOCKED")


def trades_window(client: DemoClient, start_ms: int, end_ms: int) -> dict[str, dict[str, str]]:
    return _trades_window(client, start_ms, end_ms, max_days=7)


def historical_trades_window(
    client: DemoClient, start_ms: int, end_ms: int
) -> dict[str, dict[str, str]]:
    # Full retained history: no moving cutoff that drops old orders. Each query
    # spans at most one day. The global 40-call cap includes saturation splits.
    return _trades_window(client, start_ms, end_ms, max_days=31)


def _trades_window(
    client: DemoClient, start_ms: int, end_ms: int, *, max_days: int
) -> dict[str, dict[str, str]]:
    if (
        client.settings.demo_trading_enabled
        or start_ms < 0
        or not 0 <= end_ms - start_ms <= max_days * 86400000
    ):
        raise HistoryBlocked("INVALID_HISTORY_WINDOW")
    pending = []
    cursor = start_ms
    while cursor <= end_ms:
        end = min(end_ms, cursor + 86400000 - 1)
        pending.append((cursor, end))
        cursor = end + 1
    result: dict[str, dict[str, str]] = {}
    calls = 0
    while pending:
        left, right = pending.pop()
        calls += 1
        if calls > 40:
            raise HistoryBlocked("HISTORY_QUERY_LIMIT")
        raw = client.request(
            "GET",
            "/fapi/v1/userTrades",
            {"symbol": "BTCUSDT", "startTime": str(left), "endTime": str(right), "limit": "1000"},
        )
        if not isinstance(raw, list) or len(raw) > 1000:
            raise HistoryBlocked("INVALID_TRADE_RESPONSE")
        if len(raw) == 1000:
            if left == right:
                raise HistoryBlocked("TRADE_WINDOW_SATURATED")
            middle = (left + right) // 2
            pending.extend([(left, middle), (middle + 1, right)])
            continue
        for row in raw:
            if (
                not isinstance(row, dict)
                or row.get("symbol") != "BTCUSDT"
                or row.get("positionSide") != "BOTH"
                or row.get("side") not in ("BUY", "SELL")
                or row.get("commissionAsset") != "USDT"
                or type(row.get("id")) not in (int, str)
                or not str(row.get("id")).isdigit()
                or type(row.get("orderId")) not in (int, str)
                or not str(row.get("orderId")).isdigit()
            ):
                raise HistoryBlocked("INVALID_TRADE_IDENTITY")
            stamp = row.get("time")
            if type(stamp) is not int or not left <= stamp <= right:
                raise HistoryBlocked("INVALID_TRADE_TIME")
            try:
                qty = signed_number(row.get("qty"))
                price = signed_number(row.get("price"))
                commission = signed_number(row.get("commission"))
                pnl = signed_number(row.get("realizedPnl"))
                if qty <= 0 or price <= 0 or commission < 0:
                    raise ValueError("Invalid trade amount")
            except (DemoFailure, DecimalException, ValueError, TypeError):
                raise HistoryBlocked("INVALID_TRADE_NUMBER") from None
            item = {
                "time": str(stamp),
                "side": str(row["side"]),
                "order_id": str(row["orderId"]),
                "quantity": str(qty),
                "price": str(price),
                "commission": str(commission),
                "realized_pnl": str(pnl),
            }
            key = str(row["id"])
            if key in result and result[key] != item:
                raise HistoryBlocked("CONFLICTING_TRADE_ID")
            result[key] = item
    return result


def review_flat_history(
    store: DemoStore, client: DemoClient, ledger: DemoLedger
) -> dict[str, object]:
    try:
        return _review_flat_history(store, client, ledger)
    except AuditBudgetExceeded as error:
        raise HistoryBlocked(error.code) from None


def _review_flat_history(
    store: DemoStore,
    client: DemoClient,
    ledger: DemoLedger,
    *,
    required_risk_state: str = "EMERGENCY",
) -> dict[str, object]:
    if (
        client.settings.demo_trading_enabled
        or client.settings.margin_mode != "ISOLATED"
        or client.settings.symbols != ("BTCUSDT",)
        or ledger.margin_mode != "ISOLATED"
        or ledger.symbols != ("BTCUSDT",)
        or not ledger.paused
        or required_risk_state not in {"NORMAL", "EMERGENCY"}
        or ledger.risk_state.value != required_risk_state
    ):
        raise HistoryBlocked("READ_ONLY_CONTEXT_REQUIRED")
    anchor = ledger.last_reconciled
    if (
        anchor is None
        or ledger.value.as_of != anchor
        or any(ledger.positions.values())
        or any(
            o.open_quantity
            or o.remaining
            or o.state
            not in {OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED, OrderState.EXPIRED}
            for o in ledger.orders.values()
        )
    ):
        raise HistoryBlocked("INVALID_LOCAL_FLAT_ANCHOR")
    now = store.now()
    boundary = datetime.combine(
        now.astimezone(ZoneInfo("Asia/Seoul")).date(), time(), ZoneInfo("Asia/Seoul")
    )
    if anchor >= boundary:
        raise HistoryBlocked("ANCHOR_AFTER_BOUNDARY")
    anchor_ms = int(anchor.timestamp() * 1000)
    # Include the recorded entry signal window so the prior closing fill itself
    # can be found, rather than silently omitting the gap before the flat anchor.
    window_start = min(
        [anchor, *(o.signal.signal_closed_candle_at for o in ledger.orders.values())]
    )
    start_ms = int(window_start.timestamp() * 1000)
    # Preserve the short-window contract and existing injected validators.
    extended = int(store.now().timestamp() * 1000) - start_ms > 7 * 86400000
    if extended:
        if int(store.now().timestamp() * 1000) - start_ms > 31 * 86400000:
            raise HistoryBlocked("INVALID_HISTORY_WINDOW")
        if required_risk_state != "NORMAL" or not isinstance(client, RecoveryReadClient):
            client = RecoveryReadClient(client)
    fetch_trades = historical_trades_window if extended else trades_window
    first = snapshot(client, ledger, store.now())
    end_ms = int(store.now().timestamp() * 1000)
    trades = fetch_trades(client, start_ms, end_ms)
    incomes = income_window(client, start_ms, end_ms)
    if trades != fetch_trades(client, start_ms, end_ms) or incomes != income_window(
        client, start_ms, end_ms
    ):
        raise HistoryBlocked("HISTORY_CHANGED")
    second = snapshot(client, ledger, store.now())
    if (
        first.fingerprint != second.fingerprint
        or second.as_of.astimezone(ZoneInfo("Asia/Seoul")).date()
        != now.astimezone(ZoneInfo("Asia/Seoul")).date()
    ):
        raise HistoryBlocked("ACCOUNT_CHANGED")
    if (
        any(second.positions.values())
        or any(r["status"] in {"NEW", "PARTIALLY_FILLED"} for r in second.orders.values())
        or any(r["algoStatus"] == "NEW" for r in second.algos.values())
    ):
        raise HistoryBlocked("CURRENT_EXPOSURE")
    if second.wallet_balance is None:
        raise HistoryBlocked("WALLET_MISSING")
    boundary_ms = int(boundary.timestamp() * 1000)
    net = sum((Decimal(r["income"]) for r in incomes.values()), Decimal(0))
    net_since_anchor = sum(
        (Decimal(r["income"]) for r in incomes.values() if int(r["time"]) >= anchor_ms), Decimal(0)
    )
    delta = second.wallet_balance - ledger.value.equity
    verified_exits: list[int] = []
    verified_exit_order_ids: set[str] = set()
    for order in ledger.orders.values():
        remote = second.orders.get(order.exit_id or "")
        if (
            not remote
            or remote["status"] != "FILLED"
            or remote["reduceOnly"] != "true"
            or remote["clientOrderId"] != order.exit_id
            or remote["symbol"] != order.signal.symbol
            or remote["positionSide"] != "BOTH"
            or remote["side"] != ("SELL" if order.signal.direction == "LONG" else "BUY")
            or order.filled <= 0
        ):
            continue
        matched = [r for r in trades.values() if r["order_id"] == remote["orderId"]]
        if (
            matched
            and sum((Decimal(r["quantity"]) for r in matched), Decimal(0)) == order.filled
            and signed_number(remote["executedQty"]) == order.filled
            and all(
                r["side"] == ("SELL" if order.signal.direction == "LONG" else "BUY")
                for r in matched
            )
        ):
            stamp = max(int(r["time"]) for r in matched)
            if stamp <= anchor_ms:
                verified_exits.append(stamp)
                verified_exit_order_ids.add(remote["orderId"])
    exit_ms = max(verified_exits) if verified_exits else None
    digest = hashlib.sha256(
        json.dumps(
            {"trades": trades, "incomes": incomes}, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return {
        "review": "FLAT_HISTORY_EVIDENCE_ONLY",
        "local_flat_anchor_utc": anchor.isoformat(),
        "query_start_utc": window_start.isoformat(),
        "last_known_full_exit_fill_found": exit_ms is not None,
        "last_known_full_exit_fill_utc": (
            datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=exit_ms)
        ).isoformat()
        if exit_ms is not None
        else None,
        "btc_trades_after_known_exit_before_boundary": sum(
            exit_ms < int(r["time"]) < boundary_ms for r in trades.values()
        )
        if exit_ms is not None
        else None,
        "other_trades_at_exit_timestamp": sum(
            int(r["time"]) == exit_ms and r["order_id"] not in verified_exit_order_ids
            for r in trades.values()
        )
        if exit_ms is not None
        else None,
        "boundary_kst": boundary.isoformat(),
        "cutoff_utc": (
            datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=end_ms)
        ).isoformat(),
        "trade_scope": "BTCUSDT_ONLY",
        "btc_trades_before_boundary": sum(int(r["time"]) < boundary_ms for r in trades.values()),
        "btc_trades_at_or_after_boundary": sum(
            int(r["time"]) >= boundary_ms for r in trades.values()
        ),
        "account_income_counts": dict(Counter(r["type"] for r in incomes.values())),
        "account_income_before_boundary_counts": dict(
            Counter(r["type"] for r in incomes.values() if int(r["time"]) < boundary_ms)
        ),
        "account_income_at_or_after_boundary_counts": dict(
            Counter(r["type"] for r in incomes.values() if int(r["time"]) >= boundary_ms)
        ),
        "account_income_net_usdt": str(net),
        "wallet_change_since_local_anchor_usdt": str(delta),
        "account_income_since_local_anchor_net_usdt": str(net_since_anchor),
        "wallet_change_matches_income": delta == net_since_anchor,
        "evidence_hash": digest,
        "account_wide_position_history_verified": False,
        "recovery_eligible": False,
        "reason": "BTC_HISTORY_IS_NOT_ACCOUNT_WIDE_MIDNIGHT_EQUITY_PROOF",
        "database_changed": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }
