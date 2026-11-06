import json
from uuid import uuid4

import inspect_protected_ready as reader
import pytest
from sqlalchemy import event, select

from packages.persistence.demo import accounts, events, outbox
from packages.persistence.demo_alerts import alerts, attempts
from services.execution.demo_models import PrivateSessionDiagnostic
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_paper import signal, snapshot


@pytest.fixture
def closed(demo_system):  # noqa: F811
    store, c, fake = demo_system
    sig = signal(environment="DEMO")
    op = sig.signal_id

    def start(state):
        state.operations[reader.marker(op, "start")] = "STARTED"
        state.operations["private-fixture"] = "SYNTHETIC_PRIVATE_NEVER_OUTPUT"

    store.change(c.lease, "DEMO_PROTECTED_WORKER_START", start)
    order = c.reserve(sig, snapshot())
    c.submit(str(op))
    fake.fill(order.client_id, order.quantity)
    c.reconcile()

    def fail(state):
        state.last_private_session_failure = PrivateSessionDiagnostic(
            stage="READY_CALLBACK",
            code="SNAPSHOT_CHANGED",
            ready_callback_ms=20000,
            received_events=2,
        )

    store.change(c.lease, "DEMO_PRIVATE_SESSION_FAILED", fail)
    c.command(uuid4(), "EMERGENCY_FLATTEN")
    assert c.confirm_flat() == "VERIFIED_FLAT"
    store.change(
        c.lease,
        "DEMO_PROTECTED_WORKER_FINALLY",
        lambda s: s.operations.__setitem__(reader.marker(op, "finally"), "FINALLY_RAN"),
    )
    store.release(c.lease)
    return store, fake, op


def db_snapshot(store):
    with store.engine.connect() as conn:
        return [
            conn.execute(select(t).order_by(*t.primary_key.columns)).mappings().all()
            for t in [accounts, events, outbox, alerts, attempts]
        ]


def test_readonly_scoped_timeline_and_no_exchange_or_writes(closed):
    store, fake, op = closed
    before, calls = db_snapshot(store), list(fake.calls)
    sql = []

    def capture(conn, cursor, statement, parameters, context, many):
        sql.append(statement)

    event.listen(store.engine, "before_cursor_execute", capture)
    try:
        result = reader.inspect(store.engine, "DEMO-test", op)
    finally:
        event.remove(store.engine, "before_cursor_execute", capture)
    assert result["protected_ready_diagnostic"] == "READ_ONLY_LOCAL_SCOPED_EVIDENCE_KEEP_PAUSED"
    assert (
        result["leader_released"]
        and result["finally_marker_recorded"]
        and not result["ready_marker_recorded"]
    )
    assert result["current_local_state"]["order"]["filled"] != "0"
    assert result["current_local_state"]["order"]["exit_state"] == "FILLED"
    assert result["current_local_state"]["order"]["request_states"]["STOP"] == "VERIFIED"
    assert result["current_local_state"]["order"]["request_states"]["TARGET"] == "VERIFIED"
    assert any(
        r["session_failure"] and r["session_failure"]["code"] == "SNAPSHOT_CHANGED"
        for r in result["timeline"]
    )
    assert any(r["kind"] == "DEMO_ENTRY_EFFECT" for r in result["timeline"])
    assert sql[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    assert len(sql) == 6 and all(s.startswith(("SET ", "SELECT ")) for s in sql)
    assert db_snapshot(store) == before and fake.calls == calls
    text = json.dumps(result)
    assert "SYNTHETIC_PRIVATE_NEVER_OUTPUT" not in text
    assert "payload" not in text and "client_id" not in text and "review_token" not in text
    assert (
        not result["exchange_checked"]
        and not result["unlock_applied"]
        and not result["database_changed_by_diagnostic"]
    )


def test_other_operation_cannot_be_misreported_as_this_operation(closed):
    store, _, _ = closed
    before = db_snapshot(store)
    with pytest.raises(ValueError, match="SCOPED_START_NOT_FOUND"):
        reader.inspect(store.engine, "DEMO-test", uuid4())
    assert db_snapshot(store) == before


def test_projection_drops_raw_private_values_and_invalid_numeric_data():
    op = uuid4()
    state = {
        "risk_state": "PRIVATE_SECRET",
        "paused": True,
        "last_reconciliation_mismatch_fields": ["orders", "PRIVATE_SECRET"],
        "last_private_session_failure": {
            "stage": "PRIVATE_SECRET",
            "code": "PRIVATE_SECRET",
            "url": "PRIVATE_SECRET",
        },
        "requests": {"entry": "PRIVATE_SECRET"},
        "orders": {
            str(op): {
                "client_id": "entry",
                "state": "PRIVATE_SECRET",
                "position_state": "PRIVATE_SECRET",
                "quantity": "NaN",
                "filled": "PRIVATE_SECRET",
                "remaining": "0",
                "exit_state": "PRIVATE_SECRET",
            }
        },
    }
    result = reader.summary(state, op)
    assert "PRIVATE_SECRET" not in json.dumps(result)
    assert result["order"]["filled"] is None and result["order"]["quantity"] is None
    assert result["mismatch_fields"] == ["orders"]
    assert result["session_failure"] == {"code": "DIAGNOSTIC_SCHEMA_UNRECOGNIZED"}


def test_no_order_is_not_an_invented_zero_fill():
    result = reader.summary({"risk_state": "EMERGENCY", "paused": True}, uuid4())
    assert result["order_present"] is False and "order" not in result


@pytest.mark.parametrize("failure", [ValueError("PRIVATE_SECRET"), OSError("PRIVATE_SECRET")])
def test_cli_failure_never_prints_raw_details(monkeypatch, capsys, failure):
    def fail(*a, **k):
        raise failure

    monkeypatch.setattr(reader, "create_engine", fail)
    with pytest.raises(SystemExit) as error:
        reader.main()
    assert error.value.code == 2 and "PRIVATE_SECRET" not in capsys.readouterr().out


def test_live_context_blocks_before_db(monkeypatch, capsys):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setattr(reader, "create_engine", lambda *a, **k: pytest.fail("no database"))
    with pytest.raises(SystemExit):
        reader.main()
    assert "UNAVAILABLE_KEEP_PAUSED" in capsys.readouterr().out


def test_later_owner_failure_is_not_attributed_to_the_failed_child(closed):
    from datetime import timedelta

    from sqlalchemy import insert

    store, _, op = closed
    with store.engine.begin() as conn:
        row = conn.execute(
            select(accounts.c.state).where(accounts.c.account_id == "DEMO-test")
        ).scalar_one()
        conn.execute(
            insert(events).values(
                event_id=str(uuid4()),
                account_id="DEMO-test",
                fence=999,
                as_of=store.now() + timedelta(seconds=1),
                kind="DEMO_PRIVATE_SESSION_FAILED",
                payload=json.dumps(row),
            )
        )
    result = reader.inspect(store.engine, "DEMO-test", op)
    assert all(e["fence"] != 999 for e in result["timeline"])
    assert len(result["timeline_fence_scope"]) == 2
