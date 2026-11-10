"""Post-assembly drift must stop before any synthetic sender invocation."""

# ruff: noqa: F811
import asyncio
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoCredentials
from integrations.binance_usdm.demo_tp_admission import TPModelProfile
from packages.domain.contracts import OrderState, PositionState, RiskState
from packages.persistence.demo_order_quota import LocalQuotaStopped
from services.execution.models import PaperOrder
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_managed_order_quota import managed  # noqa: F401
from tests.integration.test_managed_runtime_assembly import assembly, open_session  # noqa: F401
from tests.integration.test_paper import signal
from tests.integration.test_paper import snapshot as market_snapshot
from tests.integration.test_quota_startup_review import snapshot
from tests.unit.test_quota_startup_read_only import files


@pytest.mark.parametrize("change", ["live_context", "transport"])
def test_changed_start_context_stops_before_first_transport(assembly, monkeypatch, change):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    session = open_session(assembly, allow_synthetic_model_execution=True)
    clock.seconds += 70.01
    if change == "live_context":
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    else:
        session.coordinator.client.send = sender
    before, disk = snapshot(store), files(storage.order_directory)
    try:
        with pytest.raises(LocalQuotaStopped, match="RUNTIME_START_"):
            asyncio.run(session.run(uuid4()))
        assert snapshot(store) == before and files(storage.order_directory) == disk and not calls
    finally:
        session.close()


@pytest.mark.parametrize("fault", ["risk", "pause", "position", "history"])
def test_changed_local_state_is_refused_before_warmup_or_transport(assembly, monkeypatch, fault):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    session = open_session(assembly, allow_synthetic_model_execution=True)

    def change(ledger):
        if fault == "risk":
            ledger.risk_state = RiskState.EMERGENCY
        if fault == "pause":
            ledger.paused = False
        if fault == "position":
            ledger.positions["BTCUSDT"] = Decimal(".0007")

        if fault == "history":
            sig = signal(environment="DEMO", purpose="DEMO_VALIDATION", strategy=None)
            ledger.orders[str(sig.signal_id)] = PaperOrder(
                client_id="closed-history",
                signal=sig,
                snapshot=market_snapshot(),
                group="BTC",
                quantity=".0007",
                quantity_step=".0001",
                price_tick=".1",
                loss_per_unit="1",
                budget="1",
                remaining="0",
                state=OrderState.CANCELED,
                position_state=PositionState.CLOSED,
            )

    store.change(c.lease, "TEST_DRIFT", change)

    async def forbidden_wait():
        raise AssertionError("warmup ran before local rejection")

    monkeypatch.setattr(session._owned_bridge, "wait_ready", forbidden_wait)
    before, disk = snapshot(store), files(storage.order_directory)
    try:
        with pytest.raises(LocalQuotaStopped, match="RUNTIME_START_"):
            asyncio.run(session.run(uuid4()))
        assert snapshot(store) == before and files(storage.order_directory) == disk and not calls
    finally:
        session.close()


@pytest.mark.parametrize(
    "fault", ["settings", "enabled", "credentials", "coordinator", "budget", "journal"]
)
def test_changed_owned_wiring_is_refused_and_only_owned_resources_are_closed(assembly, fault):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    session = open_session(assembly, allow_synthetic_model_execution=True)
    owned_budget, owned_journal = session.budget, session.journal
    if fault == "settings":
        session.coordinator.client.settings = session.coordinator.client.settings.model_copy(
            update={"symbols": ("ETHUSDT",)}
        )
    if fault == "enabled":
        session.coordinator.client.settings = session.coordinator.client.settings.model_copy(
            update={"demo_trading_enabled": True}
        )
    if fault == "credentials":
        session.coordinator.client.credentials = DemoCredentials(
            SecretStr("different-key"), SecretStr("different-secret")
        )
    if fault == "coordinator":
        session.coordinator = SimpleNamespace()
    if fault == "budget":
        session.budget = SimpleNamespace(revoke=lambda: pytest.fail("revoked unowned budget"))
    if fault == "journal":
        session.journal = SimpleNamespace(close=lambda: pytest.fail("closed unowned journal"))
    before, disk = snapshot(store), files(storage.order_directory)
    try:
        with pytest.raises(LocalQuotaStopped, match="RUNTIME_START_"):
            asyncio.run(session.run(uuid4()))
        assert snapshot(store) == before and files(storage.order_directory) == disk and not calls
        assert not session._owned_client.settings.demo_trading_enabled
        assert session._owned_client.settings.symbols == ("BTCUSDT",)
    finally:
        session.close()
    assert owned_budget.revoked and not owned_journal.owns_process_lock()


