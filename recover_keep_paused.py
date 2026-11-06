"""One explicitly invoked, paused-only recovery of the previously reviewed four requests.

Embedded bytes match the separately shipped reviewed source/reference files.
No account credentials or review token are embedded. No security setting changes.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

DIRECTORY = Path("/tmp/sigkill-flat-recovery-14aac5ad5a1347208d9d4a1074c4cb65")
EXPECTED = {
    "acknowledge_flat_recovery.py": "c8b7edf3700bdbb30499196cc1f4a99956a43a7377d5442d789cb4b032ffba55",
    "reference.json": "34d6f4d10ec18d9f7a91ccb9bbd920c849f238b4c8ee501d6e3bc1e7af93826d",
    "review_unresolved_details.py": "62ef93891359953f6b9d9a6442833ee3adb508475d0faeb486ccdb2b261b7e1b",
}
PAYLOAD: dict[str, str] = {
    "acknowledge_flat_recovery.py": r'''"""Explicitly acknowledged, fixed-history recovery; entries always remain paused."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import timedelta, timezone
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

OPERATION = UUID("14aac5ad-5a13-4720-8d9d-4a1074c4cb65")
PRIOR_RECEIPT = "53d066a9-56dc-4042-9b64-204eb5048156"
EARLIER_RECEIPT = "0f72dd2e-9c5d-4567-aa46-9f3772f79ea7"
PRIOR_OPERATION = UUID("5bb5814c-55e0-4b7f-882c-a7a449ec2182")
INTERMEDIATE_OPERATION = UUID("e85a00f2-6b8e-4b39-bac2-b958a001c8c0")
LATEST_OPERATION = UUID("c75410e5-cbbb-4a75-bfe9-5272a8b447c6")
DIRECTORY = Path("/tmp/sigkill-flat-recovery-14aac5ad5a1347208d9d4a1074c4cb65")
HELPER_SHA256 = "62ef93891359953f6b9d9a6442833ee3adb508475d0faeb486ccdb2b261b7e1b"
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


