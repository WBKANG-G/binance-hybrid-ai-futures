"""Bounded signed GET review of existing owned TP history; never executes a trade."""

import argparse
import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, NoReturn

import yaml
from integrations.binance_usdm.demo import REST_HOST, DemoClient, DemoNotFound, DemoSettings
from integrations.binance_usdm.demo_snapshot import signed_number
from integrations.binance_usdm.recovery_transport import (
    recovery_connection,
    safe_transport_diagnostics,
)
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import accounts
from scripts.demo_key_session import credentials
from services.execution.demo_models import DemoLedger
from services.execution.demo_recovery_budget import RecoveryReadClient
from services.execution.models import PaperOrder
from sqlalchemy import Engine, create_engine, select

ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"
MAX_ORDERS = 12
MAX_SIGNED = 36
STATES = {"NEW", "CANCELED", "EXPIRED", "FINISHED", "REJECTED", "TRIGGERED", "TRIGGERING"}


def require(value: Any) -> None:
    if not value:
        raise ValueError("HISTORY_SCOPE_OR_EVIDENCE_CHANGED")


class ScopedReader(RecoveryReadClient):
    def __init__(self, source: DemoClient, algos: set[str]):
        super().__init__(source)
        self.algos = algos
        self.executions: set[str] = set()

    def listen_key(self, method: Literal["POST", "PUT", "DELETE"]) -> NoReturn:
        raise ValueError("PRIVATE_SESSION_FORBIDDEN")

    def request(self, method: str, path: str, params: Mapping[str, str] | None = None) -> Any:
        values = dict(params or {})
        allowed = (
            path == "/fapi/v1/algoOrder"
            and set(values) == {"clientAlgoId"}
            and values["clientAlgoId"] in self.algos
        ) or (
            path == "/fapi/v1/order"
            and set(values) == {"symbol", "orderId"}
            and values["symbol"] == "BTCUSDT"
            and values["orderId"] in self.executions
        )
        require(method == "GET" and allowed and self.budget.signed_queries < MAX_SIGNED)
        return super().request(method, path, params)


def read_state(engine: Engine, account: str) -> tuple[object, int, object, DemoLedger]:
    with engine.connect() as conn:
        conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout='3s'")
        row = (
            conn.execute(select(accounts).where(accounts.c.account_id == account)).mappings().one()
        )
        return (
            row["owner"],
            int(row["fence"]),
            row["lease_until"],
            DemoLedger.model_validate(row["state"]),
        )


def operation_ref(order: PaperOrder) -> str:
    return hashlib.sha256(str(order.signal.signal_id).encode()).hexdigest()[:16]


def selected(
    ledger: DemoLedger,
    *,
    after_ref: str | None = None,
    expected_total: int | None = None,
    expected_skipped: int | None = None,
) -> tuple[list[PaperOrder], int]:
    eligible = [
        order
        for key, order in ledger.orders.items()
        if key == str(order.signal.signal_id)
        and order.signal.environment == "DEMO"
        and order.signal.symbol == "BTCUSDT"
        and order.signal.direction == "LONG"
        and order.signal.purpose == "DEMO_VALIDATION"
        and order.filled > 0
        and order.position_state.value == "CLOSED"
        and not order.remaining
        and not order.open_quantity
        and order.algo_id
        and order.tp_algo_id
    ]
    eligible.sort(
        key=lambda order: (order.signal.signal_closed_candle_at, str(order.signal.signal_id)),
        reverse=True,
    )
    skipped = 0
    if after_ref is not None:
        require(re.fullmatch(r"[0-9a-f]{16}", after_ref) is not None)
        require(expected_total is not None and len(eligible) == expected_total)
        matches = [
            index for index, order in enumerate(eligible) if operation_ref(order) == after_ref
        ]
        require(len(matches) == 1)
        skipped = matches[0] + 1
        require(expected_skipped is not None and skipped == expected_skipped)
    else:
        require(expected_total is None and expected_skipped is None)
    return eligible[skipped : skipped + MAX_ORDERS], len(eligible)


