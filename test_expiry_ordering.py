"""Actual session control flow with synthetic clock, stores and socket only."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoFailure
from services.execution import demo_stream as module


@pytest.mark.parametrize("path", ["idle", "event", "slow_callback"])
def test_expiry_runs_after_callback_wait_and_reconciliation(monkeypatch, path):
    async def run():
        base = datetime(2026, 11, 3, tzinfo=UTC)
        clock = [0]
        trace = []
        ledger = SimpleNamespace()

        def record(name, advance=0):
            clock[0] += advance
            trace.append((name, clock[0]))

        class Store:
            def now(self):
                return base + timedelta(seconds=clock[0])

            def renew(self, lease):
                record("renew")

            def change(self, lease, kind, apply):
                record(kind)
                return apply(ledger)

        class Inbox:
            received = coalesced = 0
            failure = None

            def __init__(self, *args, **kwargs):
                pass

            async def run(self, websocket):
                record("reader_started")
                await asyncio.Future()

            def raise_if_failed(self):
                pass

            async def take(self):
                if path != "event":
                    record("five_second_idle_wait", 5)
                    raise TimeoutError
                record("event_received", 3)
                return SimpleNamespace(
                    event=lambda: {"e": "ACCOUNT_UPDATE", "E": int(base.timestamp() * 1000)},
                    receive_age_ms=0,
                    clock_wait_ms=0,
                )

        class Socket:
            async def ping(self):
                record("ping")
                future = asyncio.get_running_loop().create_future()
                future.set_result(None)
                return future

        @asynccontextmanager
        async def connector(url):
            yield Socket()

        def connected():
            # Initial reconciliation has no delay; idle pong reconciliation does.
            record("connected", 2 if "five_second_idle_wait" in [x[0] for x in trace] else 0)

        def event(raw):
            record("event_reconcile", 4)

        def expire():
            record("expire")
            raise ValueError("SYNTHETIC_STOP_AFTER_FIRST_EXPIRY_CHECK")

        def callback():
            assert any(name == "reader_started" for name, _ in trace)
            record("callback_finished", 14 if path == "slow_callback" else 10)

        coordinator = SimpleNamespace(
            store=Store(),
            lease=object(),
            connected=connected,
            event=event,
            expire=expire,
            command=lambda *args: record("cleanup_pause"),
            disconnect=lambda: record("disconnect"),
            client=SimpleNamespace(
                listen_key=lambda method: SecretStr("synthetic"),
                private_url=lambda key: SecretStr("synthetic"),
                sync_clock=lambda: record("clock_sync"),
            ),
        )
        monkeypatch.setattr(module, "EventInbox", Inbox)
        with pytest.raises(DemoFailure):
            await module.session(coordinator, connector=connector, on_ready=callback)
        at = next(stamp for name, stamp in trace if name == "expire")
        assert at == (21 if path == "slow_callback" else 17)
        names = [name for name, _ in trace]
        assert names.index("expire") > names.index("callback_finished")
        preceding = "event_reconcile" if path == "event" else "connected"
        assert max(
            i for i, name in enumerate(names[: names.index("expire")]) if name == preceding
        ) < names.index("expire")
        assert names.index("cleanup_pause") > names.index("expire")

    asyncio.run(run())