class ScopeMismatch(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def require(condition: Any, code: str = "ACKNOWLEDGED_SCOPE_OR_STATE_CHANGED") -> None:
    if not condition:
        raise ScopeMismatch(code)


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
        require(
            link["operation_id"]
            not in {str(LATEST_OPERATION), str(PRIOR_OPERATION), str(INTERMEDIATE_OPERATION)}
        )
        require(link["position_state"] == "CLOSED")
        require(Decimal(link["open_quantity"]) == Decimal(link["remaining_quantity"]) == 0)
        require(link["role"] == {"DS": "STOP", "DX": "EXIT", "DT": "TARGET"}[row["request_kind"]])
        if row["local_request_status"] == "NOT_SENT":
            require(
                link["operation_id"]
                == (
                    "77db044e-1bcd-4358-80e6-df9730630213"
                    if row["request_kind"] == "DS"
                    else "426dd7a2-805b-4576-a1f4-85313a7c4f6c"
                ),
                "HISTORICAL_NOT_SENT_OPERATION_ID_MISMATCH",
            )
            require(row["local_no_send_evidence"] == "LOCAL_VALIDATION_FAILED")
            require(link["signal_closed_candle_at"].startswith("2026-10-31T"))
            require(link["entry_state"] == link["exit_state"] == "FILLED")
            require(Decimal(link["filled_quantity"]) == Decimal("0.0007"))
        else:
            require(
                link["operation_id"] == "b8454548-08d4-4cc3-9e77-d411b5659eff",
                "HISTORICAL_ZERO_FILL_OPERATION_ID_MISMATCH",
            )
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
        timeout=150,
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
    lease_acquired = False
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
            before.daily_key == "2026-11-06" and before.weekly_key == "2026-11-02",
            "PERIOD_SCOPE_CHANGED",
        )
        require(
            store.now().astimezone(timezone(timedelta(hours=9))).date().isoformat() == "2026-11-06",
            "KST_DATE_CHANGED",
        )
        for receipt in (EARLIER_RECEIPT, PRIOR_RECEIPT):
            require(
                before.operations.get(receipt) == "ACKNOWLEDGE_FLAT_EMERGENCY",
                "PRIOR_ACK_RECEIPT_MISSING",
            )
            require(
                re.fullmatch(r"[0-9a-f]{64}", before.recovery_receipts.get(receipt, ""))
                is not None,
                "PRIOR_ACK_RECEIPT_MISSING",
            )
        for incident in (INTERMEDIATE_OPERATION, LATEST_OPERATION):
            require(
                before.operations.get(str(uuid5(incident, "protected-worker-ready")))
                == "FULL_PROTECTED_READY",
                "READY_MARKER_MISMATCH",
            )
        require(
            json.loads(
                before.operations.get(
                    str(uuid5(INTERMEDIATE_OPERATION, "protected-worker-failure")), "{}"
                )
            )
            == {"stage": "STREAM_OBSERVATION", "code": "NONE"},
            "INTERMEDIATE_CHILD_FAILURE_MISMATCH",
        )
        require(
            str(uuid5(LATEST_OPERATION, "protected-worker-finally")) not in before.operations,
            "SIGKILL_FINALLY_UNEXPECTED",
        )
        require(
            str(uuid5(LATEST_OPERATION, "protected-worker-failure")) not in before.operations,
            "SIGKILL_CHILD_FAILURE_UNEXPECTED",
        )
        require(
            json.loads(
                before.operations.get(str(uuid5(LATEST_OPERATION, "protected-worker-counts")), "{}")
            )
            == {"entry": 1, "exit": 0},
            "SIGKILL_READY_COUNTS_MISMATCH",
        )
        require(
            str(uuid5(PRIOR_OPERATION, "protected-worker-ready")) not in before.operations,
            "PRIOR_READY_MARKER_CHANGED",
        )
        ids: set[str] = set()
        for incident in (PRIOR_OPERATION, INTERMEDIATE_OPERATION, LATEST_OPERATION):
            require(
                before.operations.get(str(uuid5(incident, "protected-worker-finish-exit")))
                == "EMERGENCY_FLATTEN",
                "INCIDENT_FINISH_MARKER_MISSING",
            )
            if incident != LATEST_OPERATION:
                require(
                    before.operations.get(str(uuid5(incident, "protected-worker-finally")))
                    == "FINALLY_RAN",
                    "INCIDENT_FINALLY_MARKER_MISSING",
                )
            latest = before.orders[str(incident)]
            require(latest.state.value == "FILLED" and latest.position_state.value == "CLOSED")
            require(
                latest.exit_state == "FILLED" and not latest.remaining and not latest.open_quantity
            )
            require(latest.signal.signal_id == incident and latest.signal.symbol == "BTCUSDT")
            require(
                latest.signal.purpose == "DEMO_VALIDATION"
                and latest.filled == latest.quantity == Decimal("0.0007")
            )
            require(latest.exit_generation == 1)
            require(latest.signal.direction == "LONG")
            own_ids = {
                latest.client_id,
                latest.algo_id,
                latest.tp_algo_id,
                latest.exit_id,
            }
            require(None not in own_ids and len(own_ids) == 4)
            require(before.requests.get(latest.client_id) in {"ACKNOWLEDGED", "FILLED"})
            require(
                before.requests.get(latest.algo_id or "")
                == before.requests.get(latest.tp_algo_id or "")
                == "VERIFIED"
            )
            require(before.requests.get(latest.exit_id or "") == "FILLED")
            require(
                before.validation_evidence.get(str(incident), {}).get("stop_verified") is True
                and before.validation_evidence.get(str(incident), {}).get("target_verified") is True
            )
            require(not ids.intersection(own_ids), "INCIDENT_IDS_OVERLAP")
            ids.update(v for v in own_ids if v is not None)
        stage = "FRESH_BOUND_DETAILS"
        tokens: list[str] = []
        fresh = details.inspect(store, client, account, on_token=tokens.append)
        validate_reference(fresh)
        require(fresh["unresolved_set_digest"] == reference["unresolved_set_digest"])
        require(
            details.digest(fresh["unresolved_requests"])
            == details.digest(reference["unresolved_requests"])
        )
        # The original approved report predates these three owned, closed trials.
        # All three exact entry/SL/TP/exit cohorts must exist; unresolved history is identical.
        require(
            fresh["audited_request_count"] == reference["audited_request_count"] + 12,
            "AUDITED_REQUEST_DELTA_NOT_TWELVE",
        )
        require(len(tokens) == 1)
        require(store.read(account) == before)
        stage = "CANONICAL_APPLY"
        lease = store.acquire(account, uuid4())
        lease_acquired = True
        apply_started = True
        result = recovery.acknowledge_flat_emergency(
            store, lease, client, OPERATION, tokens[0], acknowledge_unresolved=True
        )
        require(
            result
            in {
                "EMERGENCY_ACKNOWLEDGED_ENTRIES_STILL_PAUSED",
                "ALREADY_RECORDED_NO_ACTION",
            }
        )
        after = store.read(account)
        stage = "PRESERVATION_CHECK"
        excluded = {
            "risk_state",
            "reconciled",
            "stream_ready",
            "recovery_receipts",
            "operations",
        }
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
            if isinstance(error, (recovery.RecoveryBlocked, ScopeMismatch))
            else "SCOPE_OR_REVIEW_NOT_VERIFIED",
            "application_may_have_completed": apply_started,
            "lease_acquired": lease_acquired,
            "leader_released": released if lease_acquired else None,
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
        require(
            hashlib.sha256(
                Path("services/execution/demo_protected_worker_check.py").read_bytes()
            ).hexdigest()
            == "70d9f0a6ec74223e8a7a82ec55c976288c10fb13c01c9b478f49f1a0fd408709"
        )
        raw = (DIRECTORY / "reference.json").read_bytes()
        require(len(raw) <= 65536)
        reference = json.loads(raw)
        validate_reference(reference)
        require(
            reference.get("audited_request_count") == 78,
            "ORIGINAL_BASELINE_COUNT_CHANGED",
        )
        cfg = DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(),
                Loader=UniqueKeyLoader,
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
''',
    "reference.json": r"""{
  "recovery": "READ_ONLY_BOUND_UNRESOLVED_DETAILS_KEEP_LOCK",
  "audited_request_count": 78,
  "unresolved_count": 4,
  "risk_gates": "PASS",
  "position_nonzero": false,
  "active_orders": 0,
  "orders_enabled": false,
  "live_trading_enabled": false,
  "risk_state": "EMERGENCY",
  "paused": true,
  "unresolved_requests": [
    {"request_ref":"85296c5f5538864a","request_kind":"DS","current_query_status":"NOT_FOUND_UNRESOLVED","local_request_status":"UNKNOWN","linked_orders":[{"operation_id":"b8454548-08d4-4cc3-9e77-d411b5659eff","role":"STOP","purpose":"DEMO_VALIDATION","signal_closed_candle_at":"2026-10-20T13:49:59.999000+00:00","entry_state":"CANCELED","position_state":"CLOSED","filled_quantity":"0.0000","remaining_quantity":"0","open_quantity":"0","exit_state":"UNKNOWN","is_latest_opposite_tp":false}],"unlinked_local_request":false,"local_no_send_evidence":null,"absence_proves_never_sent_or_never_filled":false},
    {"request_ref":"b1501465465b6295","request_kind":"DS","current_query_status":"NOT_FOUND_UNRESOLVED","local_request_status":"NOT_SENT","linked_orders":[{"operation_id":"77db044e-1bcd-4358-80e6-df9730630213","role":"STOP","purpose":"DEMO_VALIDATION","signal_closed_candle_at":"2026-10-31T14:21:59.999000+00:00","entry_state":"FILLED","position_state":"CLOSED","filled_quantity":"0.0007","remaining_quantity":"0","open_quantity":"0","exit_state":"FILLED","is_latest_opposite_tp":false}],"unlinked_local_request":false,"local_no_send_evidence":"LOCAL_VALIDATION_FAILED","absence_proves_never_sent_or_never_filled":false},
    {"request_ref":"7d101d4ff65720e7","request_kind":"DT","current_query_status":"NOT_FOUND_UNRESOLVED","local_request_status":"NOT_SENT","linked_orders":[{"operation_id":"426dd7a2-805b-4576-a1f4-85313a7c4f6c","role":"TARGET","purpose":"DEMO_VALIDATION","signal_closed_candle_at":"2026-10-31T10:57:59.999000+00:00","entry_state":"FILLED","position_state":"CLOSED","filled_quantity":"0.0007","remaining_quantity":"0","open_quantity":"0","exit_state":"FILLED","is_latest_opposite_tp":false}],"unlinked_local_request":false,"local_no_send_evidence":"LOCAL_VALIDATION_FAILED","absence_proves_never_sent_or_never_filled":false},
    {"request_ref":"db0c256e607ba61e","request_kind":"DX","current_query_status":"NOT_FOUND_UNRESOLVED","local_request_status":"SUBMITTING","linked_orders":[{"operation_id":"b8454548-08d4-4cc3-9e77-d411b5659eff","role":"EXIT","purpose":"DEMO_VALIDATION","signal_closed_candle_at":"2026-10-20T13:49:59.999000+00:00","entry_state":"CANCELED","position_state":"CLOSED","filled_quantity":"0.0000","remaining_quantity":"0","open_quantity":"0","exit_state":"UNKNOWN","is_latest_opposite_tp":false}],"unlinked_local_request":false,"local_no_send_evidence":null,"absence_proves_never_sent_or_never_filled":false}
  ],
  "unresolved_set_digest": "1801ba7f74cf0ac87804b251bb1645498d278b3f008705579b27f682c6ee8de4",
  "audit_details_bound_to_canonical_review": true,
  "review_token_printed": false,
  "database_changed": false,
  "unresolved_history_acknowledged": false,
  "unlock_applied": false
}
""",
    "review_unresolved_details.py": r'''"""Read-only canonical recovery review with token-bound unresolved request details."""

