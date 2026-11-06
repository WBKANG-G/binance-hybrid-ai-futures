"""A host-side helper sees only fresh ready evidence under the active fence."""

# ruff: noqa: F811 -- imported pytest fixtures share test argument names

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, select

import scripts.wait_demo_restart_ready as waiter
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_open_api_restart import exercise, restart_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_validation import case as validation_case  # noqa: F401


def test_host_handoff_is_fresh_read_only_and_bound_to_current_operation(restart_case, monkeypatch):
    store, c, _, _ = restart_case
    monkeypatch.setattr(waiter, "ACCOUNT", "DEMO-test")
    operation = uuid4()
    old, new = str(uuid4()), str(uuid4())
    calls = 0
    observed = []

    def reader():
        nonlocal calls
        calls += 1
        if calls == 1:
            return old
        before = store.read("DEMO-test")
        code, details = waiter.inspect_ready(store.engine, operation, store.now())
        assert code == "READY" and details["operation_id"] == str(operation)
        assert details["guard_fence"] == c.lease.fence
        assert store.read("DEMO-test") == before
        assert waiter.inspect_ready(store.engine, uuid4(), store.now())[0] == "WAITING_FOR_ORDER"
        assert (
            waiter.inspect_ready(store.engine, operation, store.now() + timedelta(seconds=6))[0]
            == "READY_EXPIRED_OR_FUTURE"
        )
        assert (
            waiter.inspect_ready(store.engine, operation, store.now() - timedelta(seconds=1))[0]
            == "READY_EXPIRED_OR_FUTURE"
        )
        observed.append(details)
        return new

    report = asyncio.run(exercise(restart_case, operation, reader))
    assert report["restart_check"] == "OPEN_ORDER_API_RESTART_VERIFIED", report
    assert len(observed) == 1
    assert waiter.inspect_ready(store.engine, operation, store.now())[0] == "ENTRY_NOT_OPEN"


def test_waiter_cli_needs_no_api_key_and_exits_zero_only_when_ready(
    restart_case, monkeypatch, capsys
):
    store, c, _, _ = restart_case
    monkeypatch.setattr(waiter, "ACCOUNT", "DEMO-test")
    monkeypatch.setattr(waiter, "create_engine", lambda *a, **kw: store.engine)
    monkeypatch.setattr(waiter, "utc_now", store.now)
    operation = uuid4()
    old, new = str(uuid4()), str(uuid4())
    calls = 0
    statements = []

    def collect(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    def reader():
        nonlocal calls
        calls += 1
        if calls == 1:
            return old
        monkeypatch.setattr(
            "sys.argv", ["wait_demo_restart_ready", "--operation-id", str(operation)]
        )
        event.listen(store.engine, "before_cursor_execute", collect)
        try:
            waiter.main()
        finally:
            event.remove(store.engine, "before_cursor_execute", collect)
        return new

    report = asyncio.run(exercise(restart_case, operation, reader))
    assert report["restart_check"] == "OPEN_ORDER_API_RESTART_VERIFIED"
    output = capsys.readouterr().out
    handoff = [json.loads(line) for line in output.splitlines() if "restart_handoff" in line][0]
    assert handoff["restart_handoff"] == "READY" and handoff["database_changed"] is False
    assert statements[0] == "SET TRANSACTION READ ONLY"
    assert not any(
        s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in statements
    )


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("lease", "GUARD_NOT_ACTIVE"),
        ("risk", "GUARD_NOT_READY"),
        ("paused", "GUARD_NOT_READY"),
        ("cap", "INVALID_ORDER"),
        ("filled", "ENTRY_FILLED"),
        ("expiry", "ENTRY_TOO_CLOSE_TO_EXPIRY"),
        ("tag", "WAITING_FOR_READY"),
        ("fence", "WAITING_FOR_READY"),
    ],
)
def test_waiter_never_signals_restart_with_stale_or_invalid_guard_state(
    restart_case, monkeypatch, mutation, expected
):
    store, c, _, _ = restart_case
    monkeypatch.setattr(waiter, "ACCOUNT", "DEMO-test")
    operation = uuid4()
    old, new = str(uuid4()), str(uuid4())
    calls = 0

    def reader():
        nonlocal calls
        calls += 1
        if calls == 1:
            return old
        now = store.now()
        # Inspect controlled snapshots through a real read-only connection.
        # Restore only this synthetic test mutation before normal cleanup.
        from sqlalchemy import update

        from packages.persistence.demo import accounts

        with store.engine.begin() as conn:
            original = (
                conn.execute(select(accounts).where(accounts.c.account_id == "DEMO-test"))
                .mappings()
                .one()
            )
            state = json.loads(json.dumps(original["state"]))
            values = {}
            if mutation == "lease":
                values["lease_until"] = now - timedelta(seconds=1)
            if mutation == "risk":
                state["risk_state"] = "EMERGENCY"
            if mutation == "paused":
                state["paused"] = True
            if mutation == "cap":
                state["entry_quantity_caps"][str(operation)] = "999"
            if mutation == "filled":
                state["orders"][str(operation)]["filled"] = "0.001"
            if mutation == "expiry":
                state["orders"][str(operation)]["signal"]["expires_at"] = now.isoformat()
            if mutation == "tag":
                state["operations"].clear()
            if mutation == "fence":
                values["fence"] = original["fence"] + 1
            values["state"] = state
            conn.execute(
                update(accounts).where(accounts.c.account_id == "DEMO-test").values(**values)
            )
        try:
            assert waiter.inspect_ready(store.engine, operation, now)[0] == expected
        finally:
            with store.engine.begin() as conn:
                conn.execute(
                    update(accounts)
                    .where(accounts.c.account_id == "DEMO-test")
                    .values(
                        state=original["state"],
                        fence=original["fence"],
                        lease_until=original["lease_until"],
                    )
                )
        return new

    report = asyncio.run(exercise(restart_case, operation, reader))
    assert report["restart_check"] == "OPEN_ORDER_API_RESTART_VERIFIED", report


