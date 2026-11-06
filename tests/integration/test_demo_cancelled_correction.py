"""Real PostgreSQL audit, fencing and rollback with synthetic signed DEMO truth."""

from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from packages.domain.contracts import PositionState
from packages.persistence.demo import events
from services.execution.demo_cancelled_correction import (
    CancelledCorrectionBlocked,
    apply_correction,
    review_correction,
)
from services.execution.demo_cancelled_history import CancelledHistoryBlocked
from services.risk.paper import RiskRejected
from tests.integration.test_demo import enter
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_isolated import configure
from tests.unit.test_demo_trade_history_window import row


@pytest.fixture
def correction(demo_system):  # noqa: F811
    system, _ = configure(demo_system)
    store, c, fake = system
    past_sig, past = enter(system)
    c.cancel(str(past_sig.signal_id))
    sig, current = enter(system)
    fake.fill(current.client_id, current.quantity)
    c.reconcile()
    entry_stamp = int(fake.at.timestamp() * 1000)
    fake.at += timedelta(seconds=1)
    c.command(uuid4(), "EMERGENCY_FLATTEN")
    c.reconcile()
    state = store.read("DEMO-test")
    closed = state.orders[str(sig.signal_id)]
    assert closed.position_state == PositionState.CLOSED
    exit_order = fake.orders[closed.exit_id]
    rows = [
        row(
            id=1,
            orderId=fake.orders[current.client_id]["orderId"],
            time=entry_stamp,
            qty=str(current.quantity),
            commission="0",
        ),
        row(
            id=2,
            orderId=exit_order["orderId"],
            time=int(fake.at.timestamp() * 1000),
            side="SELL",
            qty=str(current.quantity),
            commission="0",
        ),
    ]

    def legacy(ledger):
        order = ledger.orders[str(past_sig.signal_id)]
        assert order.filled == 0 and order.remaining == 0
        order.open_quantity = order.quantity
        order.actual_risk = order.quantity * Decimal(100)
        order.position_state = PositionState.EXIT_PENDING
        order.exit_state = "UNKNOWN"
        ledger.requests["DX-old-ambiguous"] = "SUBMITTING"
        ledger.requests["DS-old-ambiguous"] = "UNKNOWN"

    store.change(c.lease, "TEST_OLD_CANCELLED_EXPOSURE", legacy)
    fake.at += timedelta(days=2)
    store.release(c.lease)
    c.lease = store.acquire("DEMO-test", uuid4())
    c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": False})
    original = c.client.send

    def send(method, path, query, key):
        assert method == "GET", "Correction must never send exchange mutations"
        if path == "/fapi/v1/userTrades":
            params = {k: v[0] for k, v in parse_qs(query.get_secret_value()).items()}
            fake.calls.append((method, path, {}))
            return fake.response(
                [r for r in rows if int(params["startTime"]) <= r["time"] <= int(params["endTime"])]
            )
        return original(method, path, query, key)

    c.client.send = send
    c.client.sync_clock()
    return store, c, fake, past_sig, past, rows


def event_count(store):
    with store.engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(events)).scalar_one()


def test_preview_does_not_write_and_proposes_exact_exposure_changes(correction):
    store, c, fake, sig, past, _ = correction
    before = store.read("DEMO-test").model_dump_json()
    count = event_count(store)
    start = len(fake.calls)
    report = review_correction(store, c.client, store.read("DEMO-test"), account_id="DEMO-test")
    proposal = report["proposed_orders"][0]
    assert proposal["order_key"] == str(sig.signal_id)
    assert proposal["open_quantity_before"] == str(past.quantity)
    assert proposal["open_quantity_after"] == "0"
    assert proposal["position_state_after"] == "CLOSED"
    assert proposal["exit_state_preserved"] == "UNKNOWN"
    assert report["correction"] == "REVIEW_ONLY_NO_DATABASE_CHANGE"
    assert report["recovery_eligible"] is False
    assert report["paused"] is True and report["risk_state"] == "EMERGENCY"
    assert len(report["review_token"]) == 64
    assert all(m == "GET" for m, _, _ in fake.calls[start:])
    assert store.read("DEMO-test").model_dump_json() == before
    assert event_count(store) == count