import hashlib
import hmac
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from integrations.binance_usdm.demo import DemoClient, DemoNotFound, DemoSettings
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import DemoStore, accounts
from scripts.demo_key_session import credentials
from services.execution import demo_recovery as recovery
from services.execution.demo_models import DemoLedger
from services.execution.demo_recovery_budget import RecoveryReadClient
from sqlalchemy import Engine, select

ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"
LATEST_OPERATION = "243598bb-900a-45d5-8af3-a776d543a504"
SOURCE_HASHES = {
    "services/execution/demo_recovery.py": "ca663f098d2f7c462d1f225f4ade91d743169175f125c574794dc58175c8c76e",
    "services/execution/demo_recovery_budget.py": "2c7aa260fd708948b0ae7854257de03bc8b59c86da85180ea5230b07e8df772b",
    "integrations/binance_usdm/recovery_transport.py": "11d66dd5e05b4707b5794707df0fa889be4cbb1204fcd7a0ec23d9c6008ea791",
    "integrations/binance_usdm/demo_snapshot.py": "02e5d789280f3ed8aa2667319248982d5cd4eedd2fb8b72e70aef8785e57ed6a",
}
LOCAL_STATES = {
    "UNKNOWN",
    "SUBMITTING",
    "ACKNOWLEDGED",
    "FILLED",
    "CANCELED",
    "REJECTED",
    "EXPIRED",
    "NOT_SENT",
    "NOT_FOUND_UNRESOLVED",
}


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def row(engine: Engine, account: str) -> dict[str, Any]:
    with engine.connect() as conn:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout='2s'")
        return dict(
            conn.execute(select(accounts).where(accounts.c.account_id == account)).mappings().one()
        )


