"""Credential-free, PostgreSQL READ ONLY handoff for the host's API restart command."""

import argparse
import json
import os
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal, DecimalException
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine

from packages.domain.config import database_url
from packages.persistence.demo import accounts, events

ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"
MAX_WAIT_SECONDS = 180
READY_MAX_AGE_SECONDS = 5


def utc_now() -> datetime:
    return datetime.now(UTC)


def inspect_ready(
    engine: Engine, operation: UUID, now: datetime | None = None
) -> tuple[str, dict[str, Any]]:
    op = str(operation)
    ready_key = str(uuid5(operation, "restart-ready"))
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(text("SET LOCAL statement_timeout = '2s'"))
        row = (
            connection.execute(
                select(
                    accounts.c.owner,
                    accounts.c.fence,
                    accounts.c.lease_until,
                    accounts.c.state["orders"][op].label("order"),
                    accounts.c.state["risk_state"].as_string().label("risk"),
                    accounts.c.state["paused"].as_boolean().label("paused"),
                    accounts.c.state["reconciled"].as_boolean().label("reconciled"),
                    accounts.c.state["stream_ready"].as_boolean().label("stream"),
                    accounts.c.state["entry_quantity_caps"][op].as_string().label("cap"),
                    accounts.c.state["operations"][ready_key].as_string().label("ready_tag"),
                ).where(accounts.c.account_id == ACCOUNT)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return "ACCOUNT_NOT_FOUND", {}
        order = row["order"]
        if order is None:
            return "WAITING_FOR_ORDER", {}
        if not isinstance(order, dict):
            return "INVALID_ORDER", {}
        if order.get("state") in {"CANCELED", "EXPIRED", "REJECTED", "FILLED"}:
            return "ENTRY_NOT_OPEN", {}
        observed_now = now or utc_now()
        if not row["owner"] or row["lease_until"] is None or row["lease_until"] <= observed_now:
            return "GUARD_NOT_ACTIVE", {}
        try:
            quantity = Decimal(str(order.get("quantity", "NaN")))
            filled = Decimal(str(order.get("filled", "NaN")))
            remaining = Decimal(str(order.get("remaining", "NaN")))
            open_quantity = Decimal(str(order.get("open_quantity", "NaN")))
            cap = Decimal(str(row["cap"]))
            if not all(v.is_finite() for v in (quantity, filled, remaining, open_quantity, cap)):
                return "INVALID_ORDER", {}
            if filled != 0 or open_quantity != 0:
                return "ENTRY_FILLED", {}
            if quantity <= 0 or remaining != quantity or cap != quantity:
                return "INVALID_ORDER", {}
        except (ValueError, TypeError, DecimalException):
            return "INVALID_ORDER", {}
        signal = order.get("signal") or {}
        if (
            signal.get("signal_id") != op
            or signal.get("environment") != "DEMO"
            or signal.get("purpose") != "DEMO_VALIDATION"
            or signal.get("symbol") != "BTCUSDT"
            or signal.get("direction") != "LONG"
        ):
            return "INVALID_ORDER", {}
        try:
            expiry = datetime.fromisoformat(str(signal.get("expires_at", "")))
            if expiry.tzinfo is None or expiry <= observed_now + timedelta(seconds=5):
                return "ENTRY_TOO_CLOSE_TO_EXPIRY", {}
        except ValueError:
            return "INVALID_ORDER", {}
        if order.get("state") != "ACKNOWLEDGED":
            return "WAITING_FOR_READY", {}
        if not str(order.get("server_order_id") or "").isdigit():
            return "INVALID_ORDER", {}
        if row["risk"] != "NORMAL" or row["paused"] or not row["reconciled"] or not row["stream"]:
            return "GUARD_NOT_READY", {}
        if row["ready_tag"] != "OPEN_API_RESTART_READY":
            return "WAITING_FOR_READY", {}
        marker = connection.execute(
            select(events.c.as_of)
            .where(
                events.c.account_id == ACCOUNT,
                events.c.fence == row["fence"],
                events.c.kind == "DEMO_OPEN_API_RESTART_READY",
            )
            .order_by(events.c.as_of.desc())
            .limit(1)
        ).scalar_one_or_none()
        if marker is None:
            return "WAITING_FOR_READY", {}
        # Default reference is sampled AFTER the marker read. A newly committed
        # READY must not look future merely because the query began earlier.
        reference_now = now or utc_now()
        if row["lease_until"] <= reference_now:
            return "GUARD_NOT_ACTIVE", {}
        if expiry <= reference_now + timedelta(seconds=5):
            return "ENTRY_TOO_CLOSE_TO_EXPIRY", {}
        if not 0 <= (reference_now - marker).total_seconds() <= READY_MAX_AGE_SECONDS:
            return "READY_EXPIRED_OR_FUTURE", {}
        return "READY", {
            "operation_id": op,
            "ready_at_utc": marker.isoformat(),
            "guard_fence": row["fence"],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation-id", type=UUID, required=True)
    args = parser.parse_args()
    engine = None
    code = "WAIT_FAILED"
    details: dict[str, Any] = {}
    try:
        if (
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
            or os.getenv("TRADING_MODE", "PAPER") == "LIVE"
        ):
            code = "LIVE_CONTEXT_FORBIDDEN"
        else:
            engine = create_engine(
                database_url().get_secret_value(), connect_args={"connect_timeout": 2}
            )
            deadline = time.monotonic() + MAX_WAIT_SECONDS
            while True:
                code, details = inspect_ready(engine, args.operation_id)
                if code not in {"WAITING_FOR_ORDER", "WAITING_FOR_READY"}:
                    break
                if time.monotonic() >= deadline:
                    code = "WAIT_TIMEOUT"
                    break
                time.sleep(0.5)
    except Exception:
        code = "WAIT_FAILED"
    finally:
        if engine is not None:
            engine.dispose()
    print(
        json.dumps(
            {
                "restart_handoff": code,
                **details,
                "database_changed": False,
                "orders_enabled": False,
                "live_trading_enabled": False,
            }
        ),
        flush=True,
    )
    if code != "READY":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