def algo(reader: ScopedReader, order: PaperOrder, *, target: bool) -> dict[str, Any] | None:
    identifier = order.tp_algo_id if target else order.algo_id
    try:
        raw = reader.request("GET", "/fapi/v1/algoOrder", {"clientAlgoId": identifier or ""})
    except DemoNotFound:
        return None  # Absence is unresolved; never proof of no fill or no submission.
    require(isinstance(raw, dict))
    if not isinstance(raw, dict):
        raise TypeError("ALGO_OBJECT_REQUIRED")
    common = {
        "clientAlgoId": identifier,
        "symbol": "BTCUSDT",
        "side": "SELL",
        "positionSide": "BOTH",
        "algoType": "CONDITIONAL",
        "orderType": "TAKE_PROFIT_MARKET" if target else "STOP_MARKET",
        "workingType": "MARK_PRICE",
    }
    require(all(raw.get(key) == value for key, value in common.items()))
    require(str(raw.get("closePosition")).lower() == "true")
    require(str(raw.get("priceProtect")).lower() == "false")
    require(raw.get("algoStatus") in STATES)
    require(
        signed_number(raw.get("triggerPrice"))
        == (order.signal.target_price if target else order.signal.initial_stop_price)
    )
    return raw


def execution(reader: ScopedReader, order: PaperOrder, tp: dict[str, Any] | None) -> str:
    if tp is None or tp["algoStatus"] != "FINISHED":
        return "NOT_OBSERVED"
    actual = str(tp.get("actualOrderId", ""))
    if not actual.isdecimal() or int(actual) <= 0:
        return "EXECUTION_ID_UNAVAILABLE"
    reader.executions.add(actual)
    try:
        raw = reader.request("GET", "/fapi/v1/order", {"symbol": "BTCUSDT", "orderId": actual})
    except DemoNotFound:
        return "EXECUTION_NOT_FOUND_UNRESOLVED"
    if not isinstance(raw, dict):
        return "EXECUTION_MISMATCH"
    verified = (
        str(raw.get("orderId")) == actual
        and raw.get("symbol") == "BTCUSDT"
        and raw.get("side") == "SELL"
        and raw.get("positionSide") == "BOTH"
        and raw.get("type") == "MARKET"
        and raw.get("status") == "FILLED"
        and str(raw.get("reduceOnly")).lower() == "true"
        and signed_number(raw.get("executedQty")) == order.filled
        and signed_number(raw.get("avgPrice")) > 0
    )
    return "SIGNED_EXECUTION_VERIFIED" if verified else "EXECUTION_MISMATCH"


