"""Fenced PostgreSQL anchors plus independent durable journal commits."""

# ruff: noqa: F811
from decimal import Decimal
from uuid import uuid4

import pytest

from packages.domain.contracts import OrderState
from packages.persistence.demo_order_quota import (
    RESTART_HOLD_SECONDS,
    DurableOrderQuota,
    LocalQuotaStopped,
)
from services.execution.demo_managed_order_quota import (
    RESERVE_PREFIX,
    ManagedQuotaBridge,
    ManagedQuotaStore,
    roles_for,
)
from services.execution.models import PaperOrder
from services.risk.paper import RiskRejected
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_paper import signal, snapshot
from tests.unit.test_durable_order_quota import QuotaClock


@pytest.fixture
def managed(demo_system, tmp_path):
    store, c, fake = demo_system
    store.change(c.lease, "TEST_QUOTA_PAUSE", lambda ledger: setattr(ledger, "paused", True))
    clock = QuotaClock()
    q = ManagedQuotaBridge.bootstrap(
        store, c.lease, tmp_path, monotonic=clock.mono, wall_clock=clock.utc
    )
    bridge = ManagedQuotaBridge(store, c.lease, q)
    clock.seconds = RESTART_HOLD_SECONDS
    proxy = ManagedQuotaStore(store, bridge)
    yield store, c, bridge, proxy, q, clock, tmp_path
    q.close()


def reserve_entry(managed):
    store, c, bridge, proxy, q, clock, path = managed
    flow = uuid4()
    bridge.reserve(flow)
    ids = roles_for(c.lease.account_id, flow)

    def intent(ledger):
        ledger.orders[str(flow)] = PaperOrder(
            client_id=ids["ENTRY"],
            signal=signal(
                signal_id=flow, environment="DEMO", purpose="DEMO_VALIDATION", strategy=None
            ),
            snapshot=snapshot(),
            group="BTC",
            quantity=".0007",
            quantity_step=".0001",
            price_tick=".1",
            loss_per_unit="1",
            budget="1",
            remaining=".0007",
            state=OrderState.SUBMITTING,
        )
        ledger.requests[ids["ENTRY"]] = "SUBMITTING"

    store.change(c.lease, "TEST_PRECOMMITTED_ENTRY_INTENT", intent)
    params = {
        "newClientOrderId": [ids["ENTRY"]],
        "type": ["LIMIT"],
        "symbol": ["BTCUSDT"],
        "quantity": [".0007"],
    }
    return flow, ids, params


def test_effect_rollback_cannot_refund_a_possible_send(managed):
    store, c, bridge, proxy, q, clock, path = managed
    flow, ids, params = reserve_entry(managed)
    before = store.read(c.lease.account_id)
    sent = []

    def effect(ledger):
        bridge.authorize("POST", "/fapi/v1/order", params)
        sent.append(ids["ENTRY"])
        raise RuntimeError("SYNTHETIC_PG_EFFECT_ROLLBACK_AFTER_SEND")

    with pytest.raises(RuntimeError):
        proxy.change(c.lease, "DEMO_ENTRY_EFFECT", effect)
    assert store.read(c.lease.account_id) == before
    assert q.flow_records()[str(flow)]["claims"].keys() == {"ENTRY"}
    with pytest.raises(LocalQuotaStopped, match="DUPLICATE"):
        proxy.change(c.lease, "DEMO_ENTRY_EFFECT", effect)
    assert sent == [ids["ENTRY"]] and q.view()["slots_60s"] == 6


def test_expired_or_wrong_fence_never_consumes_or_sends(managed):
    store, c, bridge, proxy, q, clock, path = managed
    flow, ids, params = reserve_entry(managed)
    wrong = c.lease.model_copy(update={"fence": c.lease.fence + 1})
    called = []
    with pytest.raises(RiskRejected):
        proxy.change(
            wrong,
            "DEMO_ENTRY_EFFECT",
            lambda ledger: called.append(bridge.authorize("POST", "/fapi/v1/order", params)),
        )
    assert not called and q.flow_records()[str(flow)]["claims"] == {}