def test_apply_is_atomic_audited_narrow_and_idempotent(correction):
    store, c, fake, sig, _, _ = correction
    before = store.read("DEMO-test")
    token = review_correction(store, c.client, before, account_id="DEMO-test")["review_token"]
    op = uuid4()
    start = len(fake.calls)
    result = apply_correction(store, c.lease, c.client, op, token)
    assert result == "LOCAL_CANCELLED_EXPOSURE_CORRECTED_KEEP_LOCK"
    after = store.read("DEMO-test")
    exclude = {
        "orders",
        "operations",
        "cancelled_exposure_corrections",
        "reconciled",
        "stream_ready",
    }
    assert after.model_dump(exclude=exclude) == before.model_dump(exclude=exclude)
    for key, order in before.orders.items():
        if key == str(sig.signal_id):
            expected = order.model_copy(deep=True)
            expected.open_quantity = expected.actual_risk = Decimal(0)
            expected.position_state = PositionState.CLOSED
            assert after.orders[key] == expected
        else:
            assert after.orders[key] == order
    assert after.requests == before.requests
    assert after.risk_state.value == "EMERGENCY" and after.paused
    assert not after.reconciled and not after.stream_ready
    receipt = after.cancelled_exposure_corrections[str(op)]
    assert receipt.original_orders == {str(sig.signal_id): before.orders[str(sig.signal_id)]}
    assert receipt.review_token == token
    assert len(receipt.evidence_hash) == len(receipt.exchange_fingerprint) == 64
    with store.engine.connect() as conn:
        audit = conn.execute(
            select(events.c.payload).where(events.c.kind == "DEMO_CANCELLED_EXPOSURE_CORRECTED")
        ).scalar_one()
    assert str(op) in audit and token in audit
    assert apply_correction(store, c.lease, c.client, op, token) == "ALREADY_RECORDED_NO_ACTION"
    assert store.read("DEMO-test") == after
    assert all(m == "GET" for m, _, _ in fake.calls[start:])


@pytest.mark.parametrize(
    "change",
    ["ledger", "wallet", "history", "token", "operation", "filled", "not_found", "position"],
)
def test_stale_or_conflicting_evidence_rolls_back(correction, change):
    store, c, fake, sig, past, rows = correction
    token = review_correction(store, c.client, store.read("DEMO-test"), account_id="DEMO-test")[
        "review_token"
    ]
    op = uuid4()
    if change == "ledger":
        store.change(
            c.lease,
            "TEST_NEW_REQUEST",
            lambda ledger: ledger.requests.update({"DX-another": "UNKNOWN"}),
        )
    elif change == "wallet":
        fake.wallet += 1
    elif change == "history":
        rows[0]["commission"] = "0.02"
    elif change == "token":
        token = "f" * 64
    elif change == "operation":
        store.change(
            c.lease,
            "TEST_OTHER_OPERATION",
            lambda ledger: ledger.operations.update({str(op): "OTHER"}),
        )
    elif change == "filled":
        fake.orders[past.client_id]["executedQty"] = "0.001"
    elif change == "not_found":
        del fake.orders[past.client_id]
    elif change == "position":
        fake.positions["BTCUSDT"] = Decimal("0.001")
        fake.prices["BTCUSDT"] = Decimal(100)
    before = store.read("DEMO-test").model_dump_json()
    count = event_count(store)
    with pytest.raises((CancelledCorrectionBlocked, CancelledHistoryBlocked)):
        apply_correction(store, c.lease, c.client, op, token)
    assert store.read("DEMO-test").model_dump_json() == before
    assert event_count(store) == count


def test_lease_expiry_during_review_rolls_back_receipt_and_correction(correction, monkeypatch):
    import services.execution.demo_cancelled_correction as module

    store, c, fake, _, _, _ = correction
    token = review_correction(store, c.client, store.read("DEMO-test"), account_id="DEMO-test")[
        "review_token"
    ]
    before = store.read("DEMO-test").model_dump_json()
    count = event_count(store)
    original = module.review_correction

    def delayed(*args, **kwargs):
        result = original(*args, **kwargs)
        fake.at += timedelta(seconds=61)
        return result

    monkeypatch.setattr(module, "review_correction", delayed)
    with pytest.raises(RiskRejected, match="outlived lease"):
        apply_correction(store, c.lease, c.client, uuid4(), token)
    assert store.read("DEMO-test").model_dump_json() == before
    assert event_count(store) == count


