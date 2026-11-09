"""Audited operator recovery uses GET only and preserves unresolved history."""

from datetime import timedelta
from decimal import Decimal as D
from uuid import uuid4

import pytest

from integrations.binance_usdm.demo import DemoFailure
from packages.domain.contracts import RiskState
from services.execution.demo_recovery import acknowledge_flat_emergency, review
from services.risk.paper import RiskRejected
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_isolated import configure


@pytest.fixture
def recovery(demo_system):  # noqa: F811
    system, _ = configure(demo_system)
    store, c, fake = system
    c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": False})

    def locked(ledger):
        ledger.risk_state = RiskState.EMERGENCY
        ledger.paused = True
        ledger.requests["DS-historical"] = "UNKNOWN"
        ledger.requests["DX-historical"] = "SUBMITTING"

    store.change(c.lease, "TEST_OLD_EMERGENCY", locked)
    return system


def test_review_does_not_write_or_unlock(recovery):
    store, c, fake = recovery
    before = store.read("DEMO-test").model_dump_json()
    start = len(fake.calls)
    result = review(store, c.client, store.read("DEMO-test"))
    assert result["unresolved_count"] == 2
    assert len(result["review_token"]) == 64
    assert store.read("DEMO-test").model_dump_json() == before
    assert all(method == "GET" for method, _, _ in fake.calls[start:])


def test_explicit_ack_preserves_history_and_keeps_entries_paused(recovery):
    store, c, fake = recovery
    before = store.read("DEMO-test")
    result = review(store, c.client, before)
    op = uuid4()
    assert (
        acknowledge_flat_emergency(
            store, c.lease, c.client, op, result["review_token"], acknowledge_unresolved=True
        )
        == "EMERGENCY_ACKNOWLEDGED_ENTRIES_STILL_PAUSED"
    )
    ledger = store.read("DEMO-test")
    assert ledger.risk_state == RiskState.NORMAL and ledger.paused
    assert not ledger.reconciled and not ledger.stream_ready
    assert ledger.requests == before.requests and ledger.orders == before.orders
    assert ledger.value == before.value and ledger.high_water == before.high_water
    assert ledger.daily_base == before.daily_base and ledger.weekly_base == before.weekly_base
    assert ledger.recovery_receipts[str(op)] == result["review_token"]
    assert all(method == "GET" for method, _, _ in fake.calls)
    assert (
        acknowledge_flat_emergency(
            store, c.lease, c.client, op, result["review_token"], acknowledge_unresolved=True
        )
        == "ALREADY_RECORDED_NO_ACTION"
    )


@pytest.mark.parametrize(
    "reason",
    [
        "no_ack",
        "bad_token",
        "unpaused",
        "daily_lock",
        "weekly_lock",
        "drawdown_lock",
        "position",
        "loss",
        "day_boundary",
        "write_enabled",
    ],
)
def test_recovery_blocks_unsafe_or_unreviewed_changes(recovery, reason):
    store, c, fake = recovery
    token = review(store, c.client, store.read("DEMO-test"))["review_token"]
    ack = True
    if reason == "no_ack":
        ack = False
    elif reason == "bad_token":
        token = "f" * 64
    elif reason == "position":
        fake.positions["BTCUSDT"] = D("0.1")
    elif reason == "loss":
        fake.wallet = D("1")
    elif reason == "day_boundary":
        fake.at += timedelta(days=1)
    elif reason == "write_enabled":
        c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": True})
    else:

        def alter(ledger):
            if reason == "unpaused":
                ledger.paused = False
            else:
                ledger.risk_state = RiskState(reason.upper())

        store.change(c.lease, "TEST_BLOCK_RECOVERY", alter)
    before = store.read("DEMO-test").model_dump_json()
    with pytest.raises((DemoFailure, RiskRejected)):
        acknowledge_flat_emergency(
            store, c.lease, c.client, uuid4(), token, acknowledge_unresolved=ack
        )
    assert store.read("DEMO-test").model_dump_json() == before