def classify(ledger: DemoLedger, observations: dict[str, str]) -> list[dict[str, Any]]:
    details = []
    for identifier, status in sorted(observations.items()):
        if status != "NOT_FOUND_UNRESOLVED":
            continue
        kind = identifier.split("-", 1)[0]
        if kind not in {"DE", "DX", "DS", "DT"}:
            raise ValueError("UNSUPPORTED_REQUEST_KIND")
        links = []
        for key, order in ledger.orders.items():
            for role, value in (
                ("ENTRY", order.client_id),
                ("EXIT", order.exit_id),
                ("STOP", order.algo_id),
                ("TARGET", order.tp_algo_id),
            ):
                if value != identifier:
                    continue
                operation = str(UUID(key))
                links.append(
                    {
                        "operation_id": operation,
                        "role": role,
                        "purpose": order.signal.purpose,
                        "signal_closed_candle_at": order.signal.signal_closed_candle_at.isoformat(),
                        "entry_state": order.state.value,
                        "position_state": order.position_state.value,
                        "filled_quantity": str(order.filled),
                        "remaining_quantity": str(order.remaining),
                        "open_quantity": str(order.open_quantity),
                        "exit_state": order.exit_state,
                        "is_latest_opposite_tp": operation == LATEST_OPERATION,
                    }
                )
        stored = ledger.requests[identifier]
        details.append(
            {
                "request_ref": hashlib.sha256(identifier.encode()).hexdigest()[:16],
                "request_kind": kind,
                "current_query_status": status,
                "local_request_status": stored if stored in LOCAL_STATES else "OTHER_LOCAL_STATE",
                "linked_orders": links,
                "unlinked_local_request": not links,
                "local_no_send_evidence": ledger.request_no_send_evidence.get(identifier),
                "absence_proves_never_sent_or_never_filled": False,
            }
        )
    return details