def test_new_lease_preserves_token_but_old_leader_is_fenced(correction):
    store, c, _, _, _, _ = correction
    token = review_correction(store, c.client, store.read("DEMO-test"), account_id="DEMO-test")[
        "review_token"
    ]
    old = c.lease
    store.release(old)
    new = store.acquire("DEMO-test", uuid4())
    before = store.read("DEMO-test").model_dump_json()
    with pytest.raises(RiskRejected, match="fenced"):
        apply_correction(store, old, c.client, uuid4(), token)
    assert store.read("DEMO-test").model_dump_json() == before
    assert (
        apply_correction(store, new, c.client, uuid4(), token)
        == "LOCAL_CANCELLED_EXPOSURE_CORRECTED_KEEP_LOCK"
    )


def test_repeat_with_conflicting_token_cannot_reuse_receipt(correction):
    store, c, _, _, _, _ = correction
    token = review_correction(store, c.client, store.read("DEMO-test"), account_id="DEMO-test")[
        "review_token"
    ]
    op = uuid4()
    apply_correction(store, c.lease, c.client, op, token)
    before = store.read("DEMO-test").model_dump_json()
    with pytest.raises(CancelledCorrectionBlocked, match="OPERATION_CONFLICT"):
        apply_correction(store, c.lease, c.client, op, "f" * 64)
    assert store.read("DEMO-test").model_dump_json() == before


@pytest.mark.parametrize(
    "change", ["missing_exit", "later_trade", "simultaneous_trade", "today_trade"]
)
def test_incomplete_or_additional_trade_history_cannot_authorize_correction(correction, change):
    store, c, fake, _, _, rows = correction
    if change == "missing_exit":
        rows.clear()
    else:
        stamp = rows[1]["time"]
        if change == "later_trade":
            stamp += 10
        elif change == "today_trade":
            stamp = int(fake.at.timestamp() * 1000)
        rows.append(row(id=3, orderId=999, time=stamp))
    before = store.read("DEMO-test").model_dump_json()
    with pytest.raises(CancelledCorrectionBlocked, match="LEGACY_EXIT_HISTORY_NOT_CONFIRMED"):
        review_correction(store, c.client, store.read("DEMO-test"), account_id="DEMO-test")
    assert store.read("DEMO-test").model_dump_json() == before


def test_cli_default_preview_has_no_lease_or_writes(correction, monkeypatch, capsys):
    import os
    import sys

    import scripts.review_demo_cancelled_correction as cli

    store, c, _, _, _, _ = correction
    original_read = store.read
    before = original_read("DEMO-test").model_dump_json()
    count = event_count(store)
    monkeypatch.setattr(cli, "ACCOUNT_ID", "DEMO-test")
    monkeypatch.setattr(sys, "argv", ["review_demo_cancelled_correction"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "SYNTHETIC_PRIVATE_KEY")
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: store.engine)
    monkeypatch.setattr(cli, "DemoStore", lambda engine: store)
    monkeypatch.setattr(cli, "DemoClient", lambda settings, credentials: c.client)
    monkeypatch.setattr(store, "read", lambda account: original_read("DEMO-test"))

    def no_write(*args):
        raise AssertionError("Preview may not acquire, change or release")

    for name in ("acquire", "change", "release"):
        monkeypatch.setattr(store, name, no_write)
    cli.main()
    output = capsys.readouterr().out
    assert "REVIEW_ONLY_NO_DATABASE_CHANGE" in output
    assert "SYNTHETIC_PRIVATE_KEY" not in output
    assert "BINANCE_DEMO_API_KEY" not in os.environ
    assert "BINANCE_DEMO_API_SECRET" not in os.environ
    assert original_read("DEMO-test").model_dump_json() == before
    assert event_count(store) == count


