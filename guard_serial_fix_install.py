"""Read-only pre/post guard for API code update; never applies recovery or trading."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid5

from sqlalchemy import create_engine, select

from packages.domain.config import database_url
from packages.persistence.demo import accounts
from services.execution.demo_models import DemoLedger

ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"
OPERATION = UUID("e85a00f2-6b8e-4b39-bac2-b958a001c8c0")
OLD_SHA = "9c264588fa7802efc0dcaa4f923153072fa85ba6fd888a23a2dda8ede4966238"
NEW_SHA = "70d9f0a6ec74223e8a7a82ec55c976288c10fb13c01c9b478f49f1a0fd408709"
SOURCE = Path("services/execution/demo_protected_worker_check.py")


CODES = {
    "INSTALL_GUARD_BLOCKED",
    "SOURCE_HASH_MISMATCH",
    "CANONICAL_REVIEW_BLOCKED",
    "CANONICAL_GATES_NOT_READY",
    "ACCOUNT_CHANGED_DURING_REVIEW",
}


class GuardBlocked(ValueError):
    pass


def require(value, code="INSTALL_GUARD_BLOCKED"):
    if not value:
        raise GuardBlocked(code)


def local(engine):
    with engine.connect() as conn:
        conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout='3s'")
        row = (
            conn.execute(select(accounts).where(accounts.c.account_id == ACCOUNT)).mappings().one()
        )
    state = DemoLedger.model_validate(row["state"])
    order = state.orders.get(str(OPERATION))
    require(
        row["owner"] is None and state.paused and state.risk_state.value in {"EMERGENCY", "NORMAL"}
    )
    require(order is not None)
    require(
        order.signal.symbol == "BTCUSDT"
        and order.signal.direction == "LONG"
        and order.signal.purpose == "DEMO_VALIDATION"
        and order.quantity == Decimal("0.0007")
        and order.state.value == "FILLED"
        and order.filled == order.quantity
        and not order.remaining
        and not order.open_quantity
        and order.position_state.value == "CLOSED"
        and order.exit_state == "FILLED"
        and bool(order.exit_id)
    )
    require(
        not any(state.positions.values())
        and all(not o.remaining and not o.open_quantity for o in state.orders.values())
    )
    require(
        state.validation_evidence.get(str(OPERATION), {}).get("stop_verified") is True
        and state.validation_evidence.get(str(OPERATION), {}).get("target_verified") is True
    )
    require(
        state.operations.get(str(uuid5(OPERATION, "protected-worker-ready")))
        == "FULL_PROTECTED_READY"
        and state.operations.get(str(uuid5(OPERATION, "protected-worker-finally"))) == "FINALLY_RAN"
    )
    failure = json.loads(
        state.operations.get(str(uuid5(OPERATION, "protected-worker-failure")), "{}")
    )
    require(failure == {"stage": "STREAM_OBSERVATION", "code": "NONE"})
    return (row["owner"], row["fence"], row["lease_until"], state)


def review():
    process = subprocess.run(
        [sys.executable, "-m", "scripts.review_demo_recovery"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=150,
        check=False,
    )
    require(process.returncode == 0, "CANONICAL_REVIEW_BLOCKED")
    value = json.loads(process.stdout)
    require(isinstance(value, dict))
    return value


def validate_review(value):
    require(
        value.get("recovery") == "REVIEW_ONLY_NO_DATABASE_CHANGE"
        and value.get("risk_gates") == "PASS"
        and value.get("position_nonzero") is False
        and type(value.get("active_orders")) is int
        and value["active_orders"] == 0
        and value.get("paused") is True
        and value.get("risk_state") in {"EMERGENCY", "NORMAL"}
        and value.get("orders_enabled") is False
        and value.get("live_trading_enabled") is False
    )
    count = value.get("unresolved_count")
    require(type(count) is int and 0 <= count <= 128)
    return count


def run(engine, phase, *, read_local=local, read_review=review, digest=None):
    digest = digest or hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    require(digest == NEW_SHA if phase == "post" else digest in {OLD_SHA, NEW_SHA})
    before = read_local(engine)
    result = read_review()
    count = validate_review(result)
    require(read_local(engine) == before, "ACCOUNT_CHANGED_DURING_REVIEW")
    return {
        "probe_fix_guard": "POSTCHECKED_KEEP_PAUSED"
        if phase == "post"
        else "PRECHECKED_KEEP_PAUSED",
        "risk_state": result["risk_state"],
        "paused": True,
        "remote_empty_verified": True,
        "unresolved_count": count,
        "recovery_applied": False,
        "database_changed": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["pre", "post"], required=True)
    args = parser.parse_args()
    engine = None
    try:
        require(
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"
            and os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE"
        )
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        report = run(engine, args.phase)
        code = 0
    except Exception as error:  # noqa: BLE001 -- canonical token/private query output stays in RAM
        report = {
            "probe_fix_guard": "BLOCKED_KEEP_PAUSED_NO_AUTOMATIC_RETRY",
            "phase": args.phase,
            "details_redacted": True,
            "failure_code": str(error)
            if isinstance(error, GuardBlocked) and str(error) in CODES
            else "INSTALL_CHECK_UNAVAILABLE",
            "recovery_applied": False,
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