def test_no_post_outside_original_fenced_effect_context(managed):
    store, c, bridge, proxy, q, clock, path = managed
    flow, ids, params = reserve_entry(managed)
    with pytest.raises(LocalQuotaStopped, match="FENCED_EFFECT"):
        bridge.authorize("POST", "/fapi/v1/order", params)
    with pytest.raises(LocalQuotaStopped, match="ROLE_MISMATCH"):
        proxy.change(
            c.lease,
            "DEMO_SL_EFFECT",
            lambda ledger: bridge.authorize("POST", "/fapi/v1/order", params),
        )
    assert q.flow_records()[str(flow)]["claims"] == {}


def test_missing_storage_cannot_be_recreated_while_pg_binding_exists(managed):
    store, c, bridge, proxy, q, clock, path = managed
    q.close()
    q.path.unlink()
    with pytest.raises(LocalQuotaStopped, match="ALREADY_BOUND"):
        ManagedQuotaBridge.bootstrap(store, c.lease, path)
    assert not q.path.exists()


def test_rolled_back_file_cannot_discard_pg_anchored_reservation(managed):
    store, c, bridge, proxy, q, clock, path = managed
    snapshot_bytes = q.path.read_bytes()
    flow = uuid4()
    bridge.reserve(flow)
    q.close()
    q.path.write_bytes(snapshot_bytes)
    restored = DurableOrderQuota(
        path, c.lease.account_id, q.journal_id, monotonic=clock.mono, wall_clock=clock.utc
    )
    try:
        with pytest.raises(LocalQuotaStopped, match="FLOW_MISMATCH"):
            ManagedQuotaBridge(store, c.lease, restored)
        assert RESERVE_PREFIX + str(flow) in store.read(c.lease.account_id).operations
    finally:
        restored.close()


def test_seal_without_pg_commit_blocks_next_session(managed):
    store, c, bridge, proxy, q, clock, path = managed
    flow = uuid4()
    bridge.reserve(flow)
    q.seal(flow)
    q.close()
    restored = DurableOrderQuota(
        path, c.lease.account_id, q.journal_id, monotonic=clock.mono, wall_clock=clock.utc
    )
    try:
        with pytest.raises(LocalQuotaStopped, match="SEAL_MISMATCH"):
            ManagedQuotaBridge(store, c.lease, restored)
    finally:
        restored.close()


def test_original_reserved_exit_can_use_one_fallback_without_journal(managed):
    store, c, bridge, proxy, q, clock, path = managed
    flow, ids, params = reserve_entry(managed)

    def stage(ledger):
        order = ledger.orders[str(flow)]
        order.open_quantity = Decimal(".0007")
        order.exit_id = ids["EXIT"]
        order.exit_state = "SUBMITTING"
        ledger.requests[ids["EXIT"]] = "SUBMITTING"

    store.change(c.lease, "TEST_PRECOMMITTED_EXIT", stage)
    q.path.unlink()
    exit_params = {
        "newClientOrderId": [ids["EXIT"]],
        "reduceOnly": ["true"],
        "type": ["MARKET"],
        "symbol": ["BTCUSDT"],
        "quantity": [".0007"],
    }
    proxy.change(
        c.lease,
        "DEMO_REDUCE_ONLY_EFFECT",
        lambda ledger: bridge.authorize("POST", "/fapi/v1/order", exit_params),
    )
    assert bridge.exit_fallbacks == 1 and bridge.degraded
    with pytest.raises(LocalQuotaStopped, match="DUPLICATE"):
        proxy.change(
            c.lease,
            "DEMO_REDUCE_ONLY_EFFECT",
            lambda ledger: bridge.authorize("POST", "/fapi/v1/order", exit_params),
        )
    with pytest.raises(LocalQuotaStopped):
        proxy.change(
            c.lease,
            "DEMO_ENTRY_EFFECT",
            lambda ledger: bridge.authorize("POST", "/fapi/v1/order", params),
        )


def simulated_full_proof_state(managed):
    from packages.domain.contracts import PositionState

    store, c, bridge, proxy, q, clock, path = managed
    flow, ids, params = reserve_entry(managed)

    def full(ledger):
        o = ledger.orders[str(flow)]
        o.state = OrderState.FILLED
        o.filled = o.quantity
        o.remaining = Decimal(0)
        o.open_quantity = Decimal(0)
        o.position_state = PositionState.CLOSED
        ledger.paused = True
        ledger.reconciled = True
        ledger.positions = {}
        ledger.requests[ids["ENTRY"]] = "FILLED"

    store.change(c.lease, "TEST_SYNTHETIC_FULL_PROOF_STATE", full)
    return flow, store.read(c.lease.account_id)


