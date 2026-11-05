"""Read-only scoped evidence for the completed opposite-quote DEMO safety exit."""

import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import yaml
from sqlalchemy import Engine, select

from integrations.binance_usdm.demo import DemoClient, DemoSettings
from integrations.binance_usdm.demo_snapshot import signed_number
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import accounts, events
from scripts.demo_key_session import credentials
from services.execution.demo_models import DemoLedger

ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"
OPERATION = UUID("243598bb-900a-45d5-8af3-a776d543a504")
TERMINAL_ALGO = {"CANCELED", "EXPIRED", "FINISHED", "REJECTED"}
KINDS = (
    "DEMO_OPERATION_INTENT",
    "DEMO_REDUCE_ONLY_INTENT",
    "DEMO_REDUCE_ONLY_EFFECT",
    "DEMO_FLAT_CONFIRMATION_CHECK",
    "DEMO_TP_TRIGGER_RESULT",
)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def account_row(conn: Any, account: str) -> dict[str, Any]:
    return dict(
        conn.execute(select(accounts).where(accounts.c.account_id == account)).mappings().one()
    )


def empty_remote(client: DemoClient) -> bool:
    positions = client.request("GET", "/fapi/v3/positionRisk")
    regular = client.request("GET", "/fapi/v1/openOrders")
    algo = client.request("GET", "/fapi/v1/openAlgoOrders")
    return bool(
        isinstance(positions, list)
        and all(
            isinstance(p, dict)
            and isinstance(p.get("symbol"), str)
            and signed_number(p.get("positionAmt")) == 0
            for p in positions
        )
        and isinstance(regular, list)
        and not regular
        and isinstance(algo, list)
        and not algo
    )


def verify_order(raw: Any, local: Any, *, exit_order: bool) -> bool:
    identifier = local.exit_id if exit_order else local.client_id
    return bool(
        isinstance(raw, dict)
        and raw.get("clientOrderId") == identifier
        and raw.get("symbol") == "BTCUSDT"
        and raw.get("positionSide") == "BOTH"
        and raw.get("status") == "FILLED"
        and raw.get("side") == ("SELL" if exit_order else "BUY")
        and raw.get("type") == ("MARKET" if exit_order else "LIMIT")
        and str(raw.get("reduceOnly")).lower() == ("true" if exit_order else "false")
        and signed_number(raw.get("origQty")) == local.filled
        and signed_number(raw.get("executedQty")) == local.filled
        and signed_number(raw.get("avgPrice")) > 0
        and (exit_order or str(raw.get("orderId")) == local.server_order_id)
    )


def terminal_algo(raw: Any, local: Any, *, target: bool) -> str:
    identifier = local.tp_algo_id if target else local.algo_id
    if not (
        isinstance(raw, dict)
        and raw.get("clientAlgoId") == identifier
        and raw.get("symbol") == "BTCUSDT"
        and raw.get("side") == "SELL"
        and raw.get("positionSide") == "BOTH"
        and raw.get("algoType") == "CONDITIONAL"
        and raw.get("workingType") == "MARK_PRICE"
        and str(raw.get("closePosition")).lower() == "true"
        and str(raw.get("priceProtect")).lower() == "false"
        and raw.get("orderType") == ("TAKE_PROFIT_MARKET" if target else "STOP_MARKET")
        and signed_number(raw.get("triggerPrice"))
        == (local.signal.target_price if target else local.signal.initial_stop_price)
        and raw.get("algoStatus") in TERMINAL_ALGO
    ):
        raise ValueError("TERMINAL_PROTECTION_NOT_VERIFIED")
    return str(raw["algoStatus"])


