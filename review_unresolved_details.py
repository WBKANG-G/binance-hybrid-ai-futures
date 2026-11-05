"""Read-only canonical recovery review with token-bound unresolved request details."""

import hashlib
import hmac
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import Engine, select

from integrations.binance_usdm.demo import DemoClient, DemoNotFound, DemoSettings
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import DemoStore, accounts
from scripts.demo_key_session import credentials
from services.execution import demo_recovery as recovery
from services.execution.demo_models import DemoLedger
from services.execution.demo_recovery_budget import RecoveryReadClient

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


def inspect(store: DemoStore, client: DemoClient, account: str) -> dict[str, Any]:
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