def test_cli_explicit_apply_is_separate_and_keeps_emergency(correction, monkeypatch, capsys):
    import sys

    import scripts.review_demo_cancelled_correction as cli

    store, c, _, _, _, _ = correction
    token = review_correction(store, c.client, store.read("DEMO-test"), account_id="DEMO-test")[
        "review_token"
    ]
    op = uuid4()
    requests = store.read("DEMO-test").requests
    monkeypatch.setattr(cli, "ACCOUNT_ID", "DEMO-test")
    store.release(c.lease)
    original_read, original_acquire = store.read, store.acquire
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "review_demo_cancelled_correction",
            "--apply-correction",
            token,
            "--operation-id",
            str(op),
        ],
    )
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "SYNTHETIC_PRIVATE_KEY")
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: store.engine)
    monkeypatch.setattr(cli, "DemoStore", lambda engine: store)
    monkeypatch.setattr(cli, "DemoClient", lambda settings, credentials: c.client)
    monkeypatch.setattr(store, "read", lambda account: original_read("DEMO-test"))
    monkeypatch.setattr(
        store, "acquire", lambda account, owner: original_acquire("DEMO-test", owner)
    )
    cli.main()
    output = capsys.readouterr().out
    assert "LOCAL_CANCELLED_EXPOSURE_CORRECTED_KEEP_LOCK" in output
    assert "SYNTHETIC_PRIVATE_KEY" not in output
    after = original_read("DEMO-test")
    assert after.paused and after.risk_state.value == "EMERGENCY"
    assert str(op) in after.cancelled_exposure_corrections
    assert after.requests == requests


@pytest.mark.parametrize(
    "arguments",
    [
        ["--apply-correction", "bad"],
        ["--operation-id", "f0e4fc1d-bb7d-4de0-9c91-fce24bc6a445"],
        ["--apply-correction", "BAD", "--operation-id", "f0e4fc1d-bb7d-4de0-9c91-fce24bc6a445"],
    ],
)
def test_cli_incomplete_apply_blocks_before_secret_input(monkeypatch, capsys, arguments):
    import sys

    import scripts.review_demo_cancelled_correction as cli

    monkeypatch.setattr(sys, "argv", ["review_demo_cancelled_correction", *arguments])

    def no_prompt(*args):
        raise AssertionError("Invalid arguments must not prompt")

    monkeypatch.setattr(cli.getpass, "getpass", no_prompt)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert "REVIEW_TOKEN_AND_OPERATION_ID_REQUIRED" in capsys.readouterr().out


def test_changed_day_invalidates_token_even_with_fresh_lease(correction):
    store, c, fake, _, _, _ = correction
    token = review_correction(store, c.client, store.read("DEMO-test"), account_id="DEMO-test")[
        "review_token"
    ]
    store.release(c.lease)
    fake.at += timedelta(days=1)
    c.client.sync_clock()
    new = store.acquire("DEMO-test", uuid4())
    before = store.read("DEMO-test").model_dump_json()
    with pytest.raises(CancelledCorrectionBlocked, match="CORRECTION_REVIEW_CHANGED"):
        apply_correction(store, new, c.client, uuid4(), token)
    assert store.read("DEMO-test").model_dump_json() == before


def test_write_enabled_client_cannot_apply_even_with_valid_preview(correction):
    store, c, fake, _, _, _ = correction
    token = review_correction(store, c.client, store.read("DEMO-test"), account_id="DEMO-test")[
        "review_token"
    ]
    c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": True})
    before = store.read("DEMO-test").model_dump_json()
    start = len(fake.calls)
    with pytest.raises(CancelledCorrectionBlocked, match="READ_ONLY_CLIENT_REQUIRED"):
        apply_correction(store, c.lease, c.client, uuid4(), token)
    assert store.read("DEMO-test").model_dump_json() == before
    assert len(fake.calls) == start


def test_token_is_bound_to_original_account_namespace(correction):
    store, c, _, _, _, _ = correction
    token = review_correction(store, c.client, store.read("DEMO-test"), account_id="DEMO-other")[
        "review_token"
    ]
    before = store.read("DEMO-test").model_dump_json()
    with pytest.raises(CancelledCorrectionBlocked, match="CORRECTION_REVIEW_CHANGED"):
        apply_correction(store, c.lease, c.client, uuid4(), token)
    assert store.read("DEMO-test").model_dump_json() == before