def inspect(
    engine: Engine, account: str, operation: UUID, client: DemoClient | None
) -> dict[str, Any]:
    with engine.connect() as conn:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout='2s'")
        before = account_row(conn, account)
        ledger = DemoLedger.model_validate(before["state"])
        order = ledger.orders[str(operation)]
        if not (
            account.startswith("DEMO-")
            and ledger.risk_state.value == "EMERGENCY"
            and ledger.paused
            and order.signal.purpose == "DEMO_VALIDATION"
            and order.signal.direction == "LONG"
            and order.signal.symbol == "BTCUSDT"
            and order.signal.signal_id == operation
            and 0 < order.quantity <= signed_number("0.0007")
            and order.quantity * order.signal.suggested_limit_price <= 100
            and order.state.value == "FILLED"
            and order.position_state.value == "CLOSED"
            and order.filled == order.quantity
            and not order.remaining
            and not order.open_quantity
        ):
            raise ValueError("SCOPED_CLOSED_EMERGENCY_REQUIRED")
        rows = (
            conn.execute(
                select(events.c.as_of, events.c.kind, events.c.payload)
                .where(
                    events.c.account_id == account,
                    events.c.kind.in_(KINDS),
                    events.c.as_of >= order.signal.signal_closed_candle_at - timedelta(minutes=1),
                )
                .order_by(events.c.as_of, events.c.event_id)
                .limit(201)
            )
            .mappings()
            .all()
        )
        if len(rows) > 200:
            raise ValueError("AUDIT_BOUND_EXCEEDED")
    finish = str(uuid5(operation, "finish"))
    explicit_finish = ledger.operations.get(finish) == "EMERGENCY_FLATTEN"
    timeline = []
    for row in rows:
        state = json.loads(row["payload"])
        own = state.get("orders", {}).get(str(operation))
        if own and own.get("client_id") == order.client_id:
            timeline.append(
                {
                    "kind": row["kind"],
                    "audit_utc": row["as_of"].isoformat(),
                    "risk_state": state.get("risk_state"),
                    "finish_emergency_marker_present": state.get("operations", {}).get(finish)
                    == "EMERGENCY_FLATTEN",
                    "exit_state": own.get("exit_state"),
                    "position_state": own.get("position_state"),
                }
            )
    report: dict[str, Any] = {
        "exit_review": "READ_ONLY_SCOPED_EXIT_REVIEW_KEEP_LOCK",
        "operation_id": str(operation),
        "risk_state": ledger.risk_state.value,
        "paused": ledger.paused,
        "finish_emergency_command_recorded": explicit_finish,
        "local_entry_state": order.state.value,
        "local_exit_state": order.exit_state,
        "local_exit_id_present": bool(order.exit_id),
        "local_position_state": order.position_state.value,
        "filled_quantity": str(order.filled),
        "audit_timeline": timeline,
        "audit_time_meaning": "AFTER_ROW_LOCK_BEFORE_APPLY_NOT_EXCHANGE_OR_COMMIT_TIME",
        "remote_evidence": "NOT_CHECKED_CACHED_KEY_OR_CLOCK_UNAVAILABLE",
        "full_recovery_eligibility_reviewed": False,
        "unresolved_history_acknowledged": False,
        "database_changed": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }
    if client is not None:
        try:
            if (
                client.settings.demo_trading_enabled
                or not order.exit_id
                or not order.algo_id
                or not order.tp_algo_id
            ):
                raise ValueError("READ_ONLY_CLIENT_AND_EXISTING_IDENTIFIERS_REQUIRED")
            if not empty_remote(client):
                raise ValueError("REMOTE_NOT_EMPTY")
            entry = client.request(
                "GET", "/fapi/v1/order", {"symbol": "BTCUSDT", "origClientOrderId": order.client_id}
            )
            exit_row = client.request(
                "GET", "/fapi/v1/order", {"symbol": "BTCUSDT", "origClientOrderId": order.exit_id}
            )
            if not verify_order(entry, order, exit_order=False) or not verify_order(
                exit_row, order, exit_order=True
            ):
                raise ValueError("ENTRY_OR_EXIT_EVIDENCE_NOT_VERIFIED")
            stop = terminal_algo(
                client.request("GET", "/fapi/v1/algoOrder", {"clientAlgoId": order.algo_id}),
                order,
                target=False,
            )
            target = terminal_algo(
                client.request("GET", "/fapi/v1/algoOrder", {"clientAlgoId": order.tp_algo_id}),
                order,
                target=True,
            )
            if not empty_remote(client):
                raise ValueError("REMOTE_CHANGED_OR_NOT_EMPTY")
            report.update(
                remote_evidence="SIGNED_ENTRY_EXIT_AND_CURRENT_EMPTY_ACCOUNT_VERIFIED",
                remote_entry_status="FILLED",
                remote_exit_status="FILLED",
                remote_exit_reduce_only_verified=True,
                remote_stop_status=stop,
                remote_target_status=target,
                current_remote_empty_verified=True,
            )
        except Exception:  # noqa: BLE001 -- never raw private response/URL/exception
            report["remote_evidence"] = "NOT_VERIFIED_KEEP_LOCK_DO_NOT_RETRY"
    with engine.connect() as conn:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout='2s'")
        if digest(account_row(conn, account)) != digest(before):
            raise ValueError("ACCOUNT_CHANGED_DURING_REVIEW")
    report["account_owner_fence_lease_state_unchanged"] = True
    return report


def main() -> None:
    from sqlalchemy import create_engine

    engine = None
    try:
        if (
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
            or os.getenv("TRADING_MODE", "PAPER").upper() == "LIVE"
        ):
            raise ValueError("DEMO_CONTEXT_REQUIRED")
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        client = None
        try:
            key = credentials(prompt=False)
            cfg = DemoSettings.model_validate(
                yaml.load(
                    Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
                )
            ).model_copy(update={"demo_trading_enabled": False})
            if cfg.margin_mode != "ISOLATED" or cfg.symbols != ("BTCUSDT",):
                raise ValueError("SCOPED_CONTEXT_REQUIRED")
            client = DemoClient(cfg, key)
            client.sync_clock()
        except Exception:  # noqa: BLE001 -- local-only evidence still useful, no secret prompts
            client = None
        report = inspect(engine, ACCOUNT, OPERATION, client)
    except Exception:  # noqa: BLE001 -- no raw account/credential/database details
        report = {
            "exit_review": "BLOCKED_KEEP_LOCK",
            "details_logged": False,
            "orders_enabled": False,
            "live_trading_enabled": False,
        }
    finally:
        if engine is not None:
            engine.dispose()
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
