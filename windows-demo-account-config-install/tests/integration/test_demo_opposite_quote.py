"""Opposite-quote LIMIT validation preserves caps, idempotency and cancellation."""

import asyncio
from decimal import Decimal as D
from uuid import uuid4

import pytest

from integrations.binance_usdm.demo import DemoFailure
from services.execution.demo_preview import preview
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_validation import case as validation_case  # noqa: F401
from tests.integration.test_demo_validation import exercise


@pytest.fixture
def quote_context(preview_case):  # noqa: F811
    return preview_case


@pytest.fixture
def order_context(validation_case):  # noqa: F811
    return validation_case


def test_read_only_opposite_quote_uses_exact_ask_for_buy_and_bid_for_sell(quote_context):
    store, c, fake, _, _ = quote_context
    before = len(fake.calls)
    report = preview(c.client, store.read("DEMO-test"), entry_style="OPPOSITE_QUOTE")
    long, short = report["illustrations"]
    assert D(long["limit_price"]) == D(report["ask"])
    assert D(short["limit_price"]) == D(report["bid"])
    assert all(
        r["entry_type"] == "LIMIT" and r["quantity"] == r["execution_quantity_cap"]
        for r in (long, short)
    )
    assert all(method == "GET" for method, path, params in fake.calls[before:])


def test_quote_not_on_tick_is_rejected_without_padding(quote_context):
    store, c, _, _, market = quote_context
    market["ask"] = "100.11"
    with pytest.raises(ValueError, match="PRICE_FILTER"):
        preview(c.client, store.read("DEMO-test"), entry_style="OPPOSITE_QUOTE")


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_opposite_quote_sends_one_limit_with_exact_bound(order_context, direction, monkeypatch):
    store, c, fake, _ = order_context
    operation = uuid4()
    asyncio.run(exercise(c, operation, direction, monkeypatch, entry_style="OPPOSITE_QUOTE"))
    entry = posts(fake)
    assert len(entry) == 1 and entry[0]["type"] == "LIMIT"
    assert D(entry[0]["price"]) == D("100.1" if direction == "LONG" else "100")
    order = store.read("DEMO-test").orders[str(operation)]
    assert (
        D(entry[0]["quantity"])
        == order.quantity
        == store.read("DEMO-test").entry_quantity_caps[str(operation)]
    )
    assert order.remaining == 0 and order.filled == 0
    assert store.read("DEMO-test").validation_entry_styles[str(operation)] == "OPPOSITE_QUOTE"


def test_changing_style_cannot_reuse_prior_passive_operation(order_context, monkeypatch):
    store, c, fake, _ = order_context
    operation = uuid4()
    asyncio.run(exercise(c, operation, "LONG", monkeypatch))
    before = len(fake.calls)
    with pytest.raises(DemoFailure, match="operation ID conflict"):
        asyncio.run(exercise(c, operation, "LONG", monkeypatch, entry_style="OPPOSITE_QUOTE"))
    assert len(fake.calls) == before


def test_opposite_quote_recovery_does_not_chase_or_resubmit(order_context, monkeypatch):
    store, c, fake, _ = order_context
    operation = uuid4()
    asyncio.run(exercise(c, operation, "LONG", monkeypatch, entry_style="OPPOSITE_QUOTE"))
    asyncio.run(exercise(c, operation, "LONG", monkeypatch, entry_style="OPPOSITE_QUOTE"))
    assert len(posts(fake)) == 1


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_opposite_quote_fill_keeps_protection_and_verified_flat(
    order_context, direction, monkeypatch, capsys
):
    store, c, fake, options = order_context
    options["fill"] = True
    if direction == "SHORT":
        options["margin_controls"]["liquidation"] = "150"
    operation = uuid4()
    asyncio.run(exercise(c, operation, direction, monkeypatch, entry_style="OPPOSITE_QUOTE"))
    entry = [p for p in posts(fake) if p["type"] == "LIMIT"]
    assert len(entry) == 1
    assert D(entry[0]["price"]) == D("100.1" if direction == "LONG" else "100")
    assert fake.positions["BTCUSDT"] == 0
    assert store.read("DEMO-test").validation_evidence[str(operation)] == {
        "stop_verified": True,
        "target_verified": True,
    }
    assert all(p["type"] == "LIMIT" or p.get("reduceOnly") == "true" for p in posts(fake))
    assert '"filled_protection_and_flat_verified": true' in capsys.readouterr().out


def test_reservation_style_is_immutable_for_same_signal(order_context):
    import services.execution.demo_validation as validation
    from services.risk.paper import RiskRejected

    store, c, _, _ = order_context
    sig, gate, cap = validation.prepare(c, uuid4(), "LONG", entry_style="OPPOSITE_QUOTE")
    c.reserve(sig, gate, quantity_cap=cap, entry_style="OPPOSITE_QUOTE")
    with pytest.raises(RiskRejected, match="entry style"):
        c.reserve(sig, gate, quantity_cap=cap, entry_style="PASSIVE")


def test_missing_collateral_field_after_fill_uses_emergency_reduction(order_context):
    import json

    from tests.integration.test_paper import signal, snapshot

    store, c, fake, options = order_context
    candidate = signal(environment="DEMO")
    order = c.reserve(candidate, snapshot(), quantity_cap=D("0.05"))
    c.submit(str(candidate.signal_id))
    fake.fill(order.client_id, order.quantity)
    send = c.client.send

    def missing_field(method, path, query, key):
        response = send(method, path, query, key)
        if path == "/fapi/v3/positionRisk":
            data = json.loads(response.body)
            for row in data:
                row.pop("isolatedMargin", None)
            return fake.response(data)
        return response

    c.client.send = missing_field
    with pytest.raises(DemoFailure, match="Invalid/incomplete DEMO snapshot"):
        c.reconcile()
    exits = [p for p in posts(fake) if p["type"] == "MARKET"]
    assert len(exits) == 1 and exits[0]["reduceOnly"] == "true"
    assert fake.positions["BTCUSDT"] == 0