def inspect(
    store: DemoStore,
    client: DemoClient,
    account: str,
    *,
    on_token: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    before = row(store.engine, account)
    ledger = DemoLedger.model_validate(before["state"])
    known = set(ledger.requests)
    observations: dict[str, str] = {}
    fingerprints = []
    namespace = vars(recovery)
    original_client, original_snapshot = namespace["RecoveryReadClient"], namespace["snapshot"]

    class TracedReadClient(RecoveryReadClient):
        def request(self, method: str, path: str, params: Mapping[str, str] | None = None) -> Any:
            if method != "GET" or "asyn" in path:
                raise ValueError("GET_ONLY_NO_EXPORT_CREATION")
            values = params or {}
            identifier = (
                values.get("clientAlgoId")
                if path == "/fapi/v1/algoOrder"
                else values.get("origClientOrderId")
                if path == "/fapi/v1/order"
                else None
            )
            try:
                result = super().request(method, path, params)
            except DemoNotFound:
                if identifier in known:
                    observations[str(identifier)] = "NOT_FOUND_UNRESOLVED"
                raise
            if identifier in known and isinstance(result, dict):
                field = "algoStatus" if path == "/fapi/v1/algoOrder" else "status"
                observations[str(identifier)] = str(result.get(field))
            return result

    def traced_snapshot(*args: Any, **kwargs: Any) -> Any:
        result = original_snapshot(*args, **kwargs)
        fingerprints.append(result.fingerprint)
        return result

    namespace["RecoveryReadClient"], namespace["snapshot"] = TracedReadClient, traced_snapshot
    try:
        report = recovery.review(store, client, ledger)
    finally:
        namespace["RecoveryReadClient"], namespace["snapshot"] = original_client, original_snapshot
    if (
        set(observations) != known
        or len(observations) != report["audited_request_count"]
        or not fingerprints
    ):
        raise ValueError("INCOMPLETE_AUDIT_DETAILS")
    stable = ledger.model_dump(mode="json", exclude={"reconciled", "stream_ready"})
    reconstructed = digest(
        {"ledger": stable, "exchange": fingerprints[-1], "observations": observations}
    )
    if not hmac.compare_digest(reconstructed, str(report["review_token"])):
        raise ValueError("AUDIT_DETAILS_TOKEN_BINDING_MISMATCH")
    details = classify(ledger, observations)
    if len(details) != report["unresolved_count"]:
        raise ValueError("UNRESOLVED_COUNT_MISMATCH")
    if digest(json.loads(json.dumps(before, default=str))) != digest(
        json.loads(json.dumps(row(store.engine, account), default=str))
    ):
        raise ValueError("ACCOUNT_CHANGED_DURING_REVIEW")
    safe = {k: v for k, v in report.items() if k != "review_token"}
    if on_token is not None:
        on_token(reconstructed)
    return {
        "recovery": "READ_ONLY_BOUND_UNRESOLVED_DETAILS_KEEP_LOCK",
        **safe,
        "risk_state": ledger.risk_state.value,
        "paused": ledger.paused,
        "unresolved_requests": details,
        "unresolved_set_digest": digest(
            sorted(
                identifier
                for identifier, status in observations.items()
                if status == "NOT_FOUND_UNRESOLVED"
            )
        ),
        "audit_details_bound_to_canonical_review": True,
        "review_token_printed": False,
        "database_changed": False,
        "account_owner_fence_lease_state_unchanged": True,
        "unresolved_history_acknowledged": False,
        "unlock_applied": False,
    }


def main() -> None:
    from sqlalchemy import create_engine

    engine = None
    try:
        if (
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
            or os.getenv("TRADING_MODE", "PAPER").upper() == "LIVE"
        ):
            raise ValueError("DEMO_CONTEXT_REQUIRED")
        if any(
            hashlib.sha256(Path(name).read_bytes()).hexdigest() != value
            for name, value in SOURCE_HASHES.items()
        ):
            raise ValueError("CANONICAL_REVIEW_SOURCE_MISMATCH")
        cfg = DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
            )
        ).model_copy(update={"demo_trading_enabled": False})
        client = DemoClient(cfg, credentials(prompt=False))
        client.sync_clock()
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        report = inspect(DemoStore(engine), client, ACCOUNT)
    except recovery.RecoveryBlocked as error:
        report = {
            "recovery": "BLOCKED_KEEP_LOCK",
            "error_code": error.code,
            "orders_enabled": False,
            "live_trading_enabled": False,
        }
    except Exception:  # noqa: BLE001 -- no raw private response/token/environment
        report = {
            "recovery": "BLOCKED_KEEP_LOCK",
            "error_code": "DETAIL_REVIEW_NOT_VERIFIED",
            "orders_enabled": False,
            "live_trading_enabled": False,
        }
    finally:
        if engine is not None:
            engine.dispose()
    print(json.dumps(report), flush=True)
    if report["recovery"] == "BLOCKED_KEEP_LOCK":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
