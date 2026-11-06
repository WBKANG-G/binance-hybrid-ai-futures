"""Real PG/synthetic venue: receipt-bound, read-only install with coherent sources."""

import importlib.util
import json
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid5

import pytest
from sqlalchemy import select

from integrations.binance_usdm.demo import DemoFailure
from packages.domain.contracts import OrderState, PositionState
from packages.persistence.demo import accounts, events
from services.execution.models import PaperOrder
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_isolated import configure
from tests.integration.test_paper import signal, snapshot

spec = importlib.util.spec_from_file_location("rate_guard", Path("guard_tp_rate_install.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture
def closed(demo_system, monkeypatch):  # noqa: F811
    (store, c, fake), _ = configure(demo_system)
    sig = signal(
        signal_id=UUID(m.INCIDENT), environment="DEMO", purpose="DEMO_VALIDATION", strategy=None
    )
    o = PaperOrder(
        client_id="DE-test",
        signal=sig,
        snapshot=snapshot(),
        group="BTC",
        quantity="0.0007",
        quantity_step="0.0001",
        price_tick="0.1",
        loss_per_unit="1",
        budget="1",
        state=OrderState.FILLED,
        position_state=PositionState.CLOSED,
        filled="0.0007",
        remaining="0",
        open_quantity="0",
        server_order_id="28668115919",
    )

    def setup(state):
        state.paused = True
        state.orders[m.INCIDENT] = o
        state.operations[str(uuid5(UUID(m.INCIDENT), "tp-trigger-observation"))] = (
            "TP_TRIGGER:NOT_ACCEPTED"
        )
        state.operations[m.RECEIPT] = json.dumps(
            {
                "incident": m.INCIDENT,
                "source": "SIGNED_OPERATOR_MANUAL_CLOSE",
                "entry_order_id": "28668115919",
                "manual_close_order_id": "28668119491",
                "trade_ids": ["554837511", "554838531"],
                "net_usdt": "-0.07032487",
            }
        )

    store.change(c.lease, "TEST_MANUAL_CLOSE_RECEIPT", setup)
    store.release(c.lease)
    c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": False})
    monkeypatch.setattr(m, "ACCOUNT", "DEMO-test")
    return store, c.client, fake


def rows(store):
    with store.engine.connect() as conn:
        return conn.execute(select(accounts)).mappings().all(), conn.execute(
            select(events)
        ).mappings().all()


def test_pre_post_guard_preserves_accounts_events_and_never_submits(closed):
    store, client, fake = closed
    before, calls = rows(store), len(fake.calls)
    for phase in ("pre", "post"):
        result = m.check(store, client, phase)
        assert (
            result["remote_empty_verified"]
            and not result["database_changed"]
            and result["order_writes"] == 0
        )
        assert result["manual_close_receipt_preserved"] and result["failed_trial_preserved"]
    assert rows(store) == before and all(method == "GET" for method, _, _ in fake.calls[calls:])


@pytest.mark.parametrize(
    "fault",
    [
        "missing_receipt",
        "wrong_fee",
        "wrong_close",
        "wrong_marker",
        "position",
        "loss",
        "leverage",
        "transfer",
        "owner",
    ],
)
def test_guard_blocks_without_database_or_order_writes(closed, fault):
    store, client, fake = closed
    if fault in {"missing_receipt", "wrong_fee", "wrong_close", "wrong_marker"}:
        from uuid import uuid4

        lease = store.acquire(m.ACCOUNT, uuid4())

        def damage(state):
            if fault == "missing_receipt":
                state.operations.pop(m.RECEIPT)
            elif fault == "wrong_marker":
                state.operations[str(uuid5(UUID(m.INCIDENT), "tp-trigger-observation"))] = (
                    "TP_TRIGGER:VERIFIED"
                )
            else:
                receipt = json.loads(state.operations[m.RECEIPT])
                receipt["net_usdt" if fault == "wrong_fee" else "manual_close_order_id"] = "bad"
                state.operations[m.RECEIPT] = json.dumps(receipt)

        store.change(lease, "TEST_BAD_SCOPE", damage)
        store.release(lease)
    elif fault == "position":
        fake.positions["BTCUSDT"] = Decimal("0.0007")
    elif fault == "loss":
        fake.wallet = Decimal("1")
    elif fault == "leverage":
        fake.leverage = "1"
    elif fault == "transfer":
        fake.incomes.append(
            {"incomeType": "TRANSFER", "asset": "USDT", "tranId": "new", "income": "1"}
        )
    else:
        from uuid import uuid4

        store.acquire(m.ACCOUNT, uuid4())
    before, calls = rows(store), len(fake.calls)
    with pytest.raises((ValueError, DemoFailure)):
        m.check(store, client, "post")
    assert rows(store) == before and all(method == "GET" for method, _, _ in fake.calls[calls:])


def test_version_maps_reject_mixed_source_sets_and_post_on_old():
    m.verify_sources("pre", dict(m.OLD_PINS))
    m.verify_sources("pre", dict(m.NEW_PINS))
    m.verify_sources("post")
    with pytest.raises(m.Blocked):
        m.verify_sources("post", dict(m.OLD_PINS))
    mixed = dict(m.NEW_PINS)
    mixed["integrations/binance_usdm/demo.py"] = m.OLD_PINS["integrations/binance_usdm/demo.py"]
    with pytest.raises(m.Blocked):
        m.verify_sources("pre", mixed)
    assert m.OLD_PINS["integrations/binance_usdm/demo_rate_limits.py"] is None
