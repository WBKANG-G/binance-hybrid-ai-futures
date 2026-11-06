"""Real PG and loopback HTTP ACKs; never sends to a person or actual exchange."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, event, select, text, update
from sqlalchemy.exc import DBAPIError

from packages.persistence.demo import accounts, events, outbox
from packages.persistence.demo_alerts import AlertEnvelope, alerts, attempts
from packages.persistence.market import metadata
from services.operations.demo_alert_delivery import Dispatcher, LocalWebhook, summary
from tests.integration.test_demo import enter, posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_isolated import configure
from tests.unit.test_demo_alert_email_wire import smtp_server  # noqa: F401


@pytest.fixture
def case(demo_system):  # noqa: F811
    configured, _ = configure(demo_system)
    return configured


def pending(case):
    store, c, _ = case
    c.command(uuid4(), "EMERGENCY_FLATTEN")
    with store.engine.connect() as conn:
        rows = conn.execute(select(alerts)).mappings().all()
    assert len(rows) == 1
    return rows[0]


def rows(store, table):
    with store.engine.connect() as conn:
        return (
            conn.execute(select(table).order_by(table.c[list(table.primary_key.columns)[0].name]))
            .mappings()
            .all()
        )


@contextmanager
def receiver(mode="ack", on_receive=None):
    state = {"messages": [], "keys": [], "unique": set()}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["messages"].append(body)
            state["keys"].append(self.headers.get("Idempotency-Key"))
            state["unique"].add(body["alert_id"])
            if on_receive:
                on_receive()
            status = 503 if mode == "fail_first" and len(state["messages"]) == 1 else 200
            if mode == "timeout_first" and len(state["messages"]) == 1:
                time.sleep(2.2)  # receiver accepted; transport ACK is lost
            payload = {"accepted": True, "alert_id": body["alert_id"]}
            if mode == "wrong_ack":
                payload["alert_id"] = str(uuid4())
            raw = json.dumps(payload).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield LocalWebhook(f"http://127.0.0.1:{server.server_port}/alerts"), state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_real_core_stop_failure_and_unknown_exit_enqueue_once_without_leaking_ledger(case):
    store, c, fake = case
    store.change(
        c.lease,
        "TEST_SECRET_AUDIT",
        lambda ledger: ledger.operations.__setitem__("SYNTHETIC_SECRET", "DO_NOT_SEND_THIS_LEDGER"),
    )
    sig, order = enter(case)
    fake.fill(order.client_id, order.quantity)
    fake.reject_stop = True
    fake.defer_exit = True
    fake.lost_submit = True
    c.reconcile()
    queued = rows(store, alerts)
    assert {r["code"] for r in queued} >= {
        "STOP_PROTECTION_FAILURE",
        "EMERGENCY_ENTERED",
        "EXIT_OUTCOME_UNKNOWN",
    }
    with store.engine.connect() as conn:
        assert all(
            conn.execute(
                select(events.c.event_id).where(events.c.event_id == r["event_id"])
            ).first()
            for r in queued
        )
    encoded = json.dumps([r["payload"] for r in queued])
    assert "SYNTHETIC_SECRET" not in encoded and "DO_NOT_SEND" not in encoded
    assert "operations" not in encoded and "policy" not in encoded and "wallet" not in encoded
    assert all(
        AlertEnvelope.model_validate_json(json.dumps(r["payload"])).environment == "DEMO"
        for r in queued
    )
    c.reconcile()
    store.renew(c.lease)
    assert len(rows(store, alerts)) == len(queued)  # no repeat of old UNKNOWN or heartbeat
    assert len([p for p in posts(fake) if p.get("reduceOnly") == "true"]) == 1


def test_alert_insert_db_failure_rolls_back_account_audit_outbox_together(case):
    store, c, _ = case
    before = store.read("DEMO-test")
    snapshots = {t.name: rows(store, t) for t in [events, outbox, alerts]}
    admin = create_engine(store.engine.url)
    killed = []

    def cut(conn, cursor, statement, parameters, context, executemany):
        if not killed and "INSERT INTO demo_urgent_alerts" in statement:
            pid = conn.connection.driver_connection.info.backend_pid
            with admin.connect() as control:
                assert control.scalar(text("SELECT pg_terminate_backend(:pid, 2000)"), {"pid": pid})
            killed.append(pid)

    event.listen(store.engine, "before_cursor_execute", cut)
    try:
        with pytest.raises(DBAPIError):
            c.command(uuid4(), "EMERGENCY_FLATTEN")
    finally:
        event.remove(store.engine, "before_cursor_execute", cut)
        admin.dispose()
    assert killed and store.read("DEMO-test") == before
    for t in [events, outbox, alerts]:
        assert rows(store, t) == snapshots[t.name]


def test_local_receiver_ack_and_no_trading_row_lock_during_network(case):
    store, c, fake = case
    alert = pending(case)
    before = len(fake.calls)
    operation = str(uuid4())
    with receiver(
        on_receive=lambda: store.change(
            c.lease,
            "TEST_DURING_DELIVERY",
            lambda ledger: ledger.operations.__setitem__(operation, "NETWORK_OUTSIDE_ACCOUNT_LOCK"),
        )
    ) as (sink, received):
        result = Dispatcher(store.engine, store.now).deliver_one("DEMO-test", sink)
    assert result["alert_delivery"] == "DELIVERED"
    assert received["keys"] == [alert["alert_id"]]
    assert received["messages"][0]["alert_id"] == alert["alert_id"]
    assert store.read("DEMO-test").operations[operation] == "NETWORK_OUTSIDE_ACCOUNT_LOCK"
    assert len(fake.calls) == before
    assert summary(store.engine, "DEMO-test") == {
        "total": 1,
        "pending": 0,
        "delivered": 1,
        "retry_pending": 0,
        "external_receiver_configured": False,
    }


@pytest.mark.parametrize(
    "mode,expected", [("fail_first", "HTTP_REJECTED"), ("timeout_first", "TIMEOUT")]
)
def test_failed_or_lost_ack_retry_uses_same_id_and_preserves_attempt_records(case, mode, expected):
    store, _, fake = case
    alert = pending(case)
    before = store.read("DEMO-test")
    calls = len(fake.calls)
    clock = [store.now()]
    dispatcher = Dispatcher(store.engine, lambda: clock[0])
    with receiver(mode) as (sink, received):
        first = dispatcher.deliver_one("DEMO-test", sink)
        assert first["alert_delivery"] == "RETRY_PENDING" and first["result"] == expected
        assert rows(store, alerts)[0]["delivered_at"] is None
        assert dispatcher.deliver_one("DEMO-test", sink)["alert_delivery"] == "IDLE"
        clock[0] += timedelta(seconds=5)
        assert dispatcher.deliver_one("DEMO-test", sink)["alert_delivery"] == "DELIVERED"
        assert dispatcher.deliver_one("DEMO-test", sink)["alert_delivery"] == "IDLE"
    assert received["keys"] == [alert["alert_id"], alert["alert_id"]]
    assert len(received["messages"]) == 2 and len(received["unique"]) == 1
    assert [r["result"] for r in sorted(rows(store, attempts), key=lambda r: r["number"])] == [
        expected,
        "ACKNOWLEDGED",
    ]
    assert store.read("DEMO-test") == before and len(fake.calls) == calls


def test_wrong_ack_does_not_mark_delivery_complete(case):
    store, _, _ = case
    pending(case)
    with receiver("wrong_ack") as (sink, _):
        report = Dispatcher(store.engine, store.now).deliver_one("DEMO-test", sink)
    assert report["result"] == "ACK_INVALID" and report["alert_delivery"] == "RETRY_PENDING"
    assert rows(store, alerts)[0]["delivered_at"] is None


def test_expired_claim_is_recovered_and_old_worker_cannot_commit_ack(case):
    store, _, _ = case
    pending(case)
    clock = [store.now()]
    dispatcher = Dispatcher(store.engine, lambda: clock[0])
    first = dispatcher.claim("DEMO-test")
    assert (
        first is not None and Dispatcher(store.engine, lambda: clock[0]).claim("DEMO-test") is None
    )
    clock[0] += timedelta(seconds=31)  # alert claim only; trading lease is untouched
    replacement = Dispatcher(store.engine, lambda: clock[0])
    second = replacement.claim("DEMO-test")
    assert second is not None and second.token != first.token and second.alert_id == first.alert_id
    assert not dispatcher.finish(first, "ACKNOWLEDGED")
    assert rows(store, alerts)[0]["delivered_at"] is None
    assert replacement.finish(second, "ACKNOWLEDGED")
    assert [r["result"] for r in sorted(rows(store, attempts), key=lambda r: r["number"])] == [
        "CLAIM_EXPIRED",
        "ACKNOWLEDGED",
    ]


def test_competing_dispatchers_cannot_send_same_active_claim(case):
    store, _, _ = case
    pending(case)
    entered, release = threading.Event(), threading.Event()
    seen = []

    class WaitingSink:
        def send(self, envelope):
            seen.append(str(envelope.alert_id))
            entered.set()
            assert release.wait(2)

    dispatcher = Dispatcher(store.engine, store.now)
    with ThreadPoolExecutor(2) as pool:
        future = pool.submit(dispatcher.deliver_one, "DEMO-test", WaitingSink())
        assert entered.wait(1)
        assert (
            Dispatcher(store.engine, store.now).deliver_one("DEMO-test", WaitingSink())[
                "alert_delivery"
            ]
            == "IDLE"
        )
        release.set()
        assert future.result()["alert_delivery"] == "DELIVERED"
    assert len(seen) == 1 and len(rows(store, attempts)) == 1


def test_invalid_payload_never_reaches_sink_or_error_output(case):
    store, _, _ = case
    alert = pending(case)
    with store.engine.begin() as conn:
        conn.execute(
            update(alerts)
            .where(alerts.c.alert_id == alert["alert_id"])
            .values(payload={**alert["payload"], "secret": "SYNTHETIC_NEVER_SEND"})
        )

    class NeverSend:
        def send(self, envelope):
            pytest.fail("invalid payload must never be transmitted")

    result = Dispatcher(store.engine, store.now).deliver_one("DEMO-test", NeverSend())
    assert result["result"] == "PAYLOAD_INVALID" and not result["network_attempted"]
    assert "SYNTHETIC" not in json.dumps(result)


def test_additive_migration_preserves_legacy_state_audit_and_outbox(case):
    store, _, _ = case
    cfg = Config("alembic.ini")
    before = {t.name: rows(store, t) for t in [accounts, events, outbox]}
    with store.engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0003_demo")
    with store.engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    for t in [accounts, events, outbox]:
        assert rows(store, t) == before[t.name]
    with store.engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), metadata) == []
    assert not rows(store, alerts) and not rows(store, attempts)


def test_receiver_ack_then_actual_db_failure_keeps_claim_and_retries_same_id(case):
    store, _, _ = case
    alert = pending(case)
    clock = [store.now()]
    dispatcher = Dispatcher(store.engine, lambda: clock[0])
    admin = create_engine(store.engine.url)
    killed = []

    def cut(conn, cursor, statement, parameters, context, executemany):
        if (
            not killed
            and statement.startswith("UPDATE demo_urgent_alerts")
            and parameters.get("delivered_at") is not None
        ):
            with admin.connect() as control:
                assert control.scalar(
                    text("SELECT pg_terminate_backend(:pid, 2000)"),
                    {"pid": conn.connection.driver_connection.info.backend_pid},
                )
            killed.append(True)

    with receiver() as (sink, received):
        event.listen(store.engine, "before_cursor_execute", cut)
        try:
            with pytest.raises(DBAPIError):
                dispatcher.deliver_one("DEMO-test", sink)
        finally:
            event.remove(store.engine, "before_cursor_execute", cut)
            admin.dispose()
        row = rows(store, alerts)[0]
        assert killed and row["delivered_at"] is None and row["claim_token"] is not None
        assert rows(store, attempts)[0]["result"] == "SENDING"
        clock[0] += timedelta(seconds=31)
        assert (
            Dispatcher(store.engine, lambda: clock[0]).deliver_one("DEMO-test", sink)[
                "alert_delivery"
            ]
            == "DELIVERED"
        )
    assert received["keys"] == [alert["alert_id"], alert["alert_id"]]
    assert len(received["unique"]) == 1
    assert [r["result"] for r in sorted(rows(store, attempts), key=lambda r: r["number"])] == [
        "CLAIM_EXPIRED",
        "ACKNOWLEDGED",
    ]


def test_unexpected_sink_error_never_escapes_or_drops_alert(case):
    store, _, _ = case
    pending(case)

    class Broken:
        def send(self, envelope):
            raise RuntimeError("SYNTHETIC_SECRET_ERROR_DO_NOT_FORWARD")

    report = Dispatcher(store.engine, store.now).deliver_one("DEMO-test", Broken())
    assert report["result"] == "SINK_FAILED" and "SECRET" not in json.dumps(report)
    assert rows(store, alerts)[0]["delivered_at"] is None
    assert rows(store, attempts)[0]["result"] == "SINK_FAILED"


def test_retries_back_off_to_cap_without_deleting_pending_alert(case):
    from services.operations.demo_alert_delivery import DeliveryFailure

    store, _, _ = case
    pending(case)
    clock = [store.now()]
    dispatcher = Dispatcher(store.engine, lambda: clock[0])

    class Down:
        def send(self, envelope):
            raise DeliveryFailure("HTTP_REJECTED")

    for number in range(1, 10):
        assert dispatcher.deliver_one("DEMO-test", Down())["alert_delivery"] == "RETRY_PENDING"
        row = rows(store, alerts)[0]
        assert (row["next_attempt_at"] - clock[0]).total_seconds() == min(
            300, 5 * 2 ** (number - 1)
        )
        assert row["attempts"] == number and row["delivered_at"] is None
        clock[0] = row["next_attempt_at"]
    assert len(rows(store, attempts)) == 9 and len(rows(store, alerts)) == 1


def test_durable_queue_to_real_local_tls_smtp_and_auth_retry(case, smtp_server):  # noqa: F811
    from scripts.demo_alert_email_session import EmailSettings
    from services.operations.demo_alert_email import NaverEmail

    store, _, _ = case
    alert = pending(case)
    clock = [store.now()]
    dispatcher = Dispatcher(store.engine, lambda: clock[0])
    state = smtp_server
    sink = NaverEmail(EmailSettings(state.address, state.password), factory=state.factory)
    state.accept_auth = False
    first = dispatcher.deliver_one("DEMO-test", sink)
    assert first["result"] == "SMTP_AUTH_FAILED" and rows(store, alerts)[0]["delivered_at"] is None
    assert not state.messages
    clock[0] += timedelta(seconds=5)
    state.accept_auth = True
    assert dispatcher.deliver_one("DEMO-test", sink)["alert_delivery"] == "DELIVERED"
    assert len(state.messages) == 1
    assert state.messages[0]["Message-ID"] == "<demo-alert-" + alert["alert_id"] + "@naver.com>"
    assert state.messages[0]["From"] == state.messages[0]["To"] == state.address
    assert [r["result"] for r in sorted(rows(store, attempts), key=lambda r: r["number"])] == [
        "SMTP_AUTH_FAILED",
        "ACKNOWLEDGED",
    ]
