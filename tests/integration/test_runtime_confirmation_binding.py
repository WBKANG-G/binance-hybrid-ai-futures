"""Confirmation bindings reject before the first scheduler side effect."""

# ruff: noqa: F811
import asyncio
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoCredentials
from packages.persistence.demo_order_quota import LocalQuotaStopped
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_managed_order_quota import managed  # noqa: F401
from tests.integration.test_managed_runtime_assembly import assembly  # noqa: F401
from tests.integration.test_quota_startup_review import snapshot
from tests.integration.test_runtime_confirmation_deadline import scheduler
from tests.unit.test_quota_startup_read_only import files

FAULTS = [
    "store",
    "lease",
    "client",
    "budget",
    "bridge",
    "proxy_bridge",
    "proxy_engine",
    "bridge_store",
    "journal",
    "credentials",
    "settings",
    "costs",
    "provider",
]


def change(c, fault, state):
    b = c.admission_budget
    if fault == "store":
        c.store = SimpleNamespace(renew=lambda lease: state["calls"].append("foreign_renew"))
    elif fault == "lease":
        c.lease = c.lease.model_copy(update={"fence": c.lease.fence + 1})
    elif fault == "client":
        c.client = SimpleNamespace(sync_clock=lambda: state["calls"].append("foreign_clock"))
    elif fault == "budget":
        c.admission_budget = SimpleNamespace(
            clock=lambda: 0,
            _fresh=lambda now: True,
            flat_observed=True,
            profile=b.profile,
            require_proof=lambda cost: state["calls"].append("foreign_admit"),
        )
    elif fault == "bridge":
        c.quota_bridge = object()
    elif fault == "proxy_bridge":
        c.store.bridge = object()
    elif fault == "proxy_engine":
        c.store.engine = object()
    elif fault == "bridge_store":
        c.quota_bridge.store = object()
    elif fault == "journal":
        c.quota_bridge.journal = object()
    elif fault == "credentials":
        c.client.credentials = DemoCredentials(
            SecretStr("synthetic-key"), SecretStr("synthetic-secret")
        )
    elif fault == "settings":
        c.client.settings = c.client.settings.model_copy(update={"symbols": ("ETHUSDT",)})
    elif fault == "costs":
        b.costs[("GET", "/fapi/v1/time")] = 999
    elif fault == "provider":
        b.shared.scope = "DEMO-changed"


@pytest.mark.parametrize("fault", FAULTS)
def test_changed_confirmation_context_stops_before_renewal_or_new_records(
    assembly, monkeypatch, fault
):
    session, c, state = scheduler(assembly, monkeypatch, "normal")
    before = (
        snapshot(assembly[0]),
        files(assembly[8].order_directory),
        files(session.budget.shared.directory),
    )
    change(c, fault, state)
    try:
        with pytest.raises(LocalQuotaStopped):
            asyncio.run(c.confirm_exit_budgeted())
        assert not state["calls"] and not state["sleeps"]
        assert snapshot(assembly[0]) == before[0]
        assert files(assembly[8].order_directory) == before[1]
        assert files(session.budget.shared.directory) == before[2]
    finally:
        session.close()


@pytest.mark.parametrize("stage", ["renew", "clock", "position", "cost", "proof"])
def test_drift_during_a_stage_stops_before_the_next_stage(assembly, monkeypatch, stage):
    from decimal import Decimal

    session, c, state = scheduler(assembly, monkeypatch, "normal")

    def renew(lease):
        state["calls"].append("renew")
        change(c, "settings", state)

    def clock():
        state["calls"].append("clock")
        c.quota_bridge.lease = c.lease.model_copy(update={"fence": c.lease.fence + 1})

    def position():
        state["calls"].append("position")
        c.admission_budget.flat_observed = True
        change(c, "costs", state)
        return Decimal(0), Decimal(0)

    def cost(amount):
        state["calls"].append("admit")
        change(c, "proxy_bridge", state)

    def proof():
        state["calls"].append("proof")
        change(c, "credentials", state)
        return "UNKNOWN"

    if stage == "renew":
        monkeypatch.setattr(c.store, "renew", renew)
    elif stage == "clock":
        monkeypatch.setattr(c.admission_budget, "_fresh", lambda now: False)
        monkeypatch.setattr(c.client, "sync_clock", clock)
    elif stage == "position":
        c.admission_budget.flat_observed = False
        monkeypatch.setattr(c, "_physical_position", position)
    elif stage == "cost":
        monkeypatch.setattr(c.admission_budget, "require_proof", cost)
    else:
        monkeypatch.setattr(c, "confirm_flat", proof)
    expected = {
        "renew": ["renew"],
        "clock": ["renew", "clock"],
        "position": ["renew", "position"],
        "cost": ["renew", "admit"],
        "proof": ["renew", "admit", "proof"],
    }[stage]
    try:
        with pytest.raises(LocalQuotaStopped):
            asyncio.run(c.confirm_exit_budgeted())
        assert state["calls"] == expected
        assert c.confirmation_diagnostics()["binding_rejections"] == 1
        before = list(state["calls"])
        assert asyncio.run(c.confirm_exit_budgeted()) == "UNKNOWN"
        assert state["calls"] == before
    finally:
        session.close()


def test_worker_rechecks_after_async_dispatch_before_touching_store(assembly, monkeypatch):
    from services.execution import demo_runtime_confirmation as confirmation

    session, c, state = scheduler(assembly, monkeypatch, "normal")
    original = confirmation.settled_call

    async def dispatch(function, *args):
        change(c, "credentials", state)
        return await original(function, *args)

    monkeypatch.setattr(confirmation, "settled_call", dispatch)
    try:
        with pytest.raises(LocalQuotaStopped):
            asyncio.run(c.confirm_exit_budgeted())
        assert not state["calls"]
    finally:
        session.close()


@pytest.mark.parametrize("state_kind", ["revoked", "untrusted"])
def test_known_budget_hard_stop_is_checked_before_renewal(assembly, monkeypatch, state_kind):
    from integrations.binance_usdm.demo import DemoFailure

    session, c, state = scheduler(assembly, monkeypatch, "normal")
    if state_kind == "revoked":
        session.budget.revoke()
    else:
        session.budget.invalidate_clock_observation()
    before = snapshot(assembly[0]), files(session.budget.shared.directory)
    try:
        with pytest.raises(DemoFailure):
            asyncio.run(c.confirm_exit_budgeted())
        assert not state["calls"] and snapshot(assembly[0]) == before[0]
        assert files(session.budget.shared.directory) == before[1]
    finally:
        session.close()


@pytest.mark.parametrize("enabled", [False, True])
def test_capacity_only_stop_and_normal_enabled_transition_remain_valid(
    assembly, monkeypatch, enabled
):
    session, c, state = scheduler(assembly, monkeypatch, "normal")
    c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": enabled})
    session.budget._mark("ROLLING_WEIGHT_RESERVE_REACHED")
    try:
        assert asyncio.run(c.confirm_exit_budgeted()) == "UNKNOWN"
        assert state["calls"].count("proof") == 4
        assert c.confirmation_diagnostics()["binding_rejections"] == 0
        assert c.budgeted_flat_confirmation == "UNKNOWN"
    finally:
        session.close()
