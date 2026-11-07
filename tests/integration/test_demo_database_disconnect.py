"""Real PostgreSQL backend termination, synthetic exchange and local WSS only.

Only the backend PID of this test's schema-scoped engine is terminated. Never
stops a server/container or touches the user's DEMO account, keys or exports.
"""

import asyncio
from contextlib import asynccontextmanager, contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import DBAPIError
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from integrations.binance_usdm.demo import DemoClient, DemoFailure
from packages.domain.contracts import OrderState
from packages.persistence.demo import events, outbox
from services.execution.demo import DemoCoordinator
from services.execution.demo_stream import session
from services.risk.paper import RiskRejected
from tests.integration.test_demo import enter, posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_isolated import configure
from tests.integration.test_paper import signal, snapshot


@pytest.fixture
def case(demo_system):  # noqa: F811
    configured, _ = configure(demo_system)
    return configured


def counts(store):
    with store.engine.connect() as conn:
        return tuple(conn.scalar(select(func.count()).select_from(t)) for t in (events, outbox))


def kinds(store, kind):
    with store.engine.connect() as conn:
        return conn.scalar(select(func.count()).select_from(events).where(events.c.kind == kind))


@contextmanager
def terminate_effect_backend(store, kind):
    """Terminate the connection after state UPDATE, before event/outbox/COMMIT.

    This callback sends no fake DB exception. The real next PostgreSQL operation
    fails because pg_terminate_backend(pid, 2000) has waited for that backend exit.
    """
    admin = create_engine(store.engine.url, pool_size=1, max_overflow=0)
    cut = {"observed": False, "pid": None}

    def before(conn, cursor, statement, parameters, context, executemany):
        if (
            not cut["observed"]
            and "INSERT INTO demo_events" in statement
            and isinstance(parameters, dict)
            and parameters.get("kind") == kind
        ):
            pid = conn.connection.driver_connection.info.backend_pid
            with admin.connect() as control:
                assert control.scalar(text("SELECT pg_backend_pid()")) != pid
                assert control.scalar(text("SELECT pg_terminate_backend(:pid, 2000)"), {"pid": pid})
            cut.update(observed=True, pid=pid)

    event.listen(store.engine, "before_cursor_execute", before)
    try:
        yield cut
    finally:
        event.remove(store.engine, "before_cursor_execute", before)
        admin.dispose()
    assert cut["observed"], "the real connection cut must actually occur"


@pytest.mark.parametrize("phase", ["reservation", "intent"])
def test_db_connection_cut_before_intent_commit_never_sends_entry_and_rolls_back(case, phase):
    store, c, fake = case
    sig = signal(environment="DEMO")
    if phase == "intent":
        order = c.reserve(sig, snapshot())
    before, before_counts = store.read("DEMO-test"), counts(store)
    target = "DEMO_ENTRY_RESERVED" if phase == "reservation" else "DEMO_ENTRY_INTENT"
    with terminate_effect_backend(store, target):
        with pytest.raises(DBAPIError) as error:
            if phase == "reservation":
                c.reserve(sig, snapshot())
            else:
                c.submit(str(sig.signal_id))
        assert error.value.connection_invalidated
    assert store.read("DEMO-test") == before
    assert counts(store) == before_counts
    assert not posts(fake)
    if phase == "intent":
        assert store.read("DEMO-test").orders[str(sig.signal_id)].state == OrderState.RISK_RESERVED
        assert order.client_id not in store.read("DEMO-test").requests
    else:
        assert not store.read("DEMO-test").orders


async def one_local_session(c, ready=None):
    async def handler(socket):
        await socket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        @asynccontextmanager
        async def connector(url):
            async with connect(f"ws://127.0.0.1:{port}") as socket:
                yield socket

        await session(c, connector=connector, on_ready=ready, max_messages=0)


def test_ack_then_actual_db_disconnect_preserves_submitting_and_recovers_same_id(case):
    store, c, fake = case
    sig = signal(environment="DEMO")
    order = c.reserve(sig, snapshot())
    before_counts = counts(store)
    effect_count = kinds(store, "DEMO_ENTRY_EFFECT")
    with terminate_effect_backend(store, "DEMO_ENTRY_EFFECT"):
        with pytest.raises(DBAPIError) as error:
            c.submit(str(sig.signal_id))
        assert error.value.connection_invalidated
    durable = store.read("DEMO-test")
    assert durable.orders[str(sig.signal_id)].state == OrderState.SUBMITTING
    assert durable.requests[order.client_id] == "SUBMITTING"
    assert counts(store) == tuple(n + 1 for n in before_counts)  # intent only
    assert kinds(store, "DEMO_ENTRY_EFFECT") == effect_count
    assert len(posts(fake)) == 1 and fake.orders[order.client_id]["status"] == "NEW"
    c.submit(str(sig.signal_id))
    assert len(posts(fake)) == 1
    with pytest.raises(RiskRejected, match="already active"):
        store.acquire("DEMO-test", uuid4())

    # Logical clock advancement is explicit; this is not a natural 60s wait.
    fake.at += timedelta(seconds=61)
    lease = store.acquire("DEMO-test", uuid4())
    assert lease.fence == c.lease.fence + 1
    blocked = []
    for action in (
        lambda: store.renew(c.lease),
        lambda: c.reconcile(),
        lambda: store.change(c.lease, "STALE_EFFECT", lambda ledger: blocked.append(True)),
    ):
        with pytest.raises(RiskRejected, match="expired or fenced"):
            action()
    assert not blocked and len(posts(fake)) == 1
    client = DemoClient(
        c.client.settings, c.client.credentials, send=c.client.send, clock_ms=c.client.clock_ms
    )
    client.sync_clock()
    successor = DemoCoordinator(store, lease, client)
    successor.refresh_metadata()
    with pytest.raises(RiskRejected, match="gate locked"):
        successor.reserve(signal(environment="DEMO"), snapshot())

    def recovered():
        ledger = store.read("DEMO-test")
        recovered_order = ledger.orders[str(sig.signal_id)]
        assert ledger.reconciled and ledger.stream_ready
        assert recovered_order.client_id == order.client_id
        assert recovered_order.server_order_id == str(fake.orders[order.client_id]["orderId"])
        assert recovered_order.state == OrderState.ACKNOWLEDGED
        assert ledger.requests[order.client_id] == "SUBMITTING"  # original evidence retained
        successor.submit(str(sig.signal_id))
        assert len(posts(fake)) == 1

    asyncio.run(one_local_session(successor, recovered))
    after = store.read("DEMO-test")
    assert after.paused and not after.stream_ready and not after.reconciled
    assert after.orders[str(sig.signal_id)].state == OrderState.CANCELED
    assert len(posts(fake)) == 1 and not any(fake.positions.values())
    assert any(
        method == "GET"
        and path == "/fapi/v1/order"
        and params.get("origClientOrderId") == order.client_id
        for method, path, params in fake.calls
    )
    store.release(lease)


