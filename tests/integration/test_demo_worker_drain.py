"""Real PG row-lock races caused by cancellation of a running to_thread callback."""

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

import services.execution.demo_stream as stream
import services.execution.demo_validation as validation
from integrations.binance_usdm.demo import DemoFailure
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_validation import case as base_worker_case  # noqa: F401
from tests.integration.test_demo_validation import exercise


@pytest.fixture
def validation_case(base_worker_case):  # noqa: F811
    return base_worker_case


def hold_callback(store, coordinator, monkeypatch, started, release):
    original = store.change
    errors = []

    def change(*args):
        try:
            return original(*args)
        except SQLAlchemyError as error:
            errors.append(getattr(getattr(error, "orig", None), "sqlstate", None))
            raise

    def event(raw):
        def held(ledger):
            started.set()
            assert release.wait(12), "test must release its real PostgreSQL row lock"

        store.change(coordinator.lease, "TEST_IN_FLIGHT_EVENT", held)

    monkeypatch.setattr(store, "change", change)
    monkeypatch.setattr(coordinator, "event", event)
    return errors


def test_cancelled_session_waits_for_row_lock_worker_before_pause(validation_case, monkeypatch):
    store, c, fake, _ = validation_case
    store.change(c.lease, "TEST_UNPAUSE", lambda ledger: setattr(ledger, "paused", False))
    started, release = threading.Event(), threading.Event()
    errors = hold_callback(store, c, monkeypatch, started, release)

    async def run():
        async def handler(socket):
            await socket.send(
                json.dumps({"e": "ACCOUNT_UPDATE", "E": int(fake.at.timestamp() * 1000)})
            )
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            @asynccontextmanager
            async def connector(url):
                async with connect(f"ws://127.0.0.1:{port}") as socket:
                    yield socket

            task = asyncio.create_task(stream.session(c, connector=connector))
            try:
                assert await asyncio.to_thread(started.wait, 5)
                task.cancel()
                asyncio.get_running_loop().call_later(2.4, release.set)
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                release.set()

    asyncio.run(run())
    assert errors == []
    assert store.read("DEMO-test").paused
    assert not store.read("DEMO-test").stream_ready


def test_validation_cancel_during_db_event_finishes_without_lock_timeout(
    validation_case, monkeypatch, capsys
):
    store, c, fake, options = validation_case
    options["fill"] = True
    started, release = threading.Event(), threading.Event()
    errors = hold_callback(store, c, monkeypatch, started, release)

    async def run():
        ready = asyncio.Event()
        loop = asyncio.get_running_loop()

        async def handler(socket):
            await ready.wait()
            await socket.send(
                json.dumps({"e": "ACCOUNT_UPDATE", "E": int(fake.at.timestamp() * 1000)})
            )
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            @asynccontextmanager
            async def connector(url):
                async with connect(f"ws://127.0.0.1:{port}") as socket:
                    yield socket

            async def local_session(coordinator, *, on_ready):
                def callback():
                    on_ready()
                    loop.call_soon_threadsafe(ready.set)

                await stream.session(coordinator, connector=connector, on_ready=callback)

            monkeypatch.setattr(validation, "session", local_session)

            async def unlock():
                assert await asyncio.to_thread(started.wait, 5)
                await asyncio.sleep(7)
                release.set()

            unlock_task = asyncio.create_task(unlock())
            try:
                await validation.validate_once(c, uuid4(), "LONG", observation_seconds=0.2)
            finally:
                release.set()
                await unlock_task

    asyncio.run(run())
    assert errors == []
    assert len(posts(fake)) == 2
    assert fake.positions["BTCUSDT"] == 0
    output = capsys.readouterr().out
    assert '"filled_protection_and_flat_verified": true' in output
    assert '"completion_database_failure": "NONE"' in output


@pytest.mark.parametrize("worker_fails", [False, True])
def test_repeated_cancellation_drains_worker_without_exposing_failure(worker_fails):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def work():
        started.set()
        assert release.wait(5)
        finished.set()
        if worker_fails:
            raise RuntimeError("SECRET-SIGNATURE-AND-URL")
        return 17

    async def run():
        task = asyncio.create_task(stream.settled_call(work))
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()

    asyncio.run(run())


def test_uncancelled_worker_result_and_error_are_preserved():
    async def run():
        assert await stream.settled_call(lambda: 17) == 17

        def fail():
            raise ValueError("synthetic failure")

        with pytest.raises(ValueError, match="synthetic"):
            await stream.settled_call(fail)

    asyncio.run(run())