def test_state_change_during_warmup_is_rechecked_before_enabling_client(assembly, monkeypatch):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    session = open_session(assembly, allow_synthetic_model_execution=True)
    changed = {}

    async def drift():
        store.change(
            c.lease,
            "TEST_DRIFT_DURING_WAIT",
            lambda ledger: setattr(ledger, "risk_state", RiskState.EMERGENCY),
        )
        changed["db"], changed["files"] = snapshot(store), files(storage.order_directory)

    monkeypatch.setattr(session._owned_bridge, "wait_ready", drift)
    try:
        with pytest.raises(LocalQuotaStopped, match="RUNTIME_START_RISK_CHANGED"):
            asyncio.run(session.run(uuid4()))
        assert (
            snapshot(store) == changed["db"]
            and files(storage.order_directory) == changed["files"]
            and not calls
        )
        assert not session._owned_client.settings.demo_trading_enabled
    finally:
        session.close()


@pytest.mark.parametrize("method", ["public", "signed", "listen_key"])
def test_client_transport_swap_after_start_cannot_bypass_budget(assembly, method):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    session = open_session(assembly, allow_synthetic_model_execution=True)
    client = session.coordinator.client
    client.settings = client.settings.model_copy(update={"demo_trading_enabled": True})
    client.send = sender
    before, disk = snapshot(store), files(storage.order_directory)
    try:
        with pytest.raises(LocalQuotaStopped, match="CLIENT_WIRING_CHANGED"):
            if method == "public":
                client.public("/fapi/v1/time")
            elif method == "signed":
                client.request("GET", "/fapi/v3/account")
            else:
                client.listen_key("POST")
        assert snapshot(store) == before and files(storage.order_directory) == disk and not calls
    finally:
        client.settings = session._disabled_settings
        session.close()


@pytest.mark.parametrize(
    "field", ["raw_sender", "sender", "bridge", "synthetic", "profile", "costs"]
)
def test_budget_transport_drift_is_rejected_before_reserving_weight(assembly, field):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    session = open_session(assembly, allow_synthetic_model_execution=True)
    budget = session.budget
    if field == "profile":
        budget.profile = TPModelProfile(1)
    elif field == "costs":
        budget.costs[("GET", "/fapi/v1/time")] = 0
    else:
        setattr(budget, field, False if field == "synthetic" else object())
    before, disk = snapshot(store), files(storage.order_directory)
    try:
        with pytest.raises(
            LocalQuotaStopped,
            match="MODEL_BINDING_CHANGED"
            if field in {"profile", "costs"}
            else "TRANSPORT_BINDING_CHANGED",
        ):
            budget("GET", "/fapi/v1/time", SecretStr(""), None)
        assert snapshot(store) == before and files(storage.order_directory) == disk and not calls
    finally:
        session.close()


@pytest.mark.parametrize("fault", ["credentials", "settings"])
def test_client_identity_drift_after_start_is_rejected_on_each_request(assembly, fault):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    session = open_session(assembly, allow_synthetic_model_execution=True)
    client = session.coordinator.client
    if fault == "credentials":
        client.credentials = DemoCredentials(
            SecretStr("different-key"), SecretStr("different-secret")
        )
    else:
        client.settings = client.settings.model_copy(update={"symbols": ("ETHUSDT",)})
    before, disk = snapshot(store), files(storage.order_directory)
    try:
        with pytest.raises(LocalQuotaStopped, match="RUNTIME_CLIENT_"):
            client.request("GET", "/fapi/v3/account")
        assert snapshot(store) == before and files(storage.order_directory) == disk and not calls
    finally:
        session.close()


def test_pending_shared_request_start_refusal_uses_read_only_snapshot(assembly):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    session = open_session(assembly, allow_synthetic_model_execution=True)
    clock.seconds += 70.01
    w.reserve(1000, "RESCUE")
    before, disk = snapshot(store), files(storage.order_directory)
    try:
        with pytest.raises(LocalQuotaStopped, match="PENDING_SHARED_REQUESTS"):
            asyncio.run(session.run(uuid4()))
        assert snapshot(store) == before and files(storage.order_directory) == disk and not calls
    finally:
        session.close()
