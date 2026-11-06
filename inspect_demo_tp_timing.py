"""Read-only audit and signed timing of one already-canceled, unfilled DEMO order."""

import argparse
import hashlib
import json
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import yaml
from integrations.binance_usdm.demo import DemoClient, DemoSettings, transport
from integrations.binance_usdm.demo_snapshot import signed_number
from integrations.binance_usdm.recovery_transport import (
    recovery_connection,
    safe_transport_diagnostics,
)
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import accounts, events
from scripts.demo_key_session import credentials
from services.execution.demo_models import DemoLedger
from services.execution.demo_tp_trigger_check import OBSERVATION_SECONDS
from sqlalchemy import Engine, select

ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"
OPERATION = UUID("31c953e8-72dc-422d-89f3-8bbe7cdb5c0c")
KINDS = (
    "DEMO_TP_TRIGGER_START",
    "DEMO_ENTRY_RESERVED",
    "DEMO_ENTRY_INTENT",
    "DEMO_ENTRY_EFFECT",
    "DEMO_CANCEL_INTENT",
    "DEMO_CANCEL_EFFECT",
    "DEMO_OPERATION_INTENT",
    "DEMO_TP_TRIGGER_RESULT",
)


def hash_value(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def millis(value: datetime) -> int:
    delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000


def remote_millis(value: object) -> int:
    if type(value) is not int or not 0 < value < 253402300800000:
        raise ValueError("REMOTE_TIME_REQUIRED")
    return value


def iso_ms(value: int) -> str:
    seconds, ms = divmod(value, 1000)
    return (datetime.fromtimestamp(seconds, UTC) + timedelta(milliseconds=ms)).isoformat()


def read_account(conn: Any, account: str) -> dict[str, Any]:
    return dict(
        conn.execute(select(accounts).where(accounts.c.account_id == account)).mappings().one()
    )


def inspect_order(
    engine: Engine, account: str, operation: UUID, client: DemoClient | None
) -> dict[str, object]:
    with engine.connect() as conn:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout='2s'")
        raw = read_account(conn, account)
        ledger = DemoLedger.model_validate(raw["state"])
        order = ledger.orders[str(operation)]
        if (
            not account.startswith("DEMO-")
            or order.signal.purpose != "DEMO_VALIDATION"
            or order.signal.symbol != "BTCUSDT"
            or order.signal.direction != "LONG"
            or str(order.signal.signal_id) != str(operation)
            or order.state.value != "CANCELED"
            or order.position_state.value != "CLOSED"
            or order.filled
            or order.remaining
            or order.open_quantity
        ):
            raise ValueError("CLOSED_UNFILLED_DEMO_SCOPE_REQUIRED")
        rows = (
            conn.execute(
                select(events.c.as_of, events.c.kind, events.c.payload)
                .where(
                    events.c.account_id == account,
                    events.c.as_of >= order.signal.signal_closed_candle_at - timedelta(minutes=1),
                    events.c.kind.in_(KINDS),
                )
                .order_by(events.c.as_of, events.c.event_id)
                .limit(501)
            )
            .mappings()
            .all()
        )
        if len(rows) > 500:
            raise ValueError("AUDIT_WINDOW_TOO_LARGE")
    marker = str(uuid5(operation, "tp-trigger-observation"))
    pause_marker = str(uuid5(operation, "finish-pause"))
    timeline = []
    first_cancel = finish_pause = None
    for row in rows:
        payload = json.loads(row["payload"])
        own = payload.get("orders", {}).get(str(operation))
        operations = payload.get("operations", {})
        relevant = bool(own and own.get("client_id") == order.client_id)
        if row["kind"] in {"DEMO_TP_TRIGGER_START", "DEMO_TP_TRIGGER_RESULT"}:
            relevant = marker in operations
        if not relevant:
            continue
        paused_finish = operations.get(pause_marker) == "PAUSE_ENTRIES"
        if paused_finish and finish_pause is None:
            finish_pause = row["as_of"]
        if (
            row["kind"] == "DEMO_CANCEL_INTENT"
            and own
            and own.get("state") == "CANCEL_REQUESTED"
            and first_cancel is None
        ):
            first_cancel = row["as_of"]
        timeline.append(
            {
                "audit_utc": row["as_of"].isoformat(),
                "kind": row["kind"],
                "target_state": own.get("state") if own else None,
                "finish_pause_marker_present": paused_finish,
            }
        )
    expiry = millis(order.signal.expires_at)
    report: dict[str, object] = {
        "diagnostic": "READ_ONLY_CLOSED_TP_ORDER_TIMING",
        "operation_id": str(operation),
        "configured_observation_seconds": OBSERVATION_SECONDS,
        "stored_signal_expires_at_utc": order.signal.expires_at.isoformat(),
        "local_order_state": order.state.value,
        "filled_quantity": str(order.filled),
        "first_local_cancel_intent_utc": first_cancel.isoformat() if first_cancel else None,
        "first_finish_pause_marker_utc": finish_pause.isoformat() if finish_pause else None,
        "cancel_before_finish_pause": first_cancel < finish_pause
        if first_cancel and finish_pause
        else None,
        "local_cancel_vs_signal_expiry_ms": millis(first_cancel) - expiry if first_cancel else None,
        "audit_timeline": timeline,
        "audit_timestamps_mean": "LOCAL_AFTER_ROW_LOCK_BEFORE_APPLY_NOT_EXCHANGE_OR_COMMIT_TIMES",
        "cross_clock_deltas_are_not_monotonic_measurements": True,
        "remote_timing_status": "NOT_CHECKED_CACHED_KEY_UNAVAILABLE",
        "orders_enabled": False,
        "live_trading_enabled": False,
        "new_order_attempted": False,
        "database_changed": False,
    }
    if client is not None:
        try:
            if client.settings.demo_trading_enabled:
                raise ValueError("READ_ONLY_CLIENT_REQUIRED")
            response = client.request(
                "GET",
                "/fapi/v1/order",
                {"symbol": "BTCUSDT", "origClientOrderId": order.client_id},
            )
            if (
                not isinstance(response, dict)
                or response.get("symbol") != "BTCUSDT"
                or response.get("clientOrderId") != order.client_id
                or (
                    order.server_order_id is not None
                    and str(response.get("orderId")) != order.server_order_id
                )
                or response.get("side") != "BUY"
                or response.get("type") != "LIMIT"
                or response.get("status") != "CANCELED"
                or signed_number(response.get("origQty")) != order.quantity
                or signed_number(response.get("executedQty")) != 0
            ):
                raise ValueError("REMOTE_ORDER_SCOPE_CHANGED")
            opened, updated = (
                remote_millis(response.get("time")),
                remote_millis(response.get("updateTime")),
            )
            if updated < opened:
                raise ValueError("REMOTE_TIME_ORDER_INVALID")
            report.update(
                {
                    "remote_timing_status": "SIGNED_CANCELED_ZERO_FILL_ORDER_VERIFIED",
                    "remote_order_created_utc": iso_ms(opened),
                    "remote_last_update_utc": iso_ms(updated),
                    "remote_created_to_last_update_ms": updated - opened,
                    "remote_creation_to_local_expiry_ms": expiry - opened,
                    "remote_last_update_vs_local_expiry_ms": updated - expiry,
                }
            )
        except Exception:  # noqa: BLE001 -- safe fixed classification, never raw exception/response
            report["remote_timing_status"] = "REMOTE_QUERY_OR_IDENTITY_OR_TIMESTAMPS_NOT_VERIFIED"
    with engine.connect() as conn:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout='2s'")
        if hash_value(read_account(conn, account)) != hash_value(raw):
            raise ValueError("ACCOUNT_CHANGED_DURING_DIAGNOSIS")
    report["account_owner_fence_lease_state_unchanged"] = True
    return report


def compare_reads(
    engine: Engine,
    account: str,
    operation: UUID,
    client: DemoClient,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    if client.settings.demo_trading_enabled:
        raise ValueError("READ_ONLY_CLIENT_REQUIRED")

    def account_hash() -> str:
        with engine.connect() as conn:
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            conn.exec_driver_sql("SET LOCAL statement_timeout='2s'")
            return hash_value(read_account(conn, account))

    initial_account = account_hash()
    original_send = client.send
    deadline = clock() + 30
    verified = []
    durations: list[list[int]] = []

    class Measured(DemoClient):
        def __init__(self) -> None:
            super().__init__(client.settings, client.credentials, clock_ms=client.clock_ms)
            self.durations: list[int] = []

        def request(self, method: str, path: str, params: Any = None) -> Any:
            if method != "GET" or path != "/fapi/v1/order":
                raise ValueError("SINGLE_CLOSED_ORDER_GET_REQUIRED")
            started = time.monotonic_ns()
            try:
                return client.request(method, path, params)
            finally:
                self.durations.append(max(0, (time.monotonic_ns() - started) // 1_000_000))

    def sample() -> list[int]:
        measured = Measured()
        for _ in range(2):
            if clock() >= deadline:
                raise ValueError("READ_PROBE_TIME_BUDGET")
            report = inspect_order(engine, account, operation, measured)
            if (
                report["remote_timing_status"] != "SIGNED_CANCELED_ZERO_FILL_ORDER_VERIFIED"
                or clock() >= deadline
            ):
                raise ValueError("SIGNED_ORDER_OR_TIME_NOT_VERIFIED")
            verified.append((report["remote_order_created_utc"], report["remote_last_update_utc"]))
        return measured.durations

    client.sync_clock()
    durations.append(sample())
    with recovery_connection(client) as pooled:
        if pooled is None:
            raise ValueError("STANDARD_TRANSPORT_REQUIRED_FOR_COMPARISON")
        client.sync_clock()  # Warm-up is explicit and excluded from request durations.
        durations.append(sample())
        diagnostics = safe_transport_diagnostics(pooled.diagnostics())
    if (
        client.send is not original_send
        or len(set(verified)) != 1
        or account_hash() != initial_account
    ):
        raise ValueError("SOURCE_OR_CLOSED_ORDER_CHANGED")
    return {
        "read_transport_probe": "FOUR_SIGNED_CLOSED_ORDER_READS_VERIFIED_NO_CHANGE",
        "operation_id": str(operation),
        "original_sdk_request_ms": durations[0],
        "pooled_get_request_ms": durations[1],
        "samples_per_mode": 2,
        "signed_order_queries": 4,
        "clock_sync_calls": 2,
        "time_budget_seconds": 30,
        "clock_warmup_excluded_from_request_timings": True,
        "fixed_sequence": "ORIGINAL_THEN_POOLED",
        "timing_clock": "MONOTONIC_NS_AROUND_SDK_REQUEST",
        "read_diagnostic": diagnostics,
        "same_closed_unfilled_order_verified": True,
        "database_changed": False,
        "account_owner_fence_lease_state_unchanged": True,
        "order_writes": 0,
        "signal_generated": False,
        "entry_expiry_changed": False,
        "execution_transport_changed": False,
        "fill_cause_proven": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
        "next_action": "MEASUREMENT_REVIEW_ONLY_NO_ORDER_OR_POLICY_CHANGE",
    }


def main() -> None:
    from sqlalchemy import create_engine

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare-read-transports", action="store_true", required=True)
    parser.parse_args()
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
        key = credentials(prompt=False)
        cfg = DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
            )
        ).model_copy(update={"demo_trading_enabled": False})
        client = DemoClient(cfg, key)
        if type(client) is not DemoClient or client.send is not transport:
            raise ValueError("STANDARD_TRANSPORT_REQUIRED")
        report = compare_reads(engine, ACCOUNT, OPERATION, client)
    except Exception:  # noqa: BLE001 -- no raw database/key/ledger exceptions in output
        report = {
            "read_transport_probe": "BLOCKED_NO_CHANGE_NO_AUTOMATIC_RETRY",
            "details_logged": False,
            "orders_enabled": False,
            "live_trading_enabled": False,
            "new_order_attempted": False,
        }
    finally:
        if engine is not None:
            engine.dispose()
    print(json.dumps(report), flush=True)
    if report.get("read_transport_probe") != "FOUR_SIGNED_CLOSED_ORDER_READS_VERIFIED_NO_CHANGE":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
