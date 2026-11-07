# ruff: noqa: F811
"""Real PG -> actual worker loop -> local verified TLS SMTP; financial rows unchanged."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import update

from packages.persistence.demo import accounts, events, outbox
from packages.persistence.demo_alerts import alerts, attempts
from scripts import check_demo_alert_pipeline as cli
from scripts import watch_demo_alert_email as worker
from scripts.demo_alert_email_session import EmailSettings
from services.operations.demo_alert_email import NaverEmail
from services.operations.demo_alert_pipeline import enqueue_test, review_test
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_urgent_alerts import case as alert_case  # noqa: F401
from tests.integration.test_demo_urgent_alerts import rows
from tests.unit.test_demo_alert_email_wire import smtp_server  # noqa: F401


@pytest.fixture
def case(alert_case):  # noqa: F811
    store, c, fake = alert_case
    store.change(c.lease, "TEST_PREPARE_PAUSED", lambda ledger: setattr(ledger, "paused", True))
    return store, c, fake


def finance(store):
    return {table.name: rows(store, table) for table in [accounts, events, outbox]}


def test_queue_test_actual_worker_tls_smtp_preserves_all_financial_rows(
    case, smtp_server, monkeypatch, tmp_path, capsys
):  # noqa: F811
    store, c, fake = case
    before = finance(store)
    calls = len(fake.calls)
    operation = uuid4()
    assert enqueue_test(store.engine, "DEMO-test", operation)
    queued = rows(store, alerts)[0]
    assert queued["payload"]["severity"] == "TEST" and queued["code"] == "DELIVERY_PIPELINE_TEST"
    assert queued["payload"]["order_ids"] == []
    assert finance(store) == before and len(fake.calls) == calls
    assert (
        review_test(store.engine, "DEMO-test", operation)["pipeline_check"]
        == "QUEUED_WAITING_FOR_WORKER"
    )

    class StopLoop(Exception):
        pass

    def stop(*a):
        raise StopLoop

    settings = EmailSettings(smtp_server.address, smtp_server.password)
    monkeypatch.setattr(worker, "ACCOUNT", "DEMO-test")
    monkeypatch.setattr(worker, "create_engine", lambda *a, **kw: store.engine)
    monkeypatch.setattr(worker, "load", lambda: settings)
    monkeypatch.setattr(
        worker, "NaverEmail", lambda value: NaverEmail(value, factory=smtp_server.factory)
    )
    monkeypatch.setattr(worker, "Path", lambda *a: tmp_path / "health")
    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=time.monotonic, sleep=stop))
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(worker.main)
        with pytest.raises(StopLoop):
            future.result(timeout=10)
    report = review_test(store.engine, "DEMO-test", operation)
    assert report["pipeline_check"] == "QUEUE_DELIVERY_ACKNOWLEDGED"
    assert report["attempt_count"] == 1 and report["attempt_results"] == ["ACKNOWLEDGED"]
    assert report["inbox_confirmation"] == "REQUIRES_USER_CONFIRMATION"
    assert finance(store) == before and len(fake.calls) == calls
    assert len(smtp_server.messages) == 1
    message = smtp_server.messages[0]
    assert message["Message-ID"] == f"<demo-alert-{operation}@naver.com>"
    assert message["From"] == message["To"] == smtp_server.address
    assert "TEST" in message.get_payload(decode=True).decode()
    log = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ][-1]
    assert log["component"] == "DEMO_EMAIL_WORKER" and log["channel"] == "NAVER_SMTP"
    assert log["alert_id"] == str(operation) and log["alert_delivery"] == "DELIVERED"
    saved = rows(store, alerts), rows(store, attempts)
    assert not enqueue_test(store.engine, "DEMO-test", operation)
    assert (rows(store, alerts), rows(store, attempts)) == saved
    assert finance(store) == before


def test_concurrent_duplicate_enqueue_creates_only_one_test_without_audit_rows(case):
    store, _, _ = case
    before = finance(store)
    operation = uuid4()
    with ThreadPoolExecutor(2) as pool:
        result = list(
            pool.map(lambda _: enqueue_test(store.engine, "DEMO-test", operation), range(2))
        )
    assert sorted(result) == [False, True]
    assert len(rows(store, alerts)) == 1 and not rows(store, attempts)
    assert finance(store) == before


def test_new_operation_cannot_spam_same_source_event(case):
    store, _, _ = case
    assert enqueue_test(store.engine, "DEMO-test", uuid4())
    with pytest.raises(ValueError, match="SOURCE_ALREADY_HAS"):
        enqueue_test(store.engine, "DEMO-test", uuid4())
    assert len(rows(store, alerts)) == 1


def test_review_never_claims_or_sends_and_does_not_require_email_config(case, monkeypatch, capsys):
    store, _, _ = case
    operation = uuid4()
    enqueue_test(store.engine, "DEMO-test", operation)
    before = finance(store), rows(store, alerts), rows(store, attempts)
    monkeypatch.setattr(cli, "ACCOUNT", "DEMO-test")
    monkeypatch.setattr(cli, "create_engine", lambda *a, **kw: store.engine)
    monkeypatch.setattr(
        cli, "email_status", lambda: pytest.fail("review must not require email config")
    )
    monkeypatch.setattr("sys.argv", ["review", "--review", "--operation-id", str(operation)])
    with pytest.raises(SystemExit):
        cli.main()
    assert json.loads(capsys.readouterr().out)["pipeline_check"] == "QUEUED_WAITING_FOR_WORKER"
    assert (finance(store), rows(store, alerts), rows(store, attempts)) == before


def test_cli_enqueue_only_writes_test_queue_and_wait_timeout_never_sends(case, monkeypatch, capsys):
    store, _, fake = case
    before, calls = finance(store), len(fake.calls)
    operation = uuid4()
    monkeypatch.setattr(cli, "ACCOUNT", "DEMO-test")
    monkeypatch.setattr(cli, "create_engine", lambda *a, **kw: store.engine)
    monkeypatch.setattr(cli, "email_status", lambda: "CONFIGURED_PRIVATE_RAM")
    monkeypatch.setattr(
        "sys.argv",
        ["enqueue", "--enqueue-self-test", "--operation-id", str(operation), "--wait-seconds", "0"],
    )
    with pytest.raises(SystemExit):
        cli.main()
    report = json.loads(capsys.readouterr().out)
    assert report["newly_registered"] and report["pipeline_check"] == "QUEUED_WAITING_FOR_WORKER"
    assert len(rows(store, alerts)) == 1 and not rows(store, attempts)
    assert finance(store) == before and len(fake.calls) == calls


@pytest.mark.parametrize("fault", ["unpaused", "position", "source_missing"])
def test_bad_context_does_not_create_test_or_change_finance(case, fault):
    from sqlalchemy import delete

    store, c, _ = case
    if fault == "unpaused":
        store.change(c.lease, "TEST_UNPAUSED", lambda ledger: setattr(ledger, "paused", False))
    elif fault == "position":
        from decimal import Decimal

        store.change(
            c.lease,
            "TEST_POSITION",
            lambda ledger: ledger.positions.__setitem__("BTCUSDT", Decimal("0.0007")),
        )
    else:
        with store.engine.begin() as conn:
            conn.execute(delete(outbox))
            conn.execute(delete(events))
    before = finance(store)
    with pytest.raises(ValueError):
        enqueue_test(store.engine, "DEMO-test", uuid4())
    assert not rows(store, alerts) and finance(store) == before


def test_operation_reuse_across_accounts_is_rejected(case):
    store, _, _ = case
    operation = uuid4()
    enqueue_test(store.engine, "DEMO-test", operation)
    store.create("DEMO-other", store.read("DEMO-test"))
    with pytest.raises(ValueError, match="OPERATION_SCOPE_CONFLICT"):
        enqueue_test(store.engine, "DEMO-other", operation)
    assert len(rows(store, alerts)) == 1


def test_forged_delivered_timestamp_without_attempt_ack_is_not_success(case):
    store, _, _ = case
    operation = uuid4()
    enqueue_test(store.engine, "DEMO-test", operation)
    with store.engine.begin() as conn:
        conn.execute(
            update(alerts)
            .where(alerts.c.alert_id == str(operation))
            .values(delivered_at=store.now())
        )
    assert (
        review_test(store.engine, "DEMO-test", operation)["pipeline_check"]
        == "INCONSISTENT_DELIVERY_RECORD"
    )


def test_wrong_source_payload_is_not_accepted(case):
    store, _, _ = case
    operation = uuid4()
    enqueue_test(store.engine, "DEMO-test", operation)
    row = rows(store, alerts)[0]
    with store.engine.begin() as conn:
        conn.execute(
            update(alerts)
            .where(alerts.c.alert_id == str(operation))
            .values(payload={**row["payload"], "event_id": str(uuid4())})
        )
    with pytest.raises(ValueError, match="SOURCE_IDENTITY_INVALID"):
        review_test(store.engine, "DEMO-test", operation)
