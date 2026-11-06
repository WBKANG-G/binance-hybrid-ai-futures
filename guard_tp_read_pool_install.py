"""Read-only pre/post guard for API code update; never applies recovery or trading."""

import argparse
import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid5

import yaml
from sqlalchemy import create_engine, select

from integrations.binance_usdm.demo import DemoClient, DemoNotFound, DemoSettings
from integrations.binance_usdm.demo_snapshot import snapshot
from integrations.binance_usdm.recovery_transport import (
    recovery_connection,
    safe_transport_diagnostics,
)
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import DemoStore, accounts
from scripts.demo_key_session import credentials
from services.execution.demo_empty_db_restart import empty
from services.execution.demo_models import DemoLedger
from services.execution.demo_recovery import RecoveryBlocked, _check_loss_gates, local_period_status
from services.execution.demo_recovery_budget import (
    AUDIT_BATCH_SIZE,
    MAX_HISTORY_REQUESTS,
    AuditBudgetExceeded,
    RecoveryReadClient,
)

ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"
OPERATION = UUID("31c953e8-72dc-422d-89f3-8bbe7cdb5c0c")
OLD_SHA = "40522b50012be16fb6a888d67645bc72572d7da7c24b58f4b2b87f8edf9c7018"
NEW_SHA = "0bef5c186d6e4a5c226f66736085585cf1d209d13ef1bd8622666d44d52966f9"
SOURCE = Path("scripts/check_demo_tp_trigger.py")
POOL_SHA = "a6e96b21135882a272cb5c97f86ddc14c3a5f4fa79dd115e40397252c679a66d"
UNCHANGED = {
    "services/execution/demo_empty_db_restart.py": "6adcc8284b43050bc4a115f2796c001d424ba7e0261654c099075fb3d950e39f",
    "integrations/binance_usdm/demo_snapshot.py": "02e5d789280f3ed8aa2667319248982d5cd4eedd2fb8b72e70aef8785e57ed6a",
    "services/execution/demo_recovery_budget.py": "2c7aa260fd708948b0ae7854257de03bc8b59c86da85180ea5230b07e8df772b",
    "services/execution/demo_recovery.py": "ca663f098d2f7c462d1f225f4ade91d743169175f125c574794dc58175c8c76e",
    "services/execution/demo_protected_worker_check.py": "70d9f0a6ec74223e8a7a82ec55c976288c10fb13c01c9b478f49f1a0fd408709",
    "services/execution/demo_tp_trigger_check.py": "6b82ce5947480f87a58c194628853322496c90545a47713f4e07dd1fc0414dc8",
    "services/execution/demo_validation.py": "d703860caf01135b3f5e82a444adc5afde1795e1f4d48bbd515cc4b9de5666a0",
    "services/execution/demo_stream.py": "409098cb05abbfb97eb9b9f1db2070ca6bb5782bc6dd7542311e4eb703b6f456",
    "packages/persistence/demo.py": "40f9a33b8eeda28c96f36de35656e4667a2d2461ce9de79dc4b19a687cd5cd5a",
    "integrations/binance_usdm/demo.py": "c5ec1f89d645dcd124a71cdf9cb39884cc9cfa5a2493636baf1c08db2c581a81",
    "integrations/binance_usdm/recovery_transport.py": "11d66dd5e05b4707b5794707df0fa889be4cbb1204fcd7a0ec23d9c6008ea791",
    "configs/demo-isolated.example.yaml": "d93e85c2e056ffbbc3dac5fab9f0e5bf28032fd16f41bbff1f3ae81fb00882ca",
}
UNRESOLVED_REFS = {"85296c5f5538864a", "b1501465465b6295", "7d101d4ff65720e7", "db0c256e607ba61e"}


