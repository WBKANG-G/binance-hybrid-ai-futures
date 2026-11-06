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
        order = SimpleNamespace(
            remaining=1, signal=SimpleNamespace(expires_at=base + timedelta(seconds=10))
        )
        account = SimpleNamespace(orders={"target": order})
        trace = []
        ledger = SimpleNamespace()

        def record(name, advance=0):
            clock[0] += advance
            trace.append((name, clock[0]))

        class Store:
            def now(self):
                return base + timedelta(seconds=clock[0])

            def read(self, account_id):
                return account

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

            async def take(self, timeout=5):
                assert timeout == (5 if path == "slow_callback" else 2)
                if path != "event":
                    record("deadline_wait", timeout)
                    raise TimeoutError
                record("event_received", 1)
                return SimpleNamespace(
                    event=lambda: {"e": "ACCOUNT_UPDATE", "E": int(base.timestamp() * 1000)},
                    receive_age_ms=0,
                    clock_wait_ms=0,
                )

        class Socket:
            async def ping(self):
                record("ping")
                assert not order.remaining
                raise ValueError("STOP_AFTER_VERIFYING_CANCEL_PRECEDES_PING")

        @asynccontextmanager
        async def connector(url):
            yield Socket()

        def connected():
            # Initial reconciliation has no delay; idle pong reconciliation does.
            record("connected", 2 if "deadline_wait" in [x[0] for x in trace] else 0)

        def event(raw):
            record("event_reconcile", 4)

        def expire():
            record("expire")
            if clock[0] >= 10 and order.remaining:
                order.remaining = 0
                record("cancel")
                if path == "event":
                    raise ValueError("STOP_AFTER_EVENT_EXPIRY")

        def callback():
            assert any(name == "reader_started" for name, _ in trace)
            record("callback_finished", 14 if path == "slow_callback" else 8)

        coordinator = SimpleNamespace(
            store=Store(),
            lease=SimpleNamespace(account_id="DEMO-test"),
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
        canceled = [stamp for name, stamp in trace if name == "cancel"]
        assert canceled == [14 if path == "slow_callback" else 13 if path == "event" else 10]
        names = [name for name, _ in trace]
        assert names.index("expire") < names.index("clock_sync", names.index("callback_finished"))
        if path != "event":
            assert names.index("cancel") < names.index("ping")
        assert names.index("cleanup_pause") > names.index("cancel")

    asyncio.run(run())


@pytest.mark.parametrize(
    "seconds,expected", [(None, 5), (-1, 0.001), (0, 0.001), (0.2, 0.2), (12, 5)]
)
def test_wait_uses_nearest_remaining_entry_only(seconds, expected):
    now = datetime(2026, 11, 3, tzinfo=UTC)
    orders = {
        "filled": SimpleNamespace(
            remaining=0, signal=SimpleNamespace(expires_at=now - timedelta(seconds=50))
        )
    }
    if seconds is not None:
        orders["entry"] = SimpleNamespace(
            remaining=1, signal=SimpleNamespace(expires_at=now + timedelta(seconds=seconds))
        )
        orders["later"] = SimpleNamespace(
            remaining=1, signal=SimpleNamespace(expires_at=now + timedelta(seconds=20))
        )
    c = SimpleNamespace(
        store=SimpleNamespace(read=lambda _: SimpleNamespace(orders=orders), now=lambda: now),
        lease=SimpleNamespace(account_id="DEMO-test"),
    )
    assert module.entry_wait_seconds(c) == expected
