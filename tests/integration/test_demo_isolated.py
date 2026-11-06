"""Isolated margin contracts with real PostgreSQL and synthetic DEMO responses."""

import json
from decimal import Decimal as D
from uuid import uuid4

import pytest

from integrations.binance_usdm.demo import DemoFailure, DemoSettings
from integrations.binance_usdm.demo_snapshot import snapshot as remote_snapshot
from services.execution.demo import DemoCoordinator
from services.risk.paper import RiskRejected
from tests.integration.test_demo import enter, posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_paper import signal, snapshot


@pytest.fixture
def system(demo_system):  # noqa: F811
    return demo_system


def configure(system):
    store, old, fake = system
    controls = {"auto": "false", "mode": "ISOLATED", "liquidation": "50", "wallet": None}

    def send(method, path, query, api_key):
        response = fake(method, path, query, api_key)
        data = json.loads(response.body)
        if path == "/fapi/v3/account" and controls.get("available") is not None:
            data["availableBalance"] = controls["available"]
        if path == "/fapi/v1/symbolConfig":
            for row in data:
                if row["symbol"] == "BTCUSDT":
                    row["marginType"] = controls["mode"]
                    row["isAutoAddMargin"] = controls["auto"]
        if path == "/fapi/v3/positionRisk":
            for row in data:
                row["marginType"] = "isolated"
                wallet = abs(D(row["positionAmt"])) * D(row["entryPrice"]) / 2
                row["isolatedWallet"] = (
                    str(wallet) if controls["wallet"] is None else controls["wallet"]
                )
                row["isolatedMargin"] = str(wallet)
                row["liquidationPrice"] = controls["liquidation"]
        return fake.response(data, response.status)

    def bind(ledger):
        ledger.margin_mode = "ISOLATED"
        ledger.symbols = ("BTCUSDT",)

    store.change(old.lease, "TEST_ISOLATED_NEW_LEDGER", bind)
    old.client.settings = DemoSettings.model_validate(
        old.client.settings.model_dump() | {"margin_mode": "ISOLATED", "symbols": ["BTCUSDT"]}
    )
    old.client.send = send
    c = DemoCoordinator(store, old.lease, old.client)
    c.refresh_metadata()
    c.connected()
    return (store, c, fake), controls


def test_isolated_fill_protection_restart_and_flatten(system):
    isolated, _ = configure(system)
    store, c, fake = isolated
    sig, order = enter(isolated)
    fake.fill(order.client_id, order.quantity)
    c.reconcile()
    current = store.read("DEMO-test").orders[str(sig.signal_id)]
    assert current.protected and current.tp_algo_id
    assert len(posts(fake, "/fapi/v1/algoOrder")) == 2
    remote = remote_snapshot(c.client, store.read("DEMO-test"), store.now())
    assert remote.isolated_wallets["BTCUSDT"] > 0
    assert remote.margin_used >= remote.isolated_wallets["BTCUSDT"]
    assert remote.equity == fake.wallet  # No double counting of isolated collateral.
    restart = DemoCoordinator(store, c.lease, c.client)
    restart.refresh_metadata()
    restart.connected()
    assert len(posts(fake, "/fapi/v1/algoOrder")) == 2
    restart.command(uuid4(), "EMERGENCY_FLATTEN")
    assert fake.positions["BTCUSDT"] == 0
    assert all(p["type"] == "LIMIT" or p.get("reduceOnly") == "true" for p in posts(fake))


@pytest.mark.parametrize("value", [True, "true", None, 0])
def test_auto_add_margin_must_be_verified_disabled(system, value):
    isolated, controls = configure(system)
    store, c, fake = isolated
    controls["auto"] = value
    with pytest.raises(DemoFailure, match="automatic margin addition disabled"):
        remote_snapshot(c.client, store.read("DEMO-test"), store.now())
    assert not posts(fake)


def test_isolated_mode_change_is_not_accepted(system):
    isolated, controls = configure(system)
    store, c, fake = isolated
    controls["mode"] = "CROSSED"
    with pytest.raises(DemoFailure, match="margin mode does not match"):
        remote_snapshot(c.client, store.read("DEMO-test"), store.now())
    assert not posts(fake)


def test_scope_blocks_other_symbol_reservations_and_mutations(system):
    isolated, _ = configure(system)
    _, c, fake = isolated
    with pytest.raises(RiskRejected, match="outside configured"):
        c.reserve(signal(symbol="ETHUSDT", environment="DEMO"), snapshot())
    with pytest.raises(DemoFailure, match="outside configured"):
        c.client.request("POST", "/fapi/v1/order", {"symbol": "ETHUSDT", "type": "LIMIT"})
    assert not posts(fake)


