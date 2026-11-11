"""A flat resource wait must read current ownership before spending more credit."""

# ruff: noqa: F811
from datetime import timedelta
from uuid import uuid4

import pytest

from integrations.binance_usdm.demo_tp_window_budget import ProofDeferred
from packages.persistence.demo_order_quota import LocalQuotaStopped
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_managed_order_quota import managed  # noqa: F401
from tests.integration.test_managed_runtime_assembly import assembly  # noqa: F401
from tests.integration.test_quota_startup_review import snapshot
from tests.integration.test_runtime_observation_budget import observing  # noqa: F401
from tests.unit.test_quota_startup_read_only import files


@pytest.mark.parametrize("loss", ["handoff", "expiry"])
@pytest.mark.parametrize("when", ["before_wait", "during_wait", "credit_return"])
def test_current_lease_loss_blocks_observation_without_new_effects(
    observing, monkeypatch, loss, when
):
    a, s, c, b, calls, resumed, watches = observing
    store = a[0]
    storage = a[8]
    changed = {}
    checks = []

    def lose():
        if loss == "handoff":
            store.release(c.lease)
            changed["successor"] = store.acquire(c.lease.account_id, uuid4())
        else:
            expired = store.now() + timedelta(seconds=61)
            monkeypatch.setattr(store, "now", lambda: expired)
        changed["before"] = (
            snapshot(store),
            files(storage.order_directory),
            files(storage.shared_directory),
        )
        calls.clear()
        checks.clear()
        resumed.clear()
        watches.clear()

    def credit(cost):
        checks.append(cost)
        if when == "credit_return":
            lose()
        elif when == "during_wait" and len(checks) == 1:
            raise ProofDeferred

    monkeypatch.setattr(b, "require_proof", credit)
    if when == "before_wait":
        lose()
        a[3].seconds += 6  # Old code emits a fresh clock GET before noticing expired ownership.
    elif when == "during_wait":
        c._observation_sleep = lambda delay: lose()
    with pytest.raises(LocalQuotaStopped, match="CURRENT_LEASE"):
        c.connected()
    assert not calls and not resumed and not watches and not checks
    assert changed["before"] == (
        snapshot(store),
        files(storage.order_directory),
        files(storage.shared_directory),
    )
    assert c.budgeted_flat_confirmation == "UNKNOWN"


@pytest.mark.parametrize(
    "shape", ["flat", "foreign", "long", "short", "missing", "duplicate", "hedge", "nonfinite"]
)
def test_original_global_watch_refuses_new_or_ambiguous_positions_without_orders(
    assembly, monkeypatch, shape
):
    import json
    from decimal import Decimal

    from integrations.binance_usdm.demo import DemoFailure
    from integrations.binance_usdm.rest import RESTResponse
    from packages.domain.contracts import OrderState, PositionState
    from services.execution.demo_runtime_confirmation import RuntimeConfirmationCoordinator
    from services.execution.models import PaperOrder
    from tests.integration.test_paper import signal
    from tests.integration.test_paper import snapshot as gate_snapshot
    from tests.integration.test_runtime_header_provenance import prepared

    btc = dict(symbol="BTCUSDT", positionSide="BOTH", positionAmt="0", entryPrice="0")
    eth = dict(symbol="ETHUSDT", positionSide="BOTH", positionAmt="0", entryPrice="0")
    rows = [btc, eth]
    if shape == "foreign":
        eth["positionAmt"] = "1"
    elif shape == "long":
        btc["positionAmt"] = ".0001"
    elif shape == "short":
        btc["positionAmt"] = "-.0001"
    elif shape == "missing":
        rows = [eth]
    elif shape == "duplicate":
        rows = [btc, dict(btc), eth]
    elif shape == "hedge":
        btc["positionSide"] = "LONG"
    elif shape == "nonfinite":
        btc["positionAmt"] = "NaN"
    calls = []

    def send(method, path, query, key):
        calls.append((method, path))
        return RESTResponse(
            200,
            json.dumps(
                rows if path.endswith("/positionRisk") else {"serverTime": 1000000}
            ).encode(),
            {"x-mbx-used-weight-1m": "600"},
        )

    session, b = prepared(assembly, send)
    c = session.coordinator
    c.client.clock_ms = lambda: 1000000
    c.client.sync_clock()
    flow = uuid4()

    def seed(ledger):
        ledger.paused = False
        ledger.orders[str(flow)] = PaperOrder(
            client_id="DE-" + flow.hex[:30],
            signal=signal(
                signal_id=flow, environment="DEMO", purpose="DEMO_VALIDATION", strategy=None
            ),
            snapshot=gate_snapshot(),
            group="BTC",
            quantity=".0007",
            quantity_step=".0001",
            price_tick=".1",
            loss_per_unit="1",
            budget="1",
            remaining="0",
            state=OrderState.FILLED,
            position_state=PositionState.CLOSED,
        )

    assembly[0].change(c.lease, "TEST_CLOSED_OBSERVATION", seed)
    c._own_operation = flow
    b.flat_observed = True
    b.owned_entry_id = "DE-" + flow.hex[:30]
    b.owned_quantity = Decimal(".0007")
    b.owned_side = "BUY"
    c._observation_clock = assembly[3].mono
    c._observation_sleep = lambda delay: setattr(
        assembly[3], "seconds", assembly[3].seconds + delay
    )
    resumed = []
    count = [0]
    monkeypatch.setattr(
        RuntimeConfirmationCoordinator, "connected", lambda self: resumed.append(True)
    )

    def credit(cost):
        count[0] += 1
        if count[0] == 1:
            raise ProofDeferred

    monkeypatch.setattr(b, "require_proof", credit)
    calls.clear()
    charged = b.total_charge
    before = assembly[0].read(c.lease.account_id)
    try:
        if shape == "flat":
            c.connected()
            assert resumed == [True]
        else:
            with pytest.raises(DemoFailure):
                c.connected()
            assert not resumed and b.flat_observed is False
        assert calls == [("GET", "/fapi/v1/time"), ("GET", "/fapi/v3/positionRisk")]
        assert b.total_charge == charged + 6 and not b.untrusted
        after = assembly[0].read(c.lease.account_id)
        before.paused = True
        assert after == before and after.paused
        assert c.budgeted_flat_confirmation == "UNKNOWN"
    finally:
        session.close()
