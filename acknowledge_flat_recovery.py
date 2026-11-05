"""Explicitly acknowledged, fixed-history recovery; entries always remain paused."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4, uuid5

import yaml

from integrations.binance_usdm.demo import DemoClient, DemoSettings
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import DemoStore
from scripts.demo_key_session import credentials
from services.execution import demo_recovery as recovery

OPERATION = UUID("e0e27c9a-8616-48bb-aab7-5f061bff908b")
LATEST_OPERATION = UUID("243598bb-900a-45d5-8af3-a776d543a504")
DIRECTORY = Path("/tmp/flat-recovery-e0e27c9a861648bbaab75f061bff908b")
HELPER_SHA256 = "daea09c99b49d732cae087532afdfd34c378d15d18d368ed1d2c58fba04e179d"
# Verify the helper before importing any of its code in the actual stdin entrypoint.
if __name__ == "__main__":
    helper = DIRECTORY / "review_unresolved_details.py"
    try:
        if hashlib.sha256(helper.read_bytes()).hexdigest() != HELPER_SHA256:
            raise ValueError("HELPER_SOURCE_MISMATCH")
    except Exception:  # noqa: BLE001 -- no file content or private paths in output
        print(
            json.dumps(
                {
                    "recovery": "BLOCKED_BEFORE_APPLY",
                    "error_code": "HELPER_SOURCE_NOT_VERIFIED",
                    "orders_enabled": False,
                    "live_trading_enabled": False,
                }
            )
        )
        raise SystemExit(2) from None
# The read-only detail helper is uploaded to a private, operation-specific directory.
sys.path.insert(0, str(DIRECTORY))
import review_unresolved_details as details  # noqa: E402 -- verified helper is in the operation-private directory


def require(condition: Any) -> None:
    if not condition:
        raise ValueError("ACKNOWLEDGED_SCOPE_OR_STATE_CHANGED")


def validate_reference(reference: dict[str, Any]) -> None:
    require(reference.get("recovery") == "READ_ONLY_BOUND_UNRESOLVED_DETAILS_KEEP_LOCK")
    require(reference.get("audit_details_bound_to_canonical_review") is True)
    require(reference.get("unresolved_count") == 4)
    require(reference.get("risk_gates") == "PASS")
    require(reference.get("position_nonzero") is False and reference.get("active_orders") == 0)
    require(reference.get("risk_state") == "EMERGENCY" and reference.get("paused") is True)
    require(reference.get("unlock_applied") is False)
    require(reference.get("unresolved_history_acknowledged") is False)
    require(re.fullmatch(r"[0-9a-f]{64}", reference.get("unresolved_set_digest", "")) is not None)
    rows = reference.get("unresolved_requests", [])
    require(len(rows) == 4 and len({r["request_ref"] for r in rows}) == 4)
    require(Counter(r["request_kind"] for r in rows) == {"DS": 2, "DX": 1, "DT": 1})
    require(
        Counter(r["local_request_status"] for r in rows)
        == {"UNKNOWN": 1, "SUBMITTING": 1, "NOT_SENT": 2}
    )
    old_zero_operations = set()
    for row in rows:
        require(row["current_query_status"] == "NOT_FOUND_UNRESOLVED")
        require(row["unlinked_local_request"] is False and len(row["linked_orders"]) == 1)
        link = row["linked_orders"][0]
        require(link["purpose"] == "DEMO_VALIDATION" and link["is_latest_opposite_tp"] is False)
        require(link["operation_id"] != str(LATEST_OPERATION))
        require(link["position_state"] == "CLOSED")
        require(Decimal(link["open_quantity"]) == Decimal(link["remaining_quantity"]) == 0)
        require(link["role"] == {"DS": "STOP", "DX": "EXIT", "DT": "TARGET"}[row["request_kind"]])
        if row["local_request_status"] == "NOT_SENT":
            require(row["local_no_send_evidence"] == "LOCAL_VALIDATION_FAILED")
            require(link["signal_closed_candle_at"].startswith("2026-10-31T"))
            require(link["entry_state"] == link["exit_state"] == "FILLED")
            require(Decimal(link["filled_quantity"]) == Decimal("0.0007"))
        else:
            require(row["local_no_send_evidence"] is None)
            require(link["signal_closed_candle_at"].startswith("2026-10-20T"))
            require(link["entry_state"] == "CANCELED" and link["exit_state"] == "UNKNOWN")
            require(Decimal(link["filled_quantity"]) == 0)
            old_zero_operations.add(link["operation_id"])
    require(len(old_zero_operations) == 1)


def ready_check() -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "scripts.check_demo_session_ready"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    require(result.returncode == 0)
    report = json.loads(result.stdout)
    if not isinstance(report, dict):
        raise ValueError("READY_JSON_REQUIRED")
    require(report.get("session_ready") == "READ_ONLY_READY_ENTRIES_STILL_PAUSED")
    require(report.get("risk_state") == "NORMAL" and report.get("paused") is True)
    require(
        report.get("period_keys_match") is True and report.get("exchange_empty_verified") is True
    )
    require(report.get("database_changed") is False)
    require(report.get("orders_enabled") is False and report.get("live_trading_enabled") is False)
    return report


def execute(
    store: DemoStore,
    client: DemoClient,
    reference: dict[str, Any],
    *,
    acknowledged: bool,
    account: str = details.ACCOUNT,
) -> dict[str, Any]:
    stage, lease, apply_started, released = "OPERATOR_SCOPE", None, False, False
    try:
        require(acknowledged)
        validate_reference(reference)
        stage = "LOCAL_STATE"
        before = store.read(account)
        key = str(OPERATION)
        if before.risk_state.value == "NORMAL" and key in before.recovery_receipts:
            require(before.paused and before.operations.get(key) == "ACKNOWLEDGE_FLAT_EMERGENCY")
            ready = ready_check()
            return {
                "recovery": "ALREADY_ACKNOWLEDGED_READY_ENTRIES_STILL_PAUSED",
                "readiness": ready,
                "database_changed": False,
                "orders_enabled": False,
                "live_trading_enabled": False,
            }
        require(before.risk_state.value == "EMERGENCY" and before.paused)
        require(key not in before.recovery_receipts and key not in before.operations)
        require(
            before.operations.get(str(uuid5(LATEST_OPERATION, "finish"))) == "EMERGENCY_FLATTEN"
        )
        latest = before.orders[str(LATEST_OPERATION)]
        require(latest.state.value == "FILLED" and latest.position_state.value == "CLOSED")
        require(latest.exit_state == "FILLED" and not latest.remaining and not latest.open_quantity)
        require(latest.signal.signal_id == LATEST_OPERATION and latest.signal.symbol == "BTCUSDT")
        require(
            latest.signal.purpose == "DEMO_VALIDATION"
            and latest.filled == latest.quantity == Decimal("0.0007")
        )
        stage = "FRESH_BOUND_DETAILS"
        tokens: list[str] = []
        fresh = details.inspect(store, client, account, on_token=tokens.append)
        validate_reference(fresh)
        require(fresh["unresolved_set_digest"] == reference["unresolved_set_digest"])
        require(
            details.digest(fresh["unresolved_requests"])
            == details.digest(reference["unresolved_requests"])
        )
        require(fresh["audited_request_count"] == reference["audited_request_count"])
        require(len(tokens) == 1)
        require(store.read(account) == before)
        stage = "CANONICAL_APPLY"
        lease = store.acquire(account, uuid4())
        apply_started = True
        result = recovery.acknowledge_flat_emergency(
            store, lease, client, OPERATION, tokens[0], acknowledge_unresolved=True
        )
        require(
            result in {"EMERGENCY_ACKNOWLEDGED_ENTRIES_STILL_PAUSED", "ALREADY_RECORDED_NO_ACTION"}
        )
        after = store.read(account)
        stage = "PRESERVATION_CHECK"
        excluded = {"risk_state", "reconciled", "stream_ready", "recovery_receipts", "operations"}
        require(
            before.model_dump(mode="json", exclude=excluded)
            == after.model_dump(mode="json", exclude=excluded)
        )
        require(after.operations == {**before.operations, key: "ACKNOWLEDGE_FLAT_EMERGENCY"})
        require(after.recovery_receipts == {**before.recovery_receipts, key: tokens[0]})
        require(after.risk_state.value == "NORMAL" and after.paused)
        stage = "LEASE_RELEASE"
        store.release(lease)
        current = details.row(store.engine, account)
        require(current["owner"] is None and current["fence"] == lease.fence)
        released = True
        lease = None
        stage = "POST_APPLY_READY"
        ready = ready_check()
        return {
            "recovery": "EMERGENCY_ACKNOWLEDGED_READY_ENTRIES_STILL_PAUSED",
            "operation_id": key,
            "application_result": result,
            "risk_state": "NORMAL",
            "paused": True,
            "unresolved_history_acknowledged": True,
            "unresolved_requests_preserved": True,
            "unresolved_statuses_cleared": False,
            "unresolved_count_at_acknowledgement": 4,
            "financial_and_prior_history_preserved": True,
            "leader_released": True,
            "readiness": ready,
            "orders_enabled": False,
            "live_trading_enabled": False,
        }
    except Exception as error:  # noqa: BLE001 -- no raw tokens/private responses/database errors
        if lease is not None:
            try:
                store.release(lease)
                released = True
            except Exception:
                released = False
        return {
            "recovery": "BLOCKED_KEEP_PAUSED_NO_AUTOMATIC_RETRY",
            "stage": stage,
            "error_code": error.code
            if isinstance(error, recovery.RecoveryBlocked)
            else "SCOPE_OR_REVIEW_NOT_VERIFIED",
            "application_may_have_completed": apply_started,
            "leader_released": released,
            "operation_id": str(OPERATION),
            "orders_enabled": False,
            "live_trading_enabled": False,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--acknowledge-four-historical-unresolved", action="store_true", required=True
    )
    args = parser.parse_args()
    engine = None
    try:
        require(os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false")
        require(os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE")
        require(
            hashlib.sha256((DIRECTORY / "review_unresolved_details.py").read_bytes()).hexdigest()
            == HELPER_SHA256
        )
        require(
            all(
                hashlib.sha256(Path(n).read_bytes()).hexdigest() == h
                for n, h in details.SOURCE_HASHES.items()
            )
        )
        raw = (DIRECTORY / "reference.json").read_bytes()
        require(len(raw) <= 65536)
        reference = json.loads(raw)
        cfg = DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
            )
        ).model_copy(update={"demo_trading_enabled": False})
        client = DemoClient(cfg, credentials(prompt=False))
        client.sync_clock()
        from sqlalchemy import create_engine

        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        report = execute(
            DemoStore(engine),
            client,
            reference,
            acknowledged=args.acknowledge_four_historical_unresolved,
        )
    except Exception:  # noqa: BLE001 -- do not leak credential/reference/DB data
        report = {
            "recovery": "BLOCKED_BEFORE_APPLY",
            "orders_enabled": False,
            "live_trading_enabled": False,
        }
    finally:
        if engine is not None:
            engine.dispose()
    print(json.dumps(report), flush=True)
    if str(report["recovery"]).startswith("BLOCKED"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