''',
}


def decode(payload: dict[str, str] = PAYLOAD) -> dict[str, bytes]:
    if set(payload) != set(EXPECTED):
        raise ValueError("PAYLOAD_FILES_MISMATCH")
    files = {name: text.encode("utf-8") for name, text in payload.items()}
    if any(
        len(data) > 131072 or hashlib.sha256(data).hexdigest() != EXPECTED[name]
        for name, data in files.items()
    ):
        raise ValueError("PAYLOAD_SOURCE_MISMATCH")
    return files


def run(
    *,
    directory: Path = DIRECTORY,
    invoke: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    payload: dict[str, str] = PAYLOAD,
) -> int:
    created = started = False
    code = 2
    previous_umask = os.umask(0o077)
    try:
        files = decode(payload)
        directory.mkdir(mode=0o700)
        created = True
        for name, data in files.items():
            (directory / name).write_bytes(data)
        print(
            json.dumps(
                {
                    "recovery_loader_stage": "PINNED_SOURCES_AND_APPROVED_REFERENCE_VERIFIED",
                    "orders_enabled": False,
                    "live_trading_enabled": False,
                }
            ),
            flush=True,
        )
        # Python stdin retains /app imports. No timeout may terminate canonical
        # recovery between applying the receipt and releasing its lease.
        with (directory / "acknowledge_flat_recovery.py").open("rb") as source:
            started = True
            result = invoke(
                [sys.executable, "-", "--acknowledge-four-historical-unresolved"],
                stdin=source,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        code = result.returncode
    except Exception:  # noqa: BLE001 -- never expose DB, token or subprocess exception details
        print(
            json.dumps(
                {
                    "recovery_loader": "BLOCKED_KEEP_PAUSED_NO_AUTOMATIC_RETRY",
                    "execution_started": started,
                    "application_may_have_completed": started,
                    "orders_enabled": False,
                    "live_trading_enabled": False,
                }
            ),
            flush=True,
        )
    finally:
        try:
            if created:
                shutil.rmtree(directory)
        except OSError:
            print(
                json.dumps(
                    {
                        "recovery_loader": "TEMPORARY_FILE_CLEANUP_FAILED_NO_AUTOMATIC_RETRY",
                        "application_may_have_completed": started,
                        "orders_enabled": False,
                        "live_trading_enabled": False,
                    }
                ),
                flush=True,
            )
            code = 2
        os.umask(previous_umask)
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--acknowledge-four-historical-unresolved", action="store_true", required=True
    )
    parser.parse_args()
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