@pytest.mark.parametrize(
    "state,expected",
    [
        ("55P03", "DATABASE_LOCK_TIMEOUT"),
        ("40P01", "DATABASE_DEADLOCK"),
        ("08006", "DATABASE_CONNECTION_FAILED"),
        ("PRIVATE", "OTHER_DATABASE_FAILURE"),
    ],
)
def test_database_classification_never_contains_sql_or_private_parameters(state, expected):
    error = OperationalError("SECRET-SQL", {"key": "SECRET-KEY"}, SimpleNamespace(sqlstate=state))
    assert validation.database_failure(error) == expected
    assert validation.safe_failure(error) == expected


def cli_setup(case, monkeypatch):
    import scripts.run_demo as cli

    store, c, fake, _ = case
    monkeypatch.setenv("BINANCE_DEMO_API_KEY", "synthetic-key")
    monkeypatch.setenv("BINANCE_DEMO_API_SECRET", "synthetic-secret")
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: store.engine)
    monkeypatch.setattr(cli, "DemoStore", lambda engine: store)
    monkeypatch.setattr(cli.yaml, "load", lambda *a, **k: c.client.settings.model_dump())

    def client(settings, credentials):
        c.client.settings = settings
        return c.client

    monkeypatch.setattr(cli, "DemoClient", client)
    monkeypatch.setattr(
        "sys.argv",
        [
            "review",
            "--config",
            "configs/demo-isolated.example.yaml",
            "--account",
            "DEMO-test",
            "--enable-demo",
            "--confirm-flat",
        ],
    )
    return cli


def test_confirm_flat_cli_updates_stale_local_ack_without_order_posts(
    validation_case, monkeypatch, capsys
):
    store, c, fake, options = validation_case
    options["fill"] = True
    operation = uuid4()
    asyncio.run(exercise(c, operation, "LONG", monkeypatch))

    def stale(ledger):
        order = ledger.orders[str(operation)]
        from packages.domain.contracts import PositionState

        order.position_state, order.open_quantity = PositionState.EXIT_PENDING, order.filled
        order.exit_state = "ACKNOWLEDGED"
        ledger.requests[order.exit_id] = "ACKNOWLEDGED"

    store.change(c.lease, "TEST_STALE_LOCAL_EXIT_ACK", stale)
    store.release(c.lease)
    cli = cli_setup(validation_case, monkeypatch)
    count = len(posts(fake))
    cli.main()
    assert len(posts(fake)) == count
    assert store.read("DEMO-test").orders[str(operation)].open_quantity == 0
    assert store.read("DEMO-test").orders[str(operation)].exit_state == "FILLED"
    assert store.read("DEMO-test").paused
    assert '"completion_confirmation": "VERIFIED_FLAT"' in capsys.readouterr().out


def test_confirm_flat_cli_cannot_close_an_open_position_or_submit_any_order(
    validation_case, monkeypatch, capsys
):
    store, c, fake, options = validation_case
    options["fill"], fake.defer_exit = True, True
    monkeypatch.setattr(validation, "FLAT_CONFIRMATION_INTERVAL_SECONDS", 0)
    with pytest.raises(DemoFailure):
        asyncio.run(exercise(c, uuid4(), "LONG", monkeypatch))
    store.release(c.lease)
    cli = cli_setup(validation_case, monkeypatch)
    count = len(posts(fake))
    with pytest.raises(SystemExit):
        cli.main()
    assert len(posts(fake)) == count and fake.positions["BTCUSDT"] != 0
    assert '"completion_confirmation": "UNKNOWN"' in capsys.readouterr().out


@pytest.mark.parametrize(
    "extra", [[], ["--enable-demo", "--watch"], ["--enable-demo", "--operation-id", str(uuid4())]]
)
def test_confirm_flat_requires_explicit_scope_and_cannot_mix_actions(monkeypatch, extra):
    import scripts.run_demo as cli

    def no_credentials():
        raise AssertionError("invalid args must not load credentials")

    monkeypatch.setattr(cli.DemoCredentials, "from_environment", no_credentials)
    monkeypatch.setattr(
        "sys.argv",
        [
            "review",
            "--config",
            "configs/demo-isolated.example.yaml",
            "--account",
            "DEMO-test",
            "--confirm-flat",
            *extra,
        ],
    )
    with pytest.raises(SystemExit):
        cli.main()


def test_confirm_cli_reports_only_allowlisted_database_reason(validation_case, monkeypatch):
    store, c, fake, _ = validation_case
    cli = cli_setup(validation_case, monkeypatch)

    def fail(*args):
        raise OperationalError(
            "SECRET-SQL", {"key": "SECRET-KEY"}, SimpleNamespace(sqlstate="55P03")
        )

    monkeypatch.setattr(store, "acquire", fail)
    with pytest.raises(SystemExit) as error:
        cli.main()
    message = str(error.value)
    assert "DATABASE_LOCK_TIMEOUT" in message
    assert "SECRET" not in message and "55P03" not in message
