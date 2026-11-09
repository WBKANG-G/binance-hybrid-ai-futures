"""Non-mutating local startup assessment. No exchange client or credential access."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from packages.persistence.demo import accounts
from packages.persistence.demo_order_quota import (
    DEFAULT_LOCAL_POLICY,
    FLOW_SLOTS,
    RESTART_HOLD_SECONDS,
    DurableOrderQuota,
    LocalQuotaStopped,
    utc_stamp,
)
from services.execution.demo_managed_order_quota import (
    STORAGE_KEY,
    validate_journal_bindings,
)
from services.execution.demo_models import DemoLedger
from services.risk.paper import period_keys

UNRESOLVED = frozenset({"SUBMITTING", "UNKNOWN", "CANCEL_REQUESTED", "NOT_FOUND_UNRESOLVED"})
JOURNAL_CODES = frozenset(
    {
        "QUOTA_MANAGED_WRITER_BUSY",
        "QUOTA_PRIVATE_DIRECTORY_REQUIRED",
        "QUOTA_FILE_IDENTITY_INVALID",
        "QUOTA_FILE_REPLACED",
        "QUOTA_JOURNAL_MISSING",
        "QUOTA_JOURNAL_CORRUPT",
        "QUOTA_JOURNAL_SIZE_LIMIT",
        "QUOTA_JOURNAL_CHAIN_INVALID",
        "QUOTA_JOURNAL_PAYLOAD_INVALID",
        "QUOTA_STORAGE_BINDING_MISMATCH",
        "QUOTA_PG_JOURNAL_FLOW_MISMATCH",
        "QUOTA_PG_JOURNAL_SEAL_MISMATCH",
        "QUOTA_FLOW_INVALID",
        "QUOTA_EVENT_KIND_INVALID",
    }
)


def stopped(code: str) -> dict[str, Any]:
    return {
        "managed_quota_startup_review": "BLOCKED_KEEP_PAUSED",
        "blockers": [code],
        "local_storage_consistent": False,
        "review_scope": "LOCAL_SNAPSHOT_ONLY_NOT_EXCHANGE_READINESS",
        "external_account_usage": "UNKNOWN",
        "exchange_checked": False,
        "http_attempts": 0,
        "order_writes": 0,
        "database_changed_by_review": False,
        "journal_changed_by_review": False,
        "leader_acquired": False,
        "session_started": False,
        "storage_created": False,
        "execution_approved": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
        "next_action": "REVIEW_LOCAL_BLOCKERS_NO_RETRY_OR_RESUME",
    }


def assess_snapshot(
    ledger: DemoLedger,
    account: str,
    directory: Path,
    *,
    now: datetime,
    lease_until: datetime | None,
) -> dict[str, Any]:
    """The caller supplies a PostgreSQL read-only snapshot; no lease is acquired."""
    report = stopped("STORAGE_NOT_PROVISIONED")
    utc_stamp(now)
    if not account.startswith("DEMO-") or len(account) > 80:
        return stopped("DEMO_ACCOUNT_REQUIRED")
    raw = ledger.operations.get(STORAGE_KEY)
    if raw is None:
        return report  # No filesystem access or implicit bootstrap.
    try:
        binding = json.loads(raw)
        if not isinstance(binding, dict) or not isinstance(binding.get("journal"), str):
            return stopped("STORAGE_BINDING_INVALID")
        identity = UUID(binding["journal"])
        with_read_only = DurableOrderQuota(directory, account, identity, read_only=True)
        try:
            validate_journal_bindings(ledger, with_read_only)
            flows = with_read_only.flow_records()
        finally:
            with_read_only.close()
    except LocalQuotaStopped as error:
        code = str(error)
        return stopped(code if code in JOURNAL_CODES else "JOURNAL_REVIEW_FAILED")
    except OSError:
        return stopped("JOURNAL_STORAGE_UNAVAILABLE")
    except sqlite3.Error:
        return stopped("JOURNAL_DATABASE_INVALID")
    except (ValueError, TypeError, KeyError):
        return stopped("STORAGE_BINDING_INVALID")

    active = [f for f in flows.values() if f["seal"] is None]
    local_blockers = []
    if lease_until is not None and lease_until > now:
        local_blockers.append("ACTIVE_LEADER_LEASE")
    if not ledger.paused:
        local_blockers.append("ACCOUNT_NOT_PAUSED")
    if ledger.risk_state.value != "NORMAL":
        local_blockers.append("RISK_NOT_NORMAL")
    if any(ledger.positions.values()) or any(
        o.remaining or o.open_quantity or o.position_state.value != "CLOSED"
        for o in ledger.orders.values()
    ):
        local_blockers.append("LOCAL_EXPOSURE_NOT_CLOSED")
    day, week = period_keys(now, ledger.policy.week_start_kst)
    if (day, week) != (ledger.daily_key, ledger.weekly_key):
        local_blockers.append("PERIOD_KEYS_MISMATCH")
    if active:
        local_blockers.append("PENDING_FLOW_REQUIRES_RECONCILIATION")
    claims = sum(len(f["claims"]) for f in flows.values())
    report.update(
        {
            "managed_quota_startup_review": (
                "BLOCKED_KEEP_PAUSED" if local_blockers else "LOCAL_CHECKS_PASSED_KEEP_PAUSED"
            ),
            "local_storage_consistent": True,
            "local_checks_passed": not local_blockers,
            "blockers": local_blockers,
            "remaining_execution_blockers": [
                "EXTERNAL_ACCOUNT_ORDER_USAGE_UNKNOWN",
                "REQUEST_COST_CONTRACT_REVIEW_PENDING",
                "SHARED_IP_CAPACITY_NOT_RESERVED",
                "PRODUCTION_DEFAULT_EXECUTION_DISABLED",
            ],
            "pending_flows": len(active),
            "sealed_flows": len(flows) - len(active),
            "durable_claims": claims,
            "pending_reserved_slots": len(active) * FLOW_SLOTS,
            # No writer session exists, so even a clean snapshot grants no credit.
            "slots_10s": None,
            "slots_60s": None,
            "writer_restart_hold_seconds": RESTART_HOLD_SECONDS,
            "policy_digest": DEFAULT_LOCAL_POLICY.digest,
            "historical_unresolved_request_count": sum(
                state in UNRESOLVED for state in ledger.requests.values()
            ),
            "period_keys_match": (day, week) == (ledger.daily_key, ledger.weekly_key),
            "paused": ledger.paused,
            "risk_state": ledger.risk_state.value,
            "next_action": "KEEP_PAUSED_NO_AUTOMATIC_APPLY_OR_RECOVERY",
        }
    )
    return report


def review_startup(
    engine: Engine, account: str, directory: Path, *, now: datetime | None = None
) -> dict[str, Any]:
    """PostgreSQL enforces read-only access; SQLite uses mode=ro and a shared lock."""
    if engine.dialect.name != "postgresql":
        return stopped("POSTGRESQL_REQUIRED")
    if not account.startswith("DEMO-") or len(account) > 80:
        return stopped("DEMO_ACCOUNT_REQUIRED")
    observed = now or datetime.now(UTC)
    try:
        utc_stamp(observed)
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            connection.execute(text("SET LOCAL statement_timeout = '2s'"))
            connection.execute(text("SET LOCAL lock_timeout = '2s'"))
            row = (
                connection.execute(
                    select(accounts.c.state, accounts.c.lease_until).where(
                        accounts.c.account_id == account
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return stopped("DEMO_ACCOUNT_NOT_FOUND")
            result = assess_snapshot(
                DemoLedger.model_validate(row["state"]),
                account,
                directory,
                now=observed,
                lease_until=row["lease_until"],
            )
            result["database_access_mode"] = "POSTGRESQL_REPEATABLE_READ_READ_ONLY"
            return result
    except SQLAlchemyError:
        return stopped("LOCAL_DATABASE_ACCESS_FAILED")
    except (ValueError, TypeError, LocalQuotaStopped):
        return stopped("LOCAL_SNAPSHOT_INVALID")
