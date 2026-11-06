"""Real PostgreSQL read-only status; no actual exchange, SMTP or dispatcher."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, insert, select, update
from sqlalchemy.exc import SQLAlchemyError

import apps.api.main as api
from packages.persistence.demo import accounts, events, outbox
from packages.persistence.demo_alerts import alerts, attempts
from services.operations.demo_alert_status import read_queue
from tests.integration.test_demo import system as demo_system  # noqa: F401

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)


def seed(system):
    store, coordinator, fake = system
    coordinator.command(uuid4(), "EMERGENCY_FLATTEN")
    with store.engine.begin() as conn:
        row = dict(conn.execute(select(alerts)).mappings().one())
        conn.execute(
            update(alerts).values(
                created_at=NOW - timedelta(minutes=85), delivered_at=NOW, attempts=1
            )
        )
        row.update(
            alert_id=str(uuid4()),
            code="DELIVERY_PIPELINE_TEST",
            created_at=NOW - timedelta(minutes=10),
            delivered_at=None,
        )
        conn.execute(insert(alerts).values(row))
    return store, fake


def snapshot(store):
    with store.engine.connect() as conn:
        return [
            conn.execute(select(t).order_by(*t.primary_key.columns)).mappings().all()
            for t in (accounts, events, outbox, alerts, attempts)
        ]


def test_counts_delay_readonly_snapshot_and_no_writes(demo_system):  # noqa: F811
    store, fake = seed(demo_system)
    before, calls = snapshot(store), list(fake.calls)
    statements = []

    def capture(conn, cursor, statement, parameters, context, many):
        statements.append(statement)

    event.listen(store.engine, "before_cursor_execute", capture)
    try:
        at, queue, recent = read_queue(store.engine, "DEMO-test", now=NOW)
    finally:
        event.remove(store.engine, "before_cursor_execute", capture)
    assert at == NOW
    assert queue.total == 2 and queue.pending == 1 and queue.accepted == 1
    assert queue.critical_accepted == 1 and queue.critical_pending == 0
    assert queue.test_pending == 1 and queue.test_accepted == 0
    assert queue.oldest_pending_age_seconds == 600 and queue.timing_consistent
    assert recent.code == "EMERGENCY_ENTERED" and recent.delivery_delay_seconds == 5100
    assert recent.timing_consistent and recent.attempts == 1
    assert statements[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    assert len(statements) == 4 and all(s.startswith(("SET ", "SELECT ")) for s in statements)
    assert snapshot(store) == before and fake.calls == calls
    serialized = json.dumps(queue.model_dump(mode="json")) + recent.model_dump_json()
    assert "payload" not in serialized and "account_id" not in serialized


def test_future_or_backwards_times_are_unknown_not_zero(demo_system):  # noqa: F811
    store, _ = seed(demo_system)
    with store.engine.begin() as conn:
        conn.execute(
            update(alerts).values(
                created_at=NOW + timedelta(hours=1), last_failure="fixture-secret"
            )
        )
    _, queue, recent = read_queue(store.engine, "DEMO-test", now=NOW)
    assert queue.oldest_pending_age_seconds is None and not queue.timing_consistent
    assert recent.delivery_delay_seconds is None and not recent.timing_consistent
    assert recent.last_failure == "UNKNOWN"


def test_api_fixed_account_no_secret_or_control_route_and_no_cache(demo_system, monkeypatch):  # noqa: F811
    store, _ = seed(demo_system)
    monkeypatch.setattr(api, "ACCOUNT", "DEMO-test")
    monkeypatch.setattr(api, "create_engine", lambda *a, **k: store.engine)
    monkeypatch.setattr(
        api,
        "public_email_status",
        lambda: {"state": "NOT_CONFIGURED", "expires_unix": None, "remaining_seconds": None},
    )
    before = snapshot(store)
    with TestClient(api.create_app()) as client:
        response = client.get("/api/v1/demo/alerts/status?account_id=DEMO-other")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["queue"]["total"] == 2
        assert body["orders_enabled"] is False and body["live_trading_enabled"] is False
        assert (
            body["worker_liveness_checked"] is False
            and body["inbox_confirmation_available"] is False
        )
        assert body["confirmation_scope"] == "RECEIVER_ACK_ONLY"
        assert client.post("/api/v1/demo/alerts/status").status_code == 405
        for key in ("password", "address", "account_id", "payload", "event_id", "alert_id"):
            assert key not in response.text
    assert snapshot(store) == before


@pytest.mark.parametrize(
    "failure",
    [SQLAlchemyError("fixture-secret"), ValueError("fixture-secret"), OSError("fixture-secret")],
)
def test_api_failure_is_unknown_not_empty_and_never_raw_failure(monkeypatch, failure):
    def fail():
        raise failure

    monkeypatch.setattr(api, "read_alert_status", fail)
    with TestClient(api.create_app()) as client:
        response = client.get("/api/v1/demo/alerts/status")
        assert response.status_code == 503
        body = response.json()
        assert body["is_stale"] and body["queue"] is None
        assert body["email"]["state"] == "UNKNOWN" and body["latest_critical"] is None
        assert "fixture-secret" not in response.text


def test_empty_existing_queue_is_zero_without_inventing_last_delivery(demo_system):  # noqa: F811
    store, _, _ = demo_system
    _, queue, recent = read_queue(store.engine, "DEMO-test", now=NOW)
    assert queue.total == queue.pending == queue.accepted == 0
    assert queue.oldest_pending_at is None and queue.oldest_pending_age_seconds is None
    assert queue.timing_consistent and recent is None