def test_real_db_loss_in_protected_session_locks_entries_and_preserves_exchange_stop(case):
    store, c, fake = case
    armed = None
    key = None
    heartbeat_count = None

    async def exercise():
        nonlocal armed, key, heartbeat_count

        async def handler(socket):
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            @asynccontextmanager
            async def connector(url):
                async with connect(f"ws://127.0.0.1:{port}") as socket:
                    yield socket

            def ready():
                nonlocal armed, key, heartbeat_count
                sig, order = enter(case)
                key = str(sig.signal_id)
                fake.fill(order.client_id, order.quantity)
                c.reconcile()
                protected = store.read("DEMO-test").orders[key]
                assert protected.protected and protected.tp_algo_id
                heartbeat_count = kinds(store, "HEARTBEAT")
                armed = terminate_effect_backend(store, "HEARTBEAT")
                armed.__enter__()

            try:
                with pytest.raises(DemoFailure, match="entries blocked"):
                    await asyncio.wait_for(
                        session(c, connector=connector, on_ready=ready), timeout=10
                    )
            finally:
                if armed is not None:
                    armed.__exit__(None, None, None)

    asyncio.run(exercise())
    assert armed is not None and key is not None
    after = store.read("DEMO-test")
    order = after.orders[key]
    assert c.session_failed and after.paused and not after.reconciled and not after.stream_ready
    assert after.last_private_session_failure is not None
    assert after.last_private_session_failure.code == "OTHER_DATABASE"  # pg termination is 57P01
    assert kinds(store, "HEARTBEAT") == heartbeat_count
    assert order.protected and order.open_quantity > 0
    assert fake.algos[order.algo_id]["algoStatus"] == "NEW"
    assert fake.algos[order.tp_algo_id]["algoStatus"] == "NEW"
    assert len(posts(fake)) == 1 and len(posts(fake, "/fapi/v1/algoOrder")) == 2
    start = len(fake.calls)
    with pytest.raises(RiskRejected, match="gate locked"):
        c.reserve(signal(environment="DEMO"), snapshot())
    assert len(fake.calls) == start
    with pytest.raises(RiskRejected, match="already active"):
        store.acquire("DEMO-test", uuid4())

    fake.at += timedelta(seconds=61)
    lease = store.acquire("DEMO-test", uuid4())
    assert lease.fence == c.lease.fence + 1
    start = len(fake.calls)
    with pytest.raises(RiskRejected):
        store.change(
            c.lease,
            "STALE_STOP_DELETE",
            lambda ledger: c.client.request(
                "DELETE", "/fapi/v1/algoOrder", {"clientAlgoId": order.algo_id}
            ),
        )
    with pytest.raises(RiskRejected):
        store.renew(c.lease)
    assert len(fake.calls) == start and fake.algos[order.algo_id]["algoStatus"] == "NEW"
    client = DemoClient(
        c.client.settings, c.client.credentials, send=c.client.send, clock_ms=c.client.clock_ms
    )
    client.sync_clock()
    successor = DemoCoordinator(store, lease, client)
    successor.refresh_metadata()
    with pytest.raises(RiskRejected, match="gate locked"):
        successor.reserve(signal(environment="DEMO"), snapshot())

    def recovered():
        ledger = store.read("DEMO-test")
        assert ledger.reconciled and ledger.stream_ready and ledger.paused
        assert ledger.orders[key].protected
        assert ledger.orders[key].algo_id == order.algo_id
        assert ledger.orders[key].tp_algo_id == order.tp_algo_id
        assert len(posts(fake)) == 1 and len(posts(fake, "/fapi/v1/algoOrder")) == 2

    asyncio.run(one_local_session(successor, recovered))
    assert fake.algos[order.algo_id]["algoStatus"] == "NEW"
    # Synthetic cleanup after proof; no authenticated exchange is used anywhere.
    successor.command(uuid4(), "EMERGENCY_FLATTEN")
    assert successor.confirm_flat() == "VERIFIED_FLAT"
    assert not any(fake.positions.values())
    store.release(lease)
