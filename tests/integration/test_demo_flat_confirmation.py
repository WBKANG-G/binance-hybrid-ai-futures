"""Delayed close acceptance uses bounded queries, never another exit submission."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest

import services.execution.demo_validation as module
from integrations.binance_usdm.demo import DemoFailure, DemoUnknown
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_validation import case as validation_case  # noqa: F401
from tests.integration.test_demo_validation import exercise


@pytest.fixture
def case(validation_case):  # noqa: F811
    return validation_case


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(module, "FLAT_CONFIRMATION_INTERVAL_SECONDS", 0, raising=False)


def test_exchange_fill_after_first_post_ack_observation(case, monkeypatch, capsys):
    """Regression also runs against the unchanged parent implementation."""
    store, c, fake, options = case
    options["fill"] = True
    fake.defer_exit = True
    transport = c.client.send
    reads = []

    def send(method, path, query, key):
        exits = [k for k, o in fake.orders.items() if o["type"] == "MARKET"]
        if exits and path == "/fapi/v3/positionRisk":
            reads.append(path)
            if len(reads) == 3:
                fake.close(exits[0])
        return transport(method, path, query, key)

    c.client.send = send
    asyncio.run(exercise(c, uuid4(), "LONG", monkeypatch))
    assert len(reads) >= 3
    assert len([p for p in posts(fake) if p["type"] == "MARKET"]) == 1
    assert all(a["algoStatus"] != "NEW" for a in fake.algos.values())
    assert '"filled_protection_and_flat_verified": true' in capsys.readouterr().out


def delayed(case, monkeypatch, *, close_at=None, missing_fill=False):
    store, c, fake, options = case
    options["fill"] = True
    fake.defer_exit = True
    confirm = c.confirm_flat
    queries = []

    def observe():
        queries.append(len(fake.calls))
        if close_at == len(queries):
            key = next(k for k, o in fake.orders.items() if o["type"] == "MARKET")
            if missing_fill:
                fake.positions["BTCUSDT"] = 0
                fake.prices["BTCUSDT"] = 0
            else:
                fake.close(key)
        return confirm()

    monkeypatch.setattr(c, "confirm_flat", observe)
    return store, c, fake, queries


@pytest.mark.parametrize("close_at", [1, 2, 4])
def test_delayed_fill_is_confirmed_without_another_post(case, monkeypatch, capsys, close_at):
    store, c, fake, queries = delayed(case, monkeypatch, close_at=close_at)
    operation = uuid4()
    asyncio.run(exercise(c, operation, "LONG", monkeypatch))
    order = store.read("DEMO-test").orders[str(operation)]
    assert len(queries) == close_at
    assert order.position_state.value == "CLOSED" and order.open_quantity == 0
    assert order.exit_state == "FILLED"
    assert len([p for p in posts(fake) if p["type"] == "MARKET"]) == 1
    assert all(
        m == "GET" or (m == "DELETE" and p == "/fapi/v1/algoOrder")
        for m, p, _ in fake.calls[queries[0] :]
    )
    assert all(a["algoStatus"] != "NEW" for a in fake.algos.values())
    assert store.read("DEMO-test").paused
    assert store.read("DEMO-test").risk_state.value == "EMERGENCY"
    output = capsys.readouterr().out
    assert '"filled_protection_and_flat_verified": true' in output
    assert '"completion_failure": "NONE"' in output


def test_pending_exit_stops_after_bounded_queries(case, monkeypatch, capsys):
    store, c, fake, queries = delayed(case, monkeypatch)
    with pytest.raises(DemoFailure):
        asyncio.run(exercise(c, uuid4(), "LONG", monkeypatch))
    assert len(queries) == 4
    assert fake.positions["BTCUSDT"] != 0
    assert len([p for p in posts(fake) if p["type"] == "MARKET"]) == 1
    assert all(m == "GET" for m, _, _ in fake.calls[queries[0] :])
    assert '"completion_failure": "FLAT_NOT_CONFIRMED"' in capsys.readouterr().out


def test_zero_position_without_close_fill_cannot_remove_protection_or_pass(case, monkeypatch):
    store, c, fake, queries = delayed(case, monkeypatch, close_at=1, missing_fill=True)
    with pytest.raises(DemoFailure):
        asyncio.run(exercise(c, uuid4(), "LONG", monkeypatch))
    assert len(queries) == 4
    assert all(m == "GET" for m, _, _ in fake.calls[queries[0] :])
    assert all(a["algoStatus"] == "NEW" for a in fake.algos.values())
    assert all(o.open_quantity for o in store.read("DEMO-test").orders.values())


@pytest.mark.parametrize("failure", ["query", "cleanup", "lease"])
def test_completion_failures_are_sanitized_and_do_not_retry_posts(
    case, monkeypatch, capsys, failure
):
    store, c, fake, queries = delayed(case, monkeypatch, close_at=1)
    transport = c.client.send

    def send(method, path, query, key):
        if queries:
            if failure == "query":
                raise DemoFailure("SECRET-PRIVATE-URL")
            if failure == "cleanup" and method == "DELETE":
                raise DemoFailure("SECRET-PRIVATE-URL")
        return transport(method, path, query, key)

    c.client.send = send
    if failure == "lease":
        renew = store.renew

        def reject(lease):
            if any(o["type"] == "MARKET" for o in fake.orders.values()):
                fake.at += timedelta(seconds=61)
            renew(lease)

        monkeypatch.setattr(store, "renew", reject)
    with pytest.raises(DemoFailure):
        asyncio.run(exercise(c, uuid4(), "LONG", monkeypatch))
    assert len([p for p in posts(fake) if p["type"] == "MARKET"]) == 1
    output = capsys.readouterr().out
    assert "SECRET-PRIVATE-URL" not in output
    assert '"filled_protection_and_flat_verified": false' in output
    if failure == "lease":
        assert '"completion_failure": "LEASE_OR_RISK_GATE_REJECTED"' in output
    elif failure == "query":
        assert '"completion_failure": "DEMO_QUERY_OR_CLEANUP_FAILED"' in output


def test_lost_exit_reply_is_queried_not_resubmitted(case, monkeypatch, capsys):
    store, c, fake, options = case
    options["fill"] = True
    transport = c.client.send

    def send(method, path, query, key):
        response = transport(method, path, query, key)
        if (
            method == "POST"
            and path == "/fapi/v1/order"
            and any(o["type"] == "MARKET" for o in fake.orders.values())
        ):
            raise DemoUnknown("synthetic lost exit reply")
        return response

    c.client.send = send
    asyncio.run(exercise(c, uuid4(), "LONG", monkeypatch))
    assert len([p for p in posts(fake) if p["type"] == "MARKET"]) == 1
    assert '"filled_protection_and_flat_verified": true' in capsys.readouterr().out


def test_read_only_confirmation_does_not_delete_protection(case, monkeypatch):
    store, c, fake, queries = delayed(case, monkeypatch, close_at=1)
    original = c.confirm_flat

    def readonly():
        c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": False})
        return original()

    monkeypatch.setattr(c, "confirm_flat", readonly)
    with pytest.raises(DemoFailure):
        asyncio.run(exercise(c, uuid4(), "LONG", monkeypatch))
    assert all(m == "GET" for m, _, _ in fake.calls[queries[0] :])
    assert all(a["algoStatus"] == "NEW" for a in fake.algos.values())


def test_foreign_active_protection_blocks_confirmation_without_deleting_it(case, monkeypatch):
    store, c, fake, queries = delayed(case, monkeypatch, close_at=1)
    original = c.confirm_flat

    def foreign():
        if not queries:
            row = dict(next(iter(fake.algos.values())))
            row.update(clientAlgoId="foreign-protection", algoId="9999")
            fake.algos["foreign-protection"] = row
        return original()

    monkeypatch.setattr(c, "confirm_flat", foreign)
    with pytest.raises(DemoFailure):
        asyncio.run(exercise(c, uuid4(), "LONG", monkeypatch))
    assert fake.algos["foreign-protection"]["algoStatus"] == "NEW"
    assert all(m == "GET" for m, _, _ in fake.calls[queries[0] :])


def test_unpaused_or_fenced_confirmation_cannot_query_or_mutate(case):
    store, c, fake, _ = case
    store.change(c.lease, "TEST_PAUSE", lambda ledger: setattr(ledger, "paused", False))
    start = len(fake.calls)
    with pytest.raises(DemoFailure, match="paused"):
        c.confirm_flat()
    assert len(fake.calls) == start
    fake.at += timedelta(seconds=61)
    store.acquire("DEMO-test", uuid4())
    from services.risk.paper import RiskRejected

    with pytest.raises(RiskRejected):
        c.confirm_flat()
    assert len(fake.calls) == start


def test_unknown_protection_status_cannot_be_reported_flat(case, monkeypatch, capsys):
    store, c, fake, queries = delayed(case, monkeypatch, close_at=1)
    transport = c.client.send

    def send(method, path, query, key):
        response = transport(method, path, query, key)
        if queries and method == "DELETE" and path == "/fapi/v1/algoOrder":
            for row in fake.algos.values():
                row["algoStatus"] = "UNKNOWN"
        return response

    c.client.send = send
    with pytest.raises(DemoFailure):
        asyncio.run(exercise(c, uuid4(), "LONG", monkeypatch))
    assert '"filled_protection_and_flat_verified": false' in capsys.readouterr().out
    assert len([p for p in posts(fake) if p["type"] == "MARKET"]) == 1
