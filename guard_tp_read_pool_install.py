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
OPERATION = UUID("31c953e8-72dc-422d-89f3-8bbe7cdb5c0c")
OLD_SHA = "40522b50012be16fb6a888d67645bc72572d7da7c24b58f4b2b87f8edf9c7018"
NEW_SHA = "0bef5c186d6e4a5c226f66736085585cf1d209d13ef1bd8622666d44d52966f9"
SOURCE = Path("scripts/check_demo_tp_trigger.py")
POOL_SHA = "a6e96b21135882a272cb5c97f86ddc14c3a5f4fa79dd115e40397252c679a66d"
UNCHANGED = {
    "services/execution/demo_protected_worker_check.py": "70d9f0a6ec74223e8a7a82ec55c976288c10fb13c01c9b478f49f1a0fd408709",
    "services/execution/demo_tp_trigger_check.py": "6b82ce5947480f87a58c194628853322496c90545a47713f4e07dd1fc0414dc8",
    "services/execution/demo_validation.py": "d703860caf01135b3f5e82a444adc5afde1795e1f4d48bbd515cc4b9de5666a0",
    "services/execution/demo_stream.py": "409098cb05abbfb97eb9b9f1db2070ca6bb5782bc6dd7542311e4eb703b6f456",
    "packages/persistence/demo.py": "40f9a33b8eeda28c96f36de35656e4667a2d2461ce9de79dc4b19a687cd5cd5a",
    "integrations/binance_usdm/demo.py": "c5ec1f89d645dcd124a71cdf9cb39884cc9cfa5a2493636baf1c08db2c581a81",
    "integrations/binance_usdm/recovery_transport.py": "11d66dd5e05b4707b5794707df0fa889be4cbb1204fcd7a0ec23d9c6008ea791",
    "configs/demo-isolated.example.yaml": "d93e85c2e056ffbbc3dac5fab9f0e5bf28032fd16f41bbff1f3ae81fb00882ca",
}


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
    require(row["owner"] is None and state.paused and state.risk_state.value == "NORMAL")
    require(order is not None)
    require(
        order.signal.symbol == "BTCUSDT"
        and order.signal.direction == "LONG"
        and order.signal.purpose == "DEMO_VALIDATION"
        and order.quantity == Decimal("0.0007")
        and order.state.value == "CANCELED"
        and order.filled == 0
        and not order.remaining
        and not order.open_quantity
        and order.position_state.value == "CLOSED"
        and order.exit_id is None
    )
    require(
        not any(state.positions.values())
        and all(not o.remaining and not o.open_quantity for o in state.orders.values())
    )
    require(state.validation_entry_styles.get(str(OPERATION)) == "OPPOSITE_QUOTE")
    require(
        state.operations.get(str(uuid5(OPERATION, "tp-trigger-observation")))
        == "TP_TRIGGER:NOT_ACCEPTED"
    )
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
        and value.get("risk_state") == "NORMAL"
        and value.get("orders_enabled") is False
        and value.get("live_trading_enabled") is False
    )
    count = value.get("unresolved_count")
    require(type(count) is int and count == 4)
    return count


def run(engine, phase, *, read_local=local, read_review=review, digest=None):
    digest = digest or hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    require(digest == NEW_SHA if phase == "post" else digest in {OLD_SHA, NEW_SHA})
    require(
        all(hashlib.sha256(Path(n).read_bytes()).hexdigest() == h for n, h in UNCHANGED.items()),
        "SOURCE_HASH_MISMATCH",
    )
    if digest == NEW_SHA:
        require(
            hashlib.sha256(
                Path("integrations/binance_usdm/demo_read_routing.py").read_bytes()
            ).hexdigest()
            == POOL_SHA,
            "SOURCE_HASH_MISMATCH",
        )
    before = read_local(engine)
    result = read_review()
    count = validate_review(result)
    require(read_local(engine) == before, "ACCOUNT_CHANGED_DURING_REVIEW")
    return {
        "tp_read_pool_install": "POSTCHECKED_NORMAL_KEEP_PAUSED"
        if phase == "post"
        else "PRECHECKED_NORMAL_KEEP_PAUSED",
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
            "tp_read_pool_install": "BLOCKED_KEEP_PAUSED_NO_AUTOMATIC_RETRY",
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