def test_operation_id_conflict_preserves_state(recovery):
    store, c, _ = recovery
    token = review(store, c.client, store.read("DEMO-test"))["review_token"]
    op = uuid4()
    store.change(
        c.lease, "TEST_OP_CONFLICT", lambda ledger: ledger.operations.update({str(op): "SAFE_STOP"})
    )
    with pytest.raises(DemoFailure, match="conflict"):
        acknowledge_flat_emergency(store, c.lease, c.client, op, token, acknowledge_unresolved=True)


def test_recovery_rolls_back_if_exchange_changes_during_audit(recovery, monkeypatch):
    import services.execution.demo_recovery as module

    store, c, _ = recovery
    token = review(store, c.client, store.read("DEMO-test"))["review_token"]
    real = module.snapshot
    count = 0

    def changes(*args):
        nonlocal count
        value = real(*args)
        count += 1
        if count == 3:
            value.fingerprint = "changed"
        return value

    monkeypatch.setattr(module, "snapshot", changes)
    before = store.read("DEMO-test").model_dump_json()
    with pytest.raises(DemoFailure, match="changed during"):
        acknowledge_flat_emergency(
            store, c.lease, c.client, uuid4(), token, acknowledge_unresolved=True
        )
    assert store.read("DEMO-test").model_dump_json() == before


@pytest.mark.parametrize("problem", ["active", "wrong_identity", "query_failure"])
def test_uncertain_or_active_history_blocks_recovery(recovery, problem):
    from integrations.binance_usdm.demo import DemoUnknown

    store, c, fake = recovery
    original = c.client.send

    def transport(method, path, query, key):
        if method == "GET" and path == "/fapi/v1/algoOrder":
            if problem == "query_failure":
                raise DemoUnknown("synthetic query failure")
            return fake.response(
                {
                    "symbol": "BTCUSDT",
                    "clientAlgoId": "DS-historical" if problem == "active" else "other",
                    "algoStatus": "NEW" if problem == "active" else "CANCELED",
                }
            )
        return original(method, path, query, key)

    c.client.send = transport
    before = store.read("DEMO-test").model_dump_json()
    with pytest.raises(DemoFailure):
        review(store, c.client, store.read("DEMO-test"))
    assert store.read("DEMO-test").model_dump_json() == before


def test_expired_lease_cannot_acknowledge(recovery):
    store, c, fake = recovery
    token = review(store, c.client, store.read("DEMO-test"))["review_token"]
    before = store.read("DEMO-test").model_dump_json()
    fake.at += timedelta(seconds=60)
    with pytest.raises(RiskRejected):
        acknowledge_flat_emergency(
            store, c.lease, c.client, uuid4(), token, acknowledge_unresolved=True
        )
    assert store.read("DEMO-test").model_dump_json() == before


def test_cli_default_review_never_acquires_lease(recovery, monkeypatch, capsys):
    import scripts.review_demo_recovery as cli

    store, c, _ = recovery
    before = store.read("DEMO-test").model_dump_json()
    monkeypatch.setattr(
        cli.sys if hasattr(cli, "sys") else __import__("sys"), "argv", ["review_demo_recovery"]
    )
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "synthetic-recovery-secret")
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: store.engine)
    monkeypatch.setattr(cli, "DemoStore", lambda engine: store)
    monkeypatch.setattr(cli, "DemoClient", lambda settings, credentials: c.client)
    original_read = store.read
    monkeypatch.setattr(store, "read", lambda account: original_read("DEMO-test"))

    def no_lease(*args):
        raise AssertionError("Preview must never acquire")

    monkeypatch.setattr(store, "acquire", no_lease)
    cli.main()
    assert "REVIEW_ONLY_NO_DATABASE_CHANGE" in capsys.readouterr().out
    assert original_read("DEMO-test").model_dump_json() == before


def test_cli_incomplete_apply_flags_block_before_credentials(monkeypatch):
    import sys

    import scripts.review_demo_recovery as cli

    monkeypatch.setattr(sys, "argv", ["review_demo_recovery", "--apply-review", "a" * 64])

    def no_prompt(*args):
        raise AssertionError("Invalid invocation must not prompt")

    monkeypatch.setattr(cli.getpass, "getpass", no_prompt)
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 2
