"""Legacy anomalies remain in PostgreSQL while GET-only evidence is collected."""

from datetime import timedelta
from decimal import Decimal

import pytest

from integrations.binance_usdm.demo import DemoFailure
from packages.domain.contracts import PositionState, RiskState
from services.execution.demo_cancelled_history import (
    CancelledHistoryBlocked,
    review_cancelled_history,
)
from services.execution.demo_history_review import HistoryBlocked, review_flat_history
from tests.integration.test_demo import enter
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_isolated import configure


@pytest.fixture
def legacy(demo_system):  # noqa: F811
    system, _ = configure(demo_system)
    store, c, fake = system
    sig, order = enter(system)
    c.cancel(str(sig.signal_id))

    def legacy_state(ledger):
        o = ledger.orders[str(sig.signal_id)]
        assert o.filled == 0 and o.remaining == 0
        o.open_quantity = o.quantity
        o.position_state = PositionState.EXIT_PENDING
        o.exit_state = "UNKNOWN"
        ledger.paused = True
        ledger.risk_state = RiskState.EMERGENCY
        ledger.requests["DX-legacy-unknown"] = "SUBMITTING"

    store.change(c.lease, "TEST_REPRODUCE_OLD_ANOMALY", legacy_state)
    c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": False})
    original = c.client.send

    def send(method, path, query, key):
        assert method == "GET"
        if path == "/fapi/v1/userTrades":
            fake.calls.append((method, path, {}))
            return fake.response([])
        return original(method, path, query, key)

    c.client.send = send
    fake.at += timedelta(days=2)
    c.client.sync_clock()
    return store, c, fake, sig, order


def test_review_preserves_anomaly_requests_baselines_and_lock(legacy):
    store, c, fake, _, _ = legacy
    before = store.read("DEMO-test")
    serial = before.model_dump_json()
    start = len(fake.calls)
    with pytest.raises(HistoryBlocked, match="INVALID_LOCAL_FLAT_ANCHOR"):
        review_flat_history(store, c.client, before)
    report = review_cancelled_history(store, c.client, before)
    assert report["review"] == "LEGACY_CANCELLED_HISTORY_EVIDENCE_ONLY"
    assert report["confirmed_cancelled_zero_fill_orders"] == 1
    assert report["local_ledger_repaired"] is False
    assert report["unresolved_requests_acknowledged"] is False
    assert report["recovery_eligible"] is False
    assert report["paused"] is True and report["risk_state"] == "EMERGENCY"
    assert "local_flat_anchor_utc" not in report
    assert "legacy_observation_anchor_utc" in report
    assert all(m == "GET" for m, _, _ in fake.calls[start:])
    assert before.model_dump_json() == serial
    assert store.read("DEMO-test").model_dump_json() == serial


@pytest.mark.parametrize(
    "field,value",
    [
        ("executedQty", "0.001"),
        ("status", "FILLED"),
        ("orderId", "wrong"),
        ("origQty", "999"),
        ("price", "1"),
        ("side", "SELL"),
        ("reduceOnly", "true"),
        ("positionSide", "LONG"),
    ],
)
def test_remote_identity_or_zero_fill_disagreement_blocks(legacy, field, value):
    store, c, fake, _, order = legacy
    before = store.read("DEMO-test").model_dump_json()
    fake.orders[order.client_id][field] = value
    with pytest.raises(DemoFailure):
        # Snapshot parsing may reject the invalid row before the identity guard.
        review_cancelled_history(store, c.client, store.read("DEMO-test"))
    assert store.read("DEMO-test").model_dump_json() == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("filled", Decimal("0.001")),
        ("remaining", Decimal("0.001")),
        ("open_quantity", Decimal("0.001")),
        ("exit_state", "FILLED"),
    ],
)
def test_other_local_shapes_are_not_projected(legacy, field, value):
    store, c, fake, sig, _ = legacy
    state = store.read("DEMO-test")
    setattr(state.orders[str(sig.signal_id)], field, value)
    start = len(fake.calls)
    with pytest.raises(CancelledHistoryBlocked, match="LEGACY_CANCELLED_SHAPE_MISMATCH"):
        review_cancelled_history(store, c.client, state)
    assert len(fake.calls) == start


def test_current_exposure_blocks(legacy):
    store, c, fake, _, _ = legacy
    fake.positions["BTCUSDT"] = Decimal("0.001")
    fake.prices["BTCUSDT"] = Decimal("100")
    with pytest.raises(CancelledHistoryBlocked, match="CURRENT_EXPOSURE"):
        review_cancelled_history(store, c.client, store.read("DEMO-test"))


def test_cli_has_no_lease_or_writes_and_clears_credentials(legacy, monkeypatch, capsys):
    import os

    import scripts.review_demo_cancelled_history as cli

    store, c, fake, _, _ = legacy
    original_read = store.read
    before = original_read("DEMO-test").model_dump_json()
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "SYNTHETIC_PRIVATE_KEY")
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: store.engine)
    monkeypatch.setattr(cli, "DemoStore", lambda engine: store)
    monkeypatch.setattr(cli, "DemoClient", lambda settings, credentials: c.client)
    monkeypatch.setattr(store, "read", lambda account: original_read("DEMO-test"))

    def no_write(*args):
        raise AssertionError("Read-only evidence must not acquire or change")

    monkeypatch.setattr(store, "acquire", no_write)
    monkeypatch.setattr(store, "change", no_write)
    cli.main()
    output = capsys.readouterr().out
    assert "LEGACY_CANCELLED_HISTORY_EVIDENCE_ONLY" in output
    assert "SYNTHETIC_PRIVATE_KEY" not in output
    assert "BINANCE_DEMO_API_KEY" not in os.environ
    assert "BINANCE_DEMO_API_SECRET" not in os.environ
    assert original_read("DEMO-test").model_dump_json() == before


def test_missing_remote_order_is_not_treated_as_never_filled(legacy):
    store, c, fake, _, order = legacy
    del fake.orders[order.client_id]
    with pytest.raises(CancelledHistoryBlocked, match="CANCELLED_ZERO_FILL_NOT_CONFIRMED"):
        review_cancelled_history(store, c.client, store.read("DEMO-test"))


def test_snapshot_change_during_evidence_blocks(legacy, monkeypatch):
    import services.execution.demo_cancelled_history as module

    store, c, _, _, _ = legacy
    before = store.read("DEMO-test").model_dump_json()
    original = module.snapshot
    calls = 0

    def changed(*args):
        nonlocal calls
        remote = original(*args)
        calls += 1
        if calls == 2:
            remote.fingerprint = "changed"
        return remote

    monkeypatch.setattr(module, "snapshot", changed)
    with pytest.raises(CancelledHistoryBlocked, match="SNAPSHOT_CHANGED"):
        review_cancelled_history(store, c.client, store.read("DEMO-test"))
    assert store.read("DEMO-test").model_dump_json() == before


def test_write_enabled_context_blocks_before_network(legacy):
    store, c, fake, _, _ = legacy
    c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": True})
    start = len(fake.calls)
    with pytest.raises(CancelledHistoryBlocked, match="CONTEXT_MISMATCH"):
        review_cancelled_history(store, c.client, store.read("DEMO-test"))
    assert len(fake.calls) == start
