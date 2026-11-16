"""Project four requests' stored events on a fresh copy; never replay side effects."""

import hashlib
import re
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select, text

from packages.persistence.demo import accounts
from packages.persistence.demo_read_audit import require
from scripts.review_demo_historical_receipts import projected_timelines

ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"
REPORT_KEYS = (
    "copy_event_review",
    "account_state_fingerprint_sha256",
    "historical_unresolved_requests",
    "stored_day_kst",
    "stored_week_kst",
    "summary",
    "projection",
    "account_read_session_read_only",
    "backup_account_fingerprint_unchanged_observed",
    "current_original_database_checked",
    "event_transition_projection_read",
    "full_event_content_validated",
    "strict_execution_order_verified",
    "wire_submission_or_non_submission_verified",
    "local_no_send_reason_independently_verified",
    "request_resolution_authorized",
    "resend_authorized",
    "request_states_changed",
    "financial_ledger_changed",
    "period_baselines_changed",
    "clock_rebased",
    "shared_journal_changed",
    "resource_checkpoint_changed",
    "automatic_retry",
    "exchange_http_attempts",
    "order_writes",
    "installation_ready",
    "execution_approved",
    "orders_enabled",
    "live_trading_enabled",
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def plan_from_state(state: Any, expected: Any) -> list[dict[str, Any]]:
    require(
        isinstance(state, dict) and state.get("mode") == "DEMO" and state.get("paused") is True,
        "COPY_EVENTS_ACCOUNT_SCOPE_INVALID",
    )
    require(
        state.get("daily_key") == "2026-11-13" and state.get("weekly_key") == "2026-11-09",
        "COPY_EVENTS_PERIOD_SCOPE_INVALID",
    )
    orders, requests = state.get("orders"), state.get("requests")
    require(
        isinstance(orders, dict)
        and len(orders) == 34
        and isinstance(requests, dict)
        and len(requests) <= 4096,
        "COPY_EVENTS_LEDGER_SCOPE_INVALID",
    )
    require(
        isinstance(expected, dict)
        and expected.get("policy") == "HISTORICAL_ORIGINAL_ID_EVIDENCE_20261113_V1"
        and expected.get("account") == ACCOUNT
        and isinstance(expected.get("plan"), list)
        and len(expected["plan"]) == 4,
        "COPY_EVENTS_EXPECTED_PLAN_INVALID",
    )
    rows = []
    for identifier, status in requests.items():
        if status not in {"UNKNOWN", "SUBMITTING", "CANCEL_REQUESTED", "NOT_FOUND_UNRESOLVED"}:
            continue
        require(
            isinstance(identifier, str) and 0 < len(identifier) <= 128,
            "COPY_EVENTS_REQUEST_INVALID",
        )
        matches = [
            (o, role)
            for o in orders.values()
            if isinstance(o, dict)
            for role, field in [
                ("ENTRY", "client_id"),
                ("STOP_LOSS", "algo_id"),
                ("EXIT", "exit_id"),
            ]
            if o.get(field) == identifier
        ]
        require(len(matches) == 1, "COPY_EVENTS_UNIQUE_ORDER_REQUIRED")
        order, role = matches[0]
        require(
            isinstance(order.get("signal"), dict)
            and order["signal"].get("symbol") == "BTCUSDT"
            and order["signal"].get("environment") == "DEMO"
            and order.get("position_state") == "CLOSED"
            and isinstance(order.get("client_id"), str)
            and 0 < len(order["client_id"]) <= 128,
            "COPY_EVENTS_ORDER_SCOPE_INVALID",
        )
        pin = digest(identifier)
        targets = [
            p
            for p in expected["plan"]
            if isinstance(p, dict)
            and p.get("request_reference_sha256") == pin
            and p.get("role") == role
            and p.get("state") == status
        ]
        require(len(targets) == 1, "COPY_EVENTS_EXPECTED_PLAN_MISMATCH")
        rows.append(
            dict(
                identifier=identifier,
                state=status,
                role=role,
                request_reference_sha256=pin,
                order=SimpleNamespace(client_id=order["client_id"]),
            )
        )
    require(
        len(rows) == 4
        and [r["role"] for r in rows].count("ENTRY") == 2
        and [r["role"] for r in rows].count("STOP_LOSS") == 1
        and [r["role"] for r in rows].count("EXIT") == 1
        and len({r["request_reference_sha256"] for r in rows}) == 4,
        "COPY_EVENTS_FOUR_REQUESTS_REQUIRED",
    )
    return sorted(rows, key=lambda r: (r["role"], r["request_reference_sha256"]))


def account_state(engine: Any) -> tuple[dict[str, Any], str]:
    with engine.connect() as c, c.begin():
        c.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        c.execute(text("SET LOCAL statement_timeout='2s'"))
        require(
            c.execute(text("SHOW transaction_read_only")).scalar_one() == "on",
            "COPY_EVENTS_READ_ONLY_REQUIRED",
        )
        size = c.execute(
            text("SELECT octet_length(state::text) FROM demo_accounts WHERE account_id=:account"),
            dict(account=ACCOUNT),
        ).scalar_one()
        require(0 < size <= 2 * 1024**2, "COPY_EVENTS_ACCOUNT_SIZE_LIMIT")
        state = c.execute(
            select(accounts.c.state).where(accounts.c.account_id == ACCOUNT)
        ).scalar_one()
        fingerprint = c.execute(
            text(
                "SELECT encode(sha256(convert_to(state::text,'UTF8')),'hex') FROM demo_accounts WHERE account_id=:account"
            ),
            dict(account=ACCOUNT),
        ).scalar_one()
        require(
            isinstance(fingerprint, str) and re.fullmatch("[0-9a-f]{64}", fingerprint) is not None,
            "COPY_EVENTS_ACCOUNT_FINGERPRINT_INVALID",
        )
    return state, fingerprint


def review_copy_request_events(
    engine: Any, expected_plan: Any, *, copy_only_review: bool = False
) -> dict[str, Any]:
    require(copy_only_review is True, "COPY_EVENTS_EXPLICIT_COPY_REVIEW_REQUIRED")
    state, before = account_state(engine)
    plan = plan_from_state(state, expected_plan)
    projected = projected_timelines(engine, ACCOUNT, plan, SimpleNamespace(require=require))
    _, after = account_state(engine)
    require(before == after, "COPY_EVENTS_ACCOUNT_CHANGED")
    rows = []
    for item in plan:
        timeline = projected["request_local_state_timelines"][item["request_reference_sha256"]]
        reason_rows = [r for r in timeline if r["local_fields"].get("no_send_evidence") is not None]
        rows.append(
            dict(
                role=item["role"],
                request_reference_sha256=item["request_reference_sha256"],
                backup_request_state=item["state"],
                stored_transition_count=len(timeline),
                first_by_stored_time_request_state=timeline[0]["local_fields"]["request_state"]
                if timeline
                else None,
                last_by_stored_time_request_state=timeline[-1]["local_fields"]["request_state"]
                if timeline
                else None,
                stored_no_send_reason_transition_count=len(reason_rows),
                not_sent_with_reason_observed=any(
                    r["local_fields"]["request_state"] == "NOT_SENT" for r in reason_rows
                ),
                remote_resolution_verified=False,
                automatically_resolvable=False,
            )
        )
    summary = dict(
        requests=rows,
        events_scanned=projected["events_scanned"],
        source_event_bytes=projected["source_event_bytes"],
        projected_bytes_transferred=projected["projected_bytes_transferred"],
        non_ledger_payloads_skipped=projected["non_ledger_payloads_skipped"],
        query_strategy=projected["query_strategy"],
        source_row_coverage_verified=projected["source_row_coverage_verified"],
        timeline_order="STORED_AS_OF_THEN_EVENT_ID_NOT_WIRE_EXECUTION_ORDER",
        full_timelines_in_worker_result=True,
        remote_resolution_verified=False,
        request_resolution_authorized=False,
        resend_authorized=False,
    )
    result = dict(
        copy_event_review="LOCAL_EVENT_TIMELINES_READ_KEEP_STOPPED",
        account_state_fingerprint_sha256=before,
        historical_unresolved_requests=4,
        stored_day_kst=state["daily_key"],
        stored_week_kst=state["weekly_key"],
        summary=summary,
        projection=projected,
        account_read_session_read_only=True,
        backup_account_fingerprint_unchanged_observed=True,
        current_original_database_checked=False,
        event_transition_projection_read=True,
        full_event_content_validated=False,
        strict_execution_order_verified=False,
        wire_submission_or_non_submission_verified=False,
        local_no_send_reason_independently_verified=False,
        request_resolution_authorized=False,
        resend_authorized=False,
        request_states_changed=False,
        financial_ledger_changed=False,
        period_baselines_changed=False,
        clock_rebased=False,
        shared_journal_changed=False,
        resource_checkpoint_changed=False,
        automatic_retry=False,
        exchange_http_attempts=0,
        order_writes=0,
        installation_ready=False,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
    )
    require(set(result) == set(REPORT_KEYS), "COPY_EVENTS_REPORT_SHAPE_INVALID")
    return result