def test_existing_cross_ledger_cannot_silently_switch_to_isolated(system):
    store, c, _ = system
    c.client.settings = DemoSettings.model_validate(
        c.client.settings.model_dump() | {"margin_mode": "ISOLATED", "symbols": ["BTCUSDT"]}
    )
    with pytest.raises(RiskRejected, match="immutable margin"):
        DemoCoordinator(store, c.lease, c.client)


def test_isolated_does_not_treat_locked_wallet_as_available_balance(system):
    isolated, controls = configure(system)
    store, c, fake = isolated
    controls["available"] = "0"
    c.reconcile()
    assert store.read("DEMO-test").value.equity == fake.wallet
    assert store.read("DEMO-test").available_balance == 0
    with pytest.raises(RiskRejected, match="reservation/filter/leverage/exposure cap"):
        c.reserve(signal(environment="DEMO"), snapshot())
    assert not posts(fake)


def test_stop_beyond_liquidation_triggers_risk_reduction(system):
    isolated, controls = configure(system)
    store, c, fake = isolated
    sig, order = enter(isolated)
    fake.fill(order.client_id, order.quantity)
    controls["liquidation"] = "101"
    c.reconcile()
    assert fake.positions["BTCUSDT"] == 0
    assert any(p.get("reduceOnly") == "true" for p in posts(fake))


def test_isolated_scope_requires_explicit_symbols(system):
    _, c, _ = system
    with pytest.raises(ValueError, match="explicit unique"):
        DemoSettings.model_validate(
            c.client.settings.model_dump() | {"margin_mode": "ISOLATED", "symbols": []}
        )


def test_isolated_leverage_cap_is_checked_even_when_flat(system):
    isolated, _ = configure(system)
    store, c, fake = isolated
    fake.leverage = "20"
    with pytest.raises(DemoFailure, match="leverage exceeds"):
        remote_snapshot(c.client, store.read("DEMO-test"), store.now())
    assert not posts(fake)


def test_changed_margin_mode_between_reservation_and_submit_sends_no_order(system):
    isolated, controls = configure(system)
    store, c, fake = isolated
    sig = signal(environment="DEMO")
    c.reserve(sig, snapshot())
    controls["mode"] = "CROSSED"
    with pytest.raises(DemoFailure):
        c.submit(str(sig.signal_id))
    assert not posts(fake)


def test_isolated_rejects_other_symbol_exposure(system):
    isolated, _ = configure(system)
    store, c, fake = isolated
    fake.positions["ETHUSDT"] = D("1")
    fake.prices["ETHUSDT"] = D("100")
    with pytest.raises(DemoFailure, match="outside configured symbol scope"):
        remote_snapshot(c.client, store.read("DEMO-test"), store.now())
    assert not posts(fake)


def test_filled_isolated_position_requires_positive_verified_collateral(system):
    isolated, controls = configure(system)
    store, c, fake = isolated
    sig, order = enter(isolated)
    fake.fill(order.client_id, order.quantity)
    controls["wallet"] = "0"
    with pytest.raises(DemoFailure, match="Invalid/incomplete DEMO snapshot"):
        remote_snapshot(c.client, store.read("DEMO-test"), store.now())
    assert len(posts(fake)) == 1


@pytest.mark.parametrize(
    "scenario",
    [
        "test_demo_lost_response_is_queried_without_resubmit",
        "test_partial_fill_immediately_has_separate_verified_stop_and_tp",
        "test_cancel_timeout_keeps_budget_and_late_fill_gets_protection",
        "test_stop_failure_blocks_and_attempts_reduce_only_exit",
        "test_tp_fill_verified_flat_before_leftover_stop_removed",
        "test_emergency_ack_not_flat_and_duplicate_operation_no_second_exit",
        "test_restart_fences_old_leader_and_recovers_pending_order",
    ],
)
def test_isolated_runs_existing_fault_acceptance_scenarios(system, scenario):
    from tests.integration import test_demo

    isolated, _ = configure(system)
    getattr(test_demo, scenario)(isolated)


def test_scoped_algo_cleanup_cannot_cancel_other_symbol(system):
    isolated, _ = configure(system)
    _, c, fake = isolated
    fake.algos["foreign-algo"] = {"symbol": "ETHUSDT", "algoStatus": "NEW"}
    before = len(fake.calls)
    with pytest.raises(DemoFailure, match="outside configured scope"):
        c.client.request("DELETE", "/fapi/v1/algoOrder", {"clientAlgoId": "foreign-algo"})
    assert all(method == "GET" for method, path, params in fake.calls[before:])
    assert fake.algos["foreign-algo"]["algoStatus"] == "NEW"