def review(
    engine: Engine,
    source: DemoClient,
    account: str = ACCOUNT,
    *,
    after_ref: str | None = None,
    expected_total: int | None = None,
    expected_skipped: int | None = None,
) -> dict[str, Any]:
    before = read_state(engine, account)
    ledger = before[3]
    require(before[0] is None and ledger.paused and ledger.risk_state.value == "NORMAL")
    require(not any(ledger.positions.values()))
    require(
        all(not order.remaining and not order.open_quantity for order in ledger.orders.values())
    )
    orders, total = selected(
        ledger,
        after_ref=after_ref,
        expected_total=expected_total,
        expected_skipped=expected_skipped,
    )
    skipped = expected_skipped or 0
    identifiers = [
        identifier
        for order in orders
        for identifier in (order.algo_id, order.tp_algo_id)
        if identifier is not None
    ]
    require(len(identifiers) == len(set(identifiers)))
    rows = []
    with recovery_connection(source) as pooled:
        reader = ScopedReader(source, set(identifiers))
        reader.sync_clock()
        for order in orders:
            tp = algo(reader, order, target=True)
            sl = algo(reader, order, target=False)
            proof = execution(reader, order, tp)
            rows.append(
                {
                    "operation_ref": operation_ref(order),
                    "signal_closed_candle_at": order.signal.signal_closed_candle_at.isoformat(),
                    "filled_quantity": str(order.filled),
                    "target_status": tp["algoStatus"] if tp else "NOT_FOUND_UNRESOLVED",
                    "original_stop_status": sl["algoStatus"] if sl else "NOT_FOUND_UNRESOLVED",
                    "tp_execution_evidence": proof,
                    "local_operator_exit_recorded": bool(order.exit_id),
                    "t06_ordering_verified": False,
                }
            )
        diagnostics = safe_transport_diagnostics(
            pooled.diagnostics() if pooled else {"audit_transport_mode": "UNPOOLED_TRANSPORT"}
        )
    require(read_state(engine, account) == before)
    count = sum(row["tp_execution_evidence"] == "SIGNED_EXECUTION_VERIFIED" for row in rows)
    return {
        "tp_history_review": "READ_ONLY_SELECTED_COHORT_REVIEW_FINISHED",
        "eligible_owned_orders": total,
        "selected_orders": len(orders),
        "skipped_orders": skipped,
        "after_operation_ref": after_ref,
        "remaining_after_page": max(0, total - skipped - len(orders)),
        "covers_all_eligible_in_this_run": skipped == 0 and len(orders) == total,
        "coverage_truncated": total > skipped + len(orders),
        "selection": "CURRENT_PAGE_MAX12_CLOSED_FILLED_OWN_DEMO_LONG_BTC_ORDERS_AFTER_BOUND_CURSOR",
        "signed_historical_tp_execution_count": count,
        "rows": rows,
        "t06_ordering_verified": False,
        "interpretation": "SIGNED_HISTORICAL_TP_EXECUTION_OBSERVED_ORDERING_NOT_PROVEN"
        if count
        else "NO_SIGNED_TP_EXECUTION_PROVEN_IN_SELECTED_COHORT",
        "signed_queries": reader.budget.signed_queries,
        "clock_probes": reader.budget.clock_probes,
        "read_diagnostic": diagnostics,
        "current_exchange_empty_checked": False,
        "database_changed": False,
        "lease_acquired": False,
        "order_writes": 0,
        "signal_generated": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
        "next_action": "REVIEW_ONLY_NO_ORDER_OR_RECOVERY_FROM_THIS_REPORT",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-only", action="store_true", required=True)
    parser.add_argument("--after-operation-ref")
    parser.add_argument("--expected-total", type=int)
    parser.add_argument("--expected-skipped", type=int)
    args = parser.parse_args()
    if any(
        value is not None
        for value in (args.after_operation_ref, args.expected_total, args.expected_skipped)
    ):
        if (
            args.after_operation_ref is None
            or args.expected_total is None
            or args.expected_skipped is None
            or re.fullmatch(r"[0-9a-f]{16}", args.after_operation_ref) is None
            or not 0 < args.expected_skipped <= args.expected_total
        ):
            parser.error("Pagination requires a valid prior operation ref, total and skipped count")
    engine = None
    try:
        require(REST_HOST == "https://demo-fapi.binance.com")
        require(os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false")
        require(os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE")
        cfg = DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
            )
        ).model_copy(update={"demo_trading_enabled": False})
        source = DemoClient(cfg, credentials(prompt=False))
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        report = review(
            engine,
            source,
            after_ref=args.after_operation_ref,
            expected_total=args.expected_total,
            expected_skipped=args.expected_skipped,
        )
        code = 0
    except Exception:  # noqa: BLE001 -- no private URLs, credentials, ledger or raw errors in output
        report = {
            "tp_history_review": "BLOCKED_KEEP_PAUSED_NO_AUTOMATIC_RETRY",
            "details_redacted": True,
            "database_changed": False,
            "lease_acquired": False,
            "order_writes": 0,
            "orders_enabled": False,
            "live_trading_enabled": False,
        }
        code = 2
    finally:
        if engine is not None:
            engine.dispose()
    print(json.dumps(report), flush=True)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
