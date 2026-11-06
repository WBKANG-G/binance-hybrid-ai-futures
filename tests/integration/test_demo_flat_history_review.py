"""GET-only history across a local flat anchor preserves the entire ledger."""

from datetime import timedelta
from urllib.parse import parse_qs

import pytest

from services.execution.demo_history_review import HistoryBlocked, review_flat_history
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_recovery import recovery  # noqa: F401


@pytest.fixture
def history(recovery):  # noqa: F811
    store, c, fake = recovery
    anchor = store.read("DEMO-test").last_reconciled
    fake.at += timedelta(days=2)
    c.client.sync_clock()
    original = c.client.send
    rows = []

    def send(method, path, query, key):
        if path == "/fapi/v1/userTrades":
            assert method == "GET"
            params = {k: v[0] for k, v in parse_qs(query.get_secret_value()).items()}
            fake.calls.append((method, path, {}))
            return fake.response(
                [
                    r
                    for r in rows
                    if int(params["startTime"]) <= r["time"] <= int(params["endTime"])
                ][:1000]
            )
        return original(method, path, query, key)

    c.client.send = send
    return store, c, fake, rows, anchor


def test_empty_history_is_evidence_only_not_account_wide_flat_proof(history):
    store, c, fake, _, _ = history
    before = store.read("DEMO-test").model_dump_json()
    start = len(fake.calls)
    report = review_flat_history(store, c.client, store.read("DEMO-test"))
    assert (
        report["btc_trades_before_boundary"] == 0 and report["btc_trades_at_or_after_boundary"] == 0
    )
    assert report["wallet_change_matches_income"] is True
    assert report["trade_scope"] == "BTCUSDT_ONLY"
    assert (
        report["account_wide_position_history_verified"] is False
        and report["recovery_eligible"] is False
    )
    assert all(m == "GET" for m, _, _ in fake.calls[start:])
    assert store.read("DEMO-test").model_dump_json() == before


def test_additional_fill_is_counted_without_changing_state(history):
    from tests.unit.test_demo_trade_history_window import row

    store, c, _, rows, anchor = history
    rows.append(row(time=int((anchor + timedelta(seconds=1)).timestamp() * 1000)))
    before = store.read("DEMO-test").model_dump_json()
    report = review_flat_history(store, c.client, store.read("DEMO-test"))
    assert report["btc_trades_before_boundary"] == 1
    assert report["recovery_eligible"] is False
    assert store.read("DEMO-test").model_dump_json() == before


def test_changed_history_blocks_without_writes(history, monkeypatch):
    import services.execution.demo_history_review as module

    store, c, _, _, _ = history
    count = 0

    def changing(*args):
        nonlocal count
        count += 1
        return {} if count == 1 else {"different": {}}

    monkeypatch.setattr(module, "trades_window", changing)
    before = store.read("DEMO-test").model_dump_json()
    with pytest.raises(HistoryBlocked, match="HISTORY_CHANGED"):
        review_flat_history(store, c.client, store.read("DEMO-test"))
    assert store.read("DEMO-test").model_dump_json() == before


def test_cli_history_does_not_acquire_lease_or_emit_keys(history, monkeypatch, capsys):
    import os

    import scripts.review_demo_flat_history as cli

    store, c, fake, _, _ = history
    before = store.read("DEMO-test").model_dump_json()
    start = len(fake.calls)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "SYNTHETIC_PRIVATE_KEY")
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: store.engine)
    monkeypatch.setattr(cli, "DemoStore", lambda engine: store)
    monkeypatch.setattr(cli, "DemoClient", lambda settings, credentials: c.client)
    original_read = store.read
    monkeypatch.setattr(store, "read", lambda account: original_read("DEMO-test"))

    def no_lease(*args):
        raise AssertionError("Read-only history must not acquire")

    monkeypatch.setattr(store, "acquire", no_lease)
    cli.main()
    output = capsys.readouterr().out
    assert "FLAT_HISTORY_EVIDENCE_ONLY" in output and "SYNTHETIC_PRIVATE_KEY" not in output
    assert "BINANCE_DEMO_API_KEY" not in os.environ and "BINANCE_DEMO_API_SECRET" not in os.environ
    assert original_read("DEMO-test").model_dump_json() == before
    assert all(m == "GET" for m, _, _ in fake.calls[start:])


@pytest.mark.parametrize("same_millisecond", [False, True])
def test_window_includes_previous_exit_and_flags_additional_or_simultaneous_trade(
    demo_system,  # noqa: F811
    same_millisecond,
):
    from uuid import uuid4

    from tests.integration.test_demo import enter
    from tests.integration.test_demo_isolated import configure
    from tests.unit.test_demo_trade_history_window import row

    system, _ = configure(demo_system)
    store, c, fake = system
    sig, order = enter(system)
    fake.fill(order.client_id, order.quantity)
    c.reconcile()
    c.command(uuid4(), "EMERGENCY_FLATTEN")
    c.reconcile()  # Query the completed exit; its ACK alone is not proof of flat.
    state = store.read("DEMO-test")
    closed = state.orders[str(sig.signal_id)]
    assert closed.position_state.value == "CLOSED"
    exit_order = fake.orders[closed.exit_id]
    anchor = state.last_reconciled
    stamp = int(anchor.timestamp() * 1000)
    rows = [
        row(id=1, orderId=exit_order["orderId"], time=stamp, side="SELL", qty=str(closed.filled)),
        row(id=2, orderId=999, time=stamp if same_millisecond else stamp + 10),
    ]
    c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": False})
    fake.at += timedelta(days=2)
    c.client.sync_clock()
    original = c.client.send

    def send(method, path, query, key):
        if path == "/fapi/v1/userTrades":
            assert method == "GET"
            params = {k: v[0] for k, v in parse_qs(query.get_secret_value()).items()}
            return fake.response(
                [r for r in rows if int(params["startTime"]) <= r["time"] <= int(params["endTime"])]
            )
        return original(method, path, query, key)

    c.client.send = send
    before = store.read("DEMO-test").model_dump_json()
    report = review_flat_history(store, c.client, store.read("DEMO-test"))
    assert report["last_known_full_exit_fill_found"] is True
    assert report["btc_trades_after_known_exit_before_boundary"] == (0 if same_millisecond else 1)
    assert report["other_trades_at_exit_timestamp"] == (1 if same_millisecond else 0)
    assert report["recovery_eligible"] is False
    assert store.read("DEMO-test").model_dump_json() == before