CODES = {
    "INSTALL_GUARD_BLOCKED",
    "REQUEST_AUDIT_TIME_BUDGET",
    "REQUEST_AUDIT_QUERY_BUDGET",
    "REQUEST_AUDIT_CLOCK_BUDGET",
    "LOSS_OR_ACCOUNT_RISK_BLOCKED",
    "PERIOD_BASELINE_MISSING",
    "SOURCE_HASH_MISMATCH",
    "ACCOUNT_CHANGED_DURING_REVIEW",
    "NORMAL_AUDIT_CONTEXT_MISMATCH",
    "NORMAL_AUDIT_PERIOD_MISMATCH",
    "NORMAL_AUDIT_SNAPSHOT_CHANGED",
    "NORMAL_AUDIT_REMOTE_EXPOSURE",
    "NORMAL_AUDIT_SETTINGS_OR_TRANSFER_MISMATCH",
    "NORMAL_AUDIT_HISTORY_LIMIT",
    "NORMAL_AUDIT_REQUEST_IDENTITY_MISMATCH",
    "NORMAL_AUDIT_REQUEST_ACTIVE_OR_INVALID",
    "NORMAL_AUDIT_UNRESOLVED_COHORT_CHANGED",
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


def normal_audit(store, client, ledger):
    # NORMAL has its own installation review. Do not fake EMERGENCY or weaken
    # the canonical recovery review's EMERGENCY-only applicability rule.
    empty(ledger)
    require(
        all(
            o.state.value in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}
            for o in ledger.orders.values()
        ),
        "NORMAL_AUDIT_CONTEXT_MISMATCH",
    )
    require(
        not client.settings.demo_trading_enabled
        and client.settings.margin_mode == "ISOLATED"
        and client.settings.symbols == ("BTCUSDT",)
        and client.settings.policy == ledger.policy,
        "NORMAL_AUDIT_CONTEXT_MISMATCH",
    )
    require(
        local_period_status(store, ledger)["period_keys_match"] is True,
        "NORMAL_AUDIT_PERIOD_MISMATCH",
    )
    require(len(ledger.requests) <= MAX_HISTORY_REQUESTS, "NORMAL_AUDIT_HISTORY_LIMIT")
    with recovery_connection(client) as pooled:
        reader = RecoveryReadClient(client)
        reader.sync_clock()
        first = snapshot(reader, ledger, store.now())
        second = snapshot(reader, ledger, store.now())
        require(first.fingerprint == second.fingerprint, "NORMAL_AUDIT_SNAPSHOT_CHANGED")
        require(
            not any(second.positions.values())
            and not second.margin_used
            and not any(r["status"] in {"NEW", "PARTIALLY_FILLED"} for r in second.orders.values())
            and not any(r["algoStatus"] == "NEW" for r in second.algos.values()),
            "NORMAL_AUDIT_REMOTE_EXPOSURE",
        )
        require(
            second.leverage.get("BTCUSDT") == 2 and second.transfers == ledger.income_seen,
            "NORMAL_AUDIT_SETTINGS_OR_TRANSFER_MISMATCH",
        )
        _check_loss_gates(store, ledger, second.equity)
        observations = {}
        identifiers = sorted(ledger.requests)
        for offset in range(0, len(identifiers), AUDIT_BATCH_SIZE):
            reader.budget.check()
            for identifier in identifiers[offset : offset + AUDIT_BATCH_SIZE]:
                kind = identifier.split("-", 1)[0]
                require(kind in {"DE", "DX", "DS", "DT"}, "NORMAL_AUDIT_REQUEST_IDENTITY_MISMATCH")
                is_algo = kind in {"DS", "DT"}
                try:
                    raw = reader.request(
                        "GET",
                        "/fapi/v1/algoOrder" if is_algo else "/fapi/v1/order",
                        {"clientAlgoId": identifier}
                        if is_algo
                        else {"symbol": "BTCUSDT", "origClientOrderId": identifier},
                    )
                except DemoNotFound:
                    observations[identifier] = "NOT_FOUND_UNRESOLVED"
                    continue
                require(
                    isinstance(raw, dict)
                    and raw.get("symbol") == "BTCUSDT"
                    and raw.get("clientAlgoId" if is_algo else "clientOrderId") == identifier,
                    "NORMAL_AUDIT_REQUEST_IDENTITY_MISMATCH",
                )
                status = raw.get("algoStatus" if is_algo else "status")
                allowed = (
                    {"CANCELED", "EXPIRED", "FINISHED", "REJECTED"}
                    if is_algo
                    else {"FILLED", "CANCELED", "EXPIRED", "EXPIRED_IN_MATCH", "REJECTED"}
                )
                require(status in allowed, "NORMAL_AUDIT_REQUEST_ACTIVE_OR_INVALID")
                observations[identifier] = status
        final = snapshot(reader, ledger, store.now())
        require(
            final.fingerprint == second.fingerprint and final.transfers == second.transfers,
            "NORMAL_AUDIT_SNAPSHOT_CHANGED",
        )
        _check_loss_gates(store, ledger, final.equity)
        unresolved = {
            hashlib.sha256(identifier.encode()).hexdigest()[:16]
            for identifier, status in observations.items()
            if status == "NOT_FOUND_UNRESOLVED"
        }
        require(unresolved == UNRESOLVED_REFS, "NORMAL_AUDIT_UNRESOLVED_COHORT_CHANGED")
        reader.budget.check()
        return {
            "installation_review": "NORMAL_PAUSED_FULL_READ_ONLY_AUDIT",
            "risk_gates": "PASS",
            "position_nonzero": False,
            "active_orders": 0,
            "risk_state": "NORMAL",
            "paused": True,
            "period_keys_match": True,
            "unresolved_count": len(unresolved),
            "audited_request_count": len(observations),
            "signed_query_count": reader.budget.signed_queries,
            "orders_enabled": False,
            "live_trading_enabled": False,
            "read_diagnostic": safe_transport_diagnostics(
                pooled.diagnostics() if pooled else {"audit_transport_mode": "UNPOOLED_TRANSPORT"}
            ),
        }


def review(engine, ledger, client=None):
    if client is None:
        cfg = DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
            )
        ).model_copy(update={"demo_trading_enabled": False})
        client = DemoClient(cfg, credentials(prompt=False))
    return normal_audit(DemoStore(engine), client, ledger)


def validate_review(value):
    require(
        value.get("installation_review") == "NORMAL_PAUSED_FULL_READ_ONLY_AUDIT"
        and value.get("period_keys_match") is True
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


def run(engine, phase, *, read_local=local, read_review=None, digest=None, client=None):
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
    result = read_review() if read_review is not None else review(engine, before[-1], client)
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
        "installation_review": result["installation_review"],
        "audited_request_count": result["audited_request_count"],
        "signed_query_count": result["signed_query_count"],
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
    except Exception as error:  # noqa: BLE001 -- private query details stay in RAM
        failure = (
            str(error)
            if isinstance(error, GuardBlocked)
            else (
                error.code
                if isinstance(error, (RecoveryBlocked, AuditBudgetExceeded))
                else "INSTALL_CHECK_UNAVAILABLE"
            )
        )
        report = {
            "tp_read_pool_install": "BLOCKED_KEEP_PAUSED_NO_AUTOMATIC_RETRY",
            "phase": args.phase,
            "details_redacted": True,
            "failure_code": failure if failure in CODES else "INSTALL_CHECK_UNAVAILABLE",
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