def test_seal_is_idempotent_without_restarting_slot_expiry(managed):
    store, c, bridge, proxy, q, clock, path = managed
    flow, before = simulated_full_proof_state(managed)
    bridge.seal_verified(before)
    first = q.flow_records()[str(flow)]["seal"]
    clock.seconds += 1
    bridge.seal_verified(store.read(c.lease.account_id))
    assert q.flow_records()[str(flow)]["seal"] == first and q.view()["slots_60s"] == 6


def test_sql_rollback_after_journal_seal_requires_review_not_new_credit(managed, monkeypatch):
    store, c, bridge, proxy, q, clock, path = managed
    flow, before = simulated_full_proof_state(managed)
    native = store.change

    def fail_after_seal(lease, kind, apply):
        if kind != "DEMO_MANAGED_QUOTA_FULL_PROOF_SEALED":
            return native(lease, kind, apply)

        def callback(ledger):
            apply(ledger)
            raise RuntimeError("SYNTHETIC_SEAL_PG_COMMIT_FAILURE")

        return native(lease, kind, callback)

    monkeypatch.setattr(store, "change", fail_after_seal)
    with pytest.raises(RuntimeError):
        bridge.seal_verified(before)
    assert q.flow_records()[str(flow)]["seal"] is not None
    with pytest.raises(LocalQuotaStopped, match="SEAL_MISMATCH"):
        bridge.reserve(uuid4())


def test_changed_local_proof_cannot_release_flow(managed):
    store, c, bridge, proxy, q, clock, path = managed
    flow, before = simulated_full_proof_state(managed)
    store.change(
        c.lease,
        "TEST_AFTER_PROOF_CHANGE",
        lambda ledger: ledger.operations.__setitem__("SYNTHETIC_CHANGE", "CHANGED"),
    )
    with pytest.raises(LocalQuotaStopped, match="FULL_PROOF_CHANGED"):
        bridge.seal_verified(before)
    assert q.flow_records()[str(flow)]["seal"] is None and q.view()["slots_60s"] == 6


def test_local_denial_happens_before_weight_transport_and_keeps_rescue_trust(managed, monkeypatch):
    from pydantic import SecretStr

    from integrations.binance_usdm.demo_tp_admission import TPModelProfile
    from integrations.binance_usdm.rest import RESTResponse
    from services.execution.demo_managed_order_quota import ManagedOrderWindowBudget
    from tests.fixtures.tp_admission_synthetic_exchange import COSTS

    store, c, bridge, proxy, q, clock, path = managed
    flow, ids, params = reserve_entry(managed)
    calls = []

    def send(*args):
        calls.append(args[0:2])
        return RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "1"})

    b = ManagedOrderWindowBudget(
        send, TPModelProfile(0), COSTS, bridge=bridge, allow_synthetic_model_execution=True
    )
    b("GET", "/fapi/v1/time", SecretStr(""), None)
    b.configure(
        {
            "rateLimits": [
                {
                    "rateLimitType": "REQUEST_WEIGHT",
                    "interval": "MINUTE",
                    "intervalNum": 1,
                    "limit": 6000,
                },
                {"rateLimitType": "ORDERS", "interval": "SECOND", "intervalNum": 10, "limit": 300},
                {"rateLimitType": "ORDERS", "interval": "MINUTE", "intervalNum": 1, "limit": 1200},
            ]
        }
    )
    b.preflight()
    b.before_preparation()
    b.admit_entry()
    b.owned_entry_id = ids["ENTRY"]
    b.owned_quantity = Decimal(".0007")
    b.owned_side = "BUY"

    def broken(*args):
        raise OSError("SYNTHETIC_STORAGE_FAILURE")

    monkeypatch.setattr(q, "claim", broken)
    query = SecretStr(
        "newClientOrderId=" + ids["ENTRY"] + "&type=LIMIT&symbol=BTCUSDT&side=BUY&quantity=.0007"
    )
    with pytest.raises(LocalQuotaStopped):
        proxy.change(
            c.lease,
            "DEMO_ENTRY_EFFECT",
            lambda ledger: b("POST", "/fapi/v1/order", query, SecretStr("synthetic-key")),
        )
    assert calls == [("GET", "/fapi/v1/time")] and not b.entry_sent and not b.untrusted
    assert q.view()["slots_60s"] == 6