def test_callback_timeout_reason_and_durable_result_survive_session_cancellation(restart_case):
    store, c, _, _ = restart_case
    operation = uuid4()
    identity = str(uuid4())
    report = asyncio.run(exercise(restart_case, operation, lambda: identity))
    assert report["restart_check"] == "NOT_ACCEPTED"
    assert report["failure"] == "API_RESTART_NOT_OBSERVED"
    assert report["failure_stage"] in {
        "WAIT_API_RESTART",
        "RECONCILE_PENDING_ORDER",
        "VERIFY_PENDING_ORDER",
        "READ_API_INSTANCE",
    }
    assert report["ready_at_utc"] and report["wait_deadline_utc"] and report["api_reads"] >= 1
    assert report["last_seen_api_instance"] == identity and report["wait_elapsed_ms"] > 0
    assert report["flat_confirmation"] == "VERIFIED_FLAT"
    assert report["risk_state"] == "NORMAL" and report["final_paused"]
    from uuid import uuid5

    assert (
        store.read("DEMO-test").operations[str(uuid5(operation, "restart-result"))]
        == "OPEN_API_RESTART:API_RESTART_NOT_OBSERVED"
    )


def test_private_exception_is_classified_without_printing_text(restart_case, monkeypatch, capsys):
    store, c, _, _ = restart_case
    operation = uuid4()
    identity = str(uuid4())
    calls = 0

    def reader():
        nonlocal calls
        calls += 1
        if calls == 1:
            return identity
        raise RuntimeError("PRIVATE_API_KEY_URL_RAW")

    report = asyncio.run(exercise(restart_case, operation, reader))
    assert (
        report["failure"] == "OTHER_VALIDATION_FAILURE"
        and report["failure_stage"] == "READ_API_INSTANCE"
    )
    assert "PRIVATE_API_KEY_URL_RAW" not in json.dumps(report) + capsys.readouterr().out
    assert report["flat_confirmation"] == "VERIFIED_FLAT"


def test_final_history_rejection_cannot_keep_none_failure(restart_case):
    from decimal import Decimal

    store, c, fake, _ = restart_case
    store.change(
        c.lease,
        "TEST_PRIOR_HIGH_WATER",
        lambda ledger: setattr(ledger, "high_water", Decimal("10001")),
    )
    old, new = str(uuid4()), str(uuid4())
    calls = 0

    def reader():
        nonlocal calls
        calls += 1
        if calls == 1:
            return old
        fake.wallet += Decimal("0.1")
        fake.incomes.append(dict(incomeType="TRANSFER", asset="USDT", tranId=123, income="0.1"))
        return new

    report = asyncio.run(exercise(restart_case, uuid4(), reader))
    assert report["failure"] == "HISTORY_CHANGED" and report["failure_stage"] == "FINAL_STATE"
    assert report["restart_check"] == "NOT_ACCEPTED"


def test_cleanup_failure_has_distinct_cause_and_never_leaks_private_text(
    restart_case, monkeypatch, capsys
):
    from integrations.binance_usdm.demo import DemoFailure

    _, c, _, _ = restart_case

    def fail():
        raise DemoFailure("PRIVATE_CLEANUP_ERROR_URL")

    monkeypatch.setattr(c, "confirm_flat", fail)
    ids = iter([str(uuid4()), str(uuid4())])
    report = asyncio.run(exercise(restart_case, uuid4(), lambda: next(ids)))
    assert report["failure"] == "FLAT_NOT_CONFIRMED" and report["flat_confirmation"] == "UNKNOWN"
    assert report["completion_failure"] == "DEMO_SESSION_OR_QUERY_FAILED"
    assert "PRIVATE_CLEANUP_ERROR_URL" not in json.dumps(report) + capsys.readouterr().out


def test_result_audit_failure_prevents_success_even_after_flat(restart_case, monkeypatch, capsys):
    store, c, _, _ = restart_case
    original = store.change

    def changed(lease, kind, callback):
        if kind == "DEMO_OPEN_API_RESTART_RESULT":
            raise RuntimeError("PRIVATE_DATABASE_URL")
        return original(lease, kind, callback)

    monkeypatch.setattr(store, "change", changed)
    ids = iter([str(uuid4()), str(uuid4())])
    report = asyncio.run(exercise(restart_case, uuid4(), lambda: next(ids)))
    assert report["restart_check"] == "NOT_ACCEPTED" and report["failure"] == "RESULT_AUDIT_FAILED"
    assert report["flat_confirmation"] == "VERIFIED_FLAT"
    assert "PRIVATE_DATABASE_URL" not in json.dumps(report) + capsys.readouterr().out


def test_ready_committed_during_query_is_measured_at_read_completion(restart_case, monkeypatch):
    store, c, _, _ = restart_case
    monkeypatch.setattr(waiter, "ACCOUNT", "DEMO-test")
    operation = uuid4()
    old, new = str(uuid4()), str(uuid4())
    calls = 0
    clock = [store.now() - timedelta(milliseconds=1)]
    monkeypatch.setattr(waiter, "utc_now", lambda: clock[0])

    def marker_read(conn, cursor, statement, parameters, context, executemany):
        if "demo_events" in statement:
            clock[0] = store.now() + timedelta(milliseconds=1)

    def reader():
        nonlocal calls
        calls += 1
        if calls == 1:
            return old
        event.listen(store.engine, "before_cursor_execute", marker_read)
        try:
            code, _ = waiter.inspect_ready(store.engine, operation)
            assert code == "READY"
        finally:
            event.remove(store.engine, "before_cursor_execute", marker_read)
        return new

    report = asyncio.run(exercise(restart_case, operation, reader))
    assert report["restart_check"] == "OPEN_ORDER_API_RESTART_VERIFIED", report


def test_ready_does_not_become_untrusted_again_before_host_handoff(restart_case, monkeypatch):
    store, c, _, _ = restart_case
    monkeypatch.setattr(waiter, "ACCOUNT", "DEMO-test")
    operation = uuid4()
    old, new = str(uuid4()), str(uuid4())
    calls = 0
    original = store.change
    state = {"ready": False, "locked": False}

    def changed(lease, kind, callback):
        if kind == "DEMO_API_RESTART_GATE_LOCKED":
            state["locked"] = True
        result = original(lease, kind, callback)
        if kind == "DEMO_OPEN_API_RESTART_READY":
            state["ready"] = True
        if state["ready"] and not state["locked"]:
            ledger = store.read("DEMO-test")
            assert ledger.reconciled and ledger.stream_ready
        return result

    monkeypatch.setattr(store, "change", changed)

    def reader():
        nonlocal calls
        calls += 1
        if calls == 1:
            return old
        assert waiter.inspect_ready(store.engine, operation, store.now())[0] == "READY"
        return new

    report = asyncio.run(exercise(restart_case, operation, reader))
    assert report["restart_check"] == "OPEN_ORDER_API_RESTART_VERIFIED", report


def test_partial_fill_on_pending_poll_enters_original_protection_before_cleanup(restart_case):
    from decimal import Decimal

    from tests.integration.test_demo import posts

    store, c, fake, _ = restart_case
    identity = str(uuid4())
    calls = 0

    def reader():
        nonlocal calls
        calls += 1
        if calls == 1:
            return identity
        current = next(o for o in store.read("DEMO-test").orders.values() if o.remaining)
        fake.fill(current.client_id, Decimal("0.001"), price=current.signal.suggested_limit_price)
        return identity

    report = asyncio.run(exercise(restart_case, uuid4(), reader))
    assert report["restart_check"] == "NOT_ACCEPTED"
    assert report["failure"] == "ENTRY_NOT_OPEN_UNFILLED"
    assert report["failure_stage"] == "RECONCILE_CHANGED_ORDER"
    assert report["flat_confirmation"] == "VERIFIED_FLAT"
    assert fake.positions["BTCUSDT"] == 0
    assert len(posts(fake, "/fapi/v1/algoOrder")) == 2
