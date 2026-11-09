import asyncio

from integrations.binance_usdm.streams import run_reconnecting


def test_disconnect_and_bad_frame_trigger_reconcile_before_next_session():
    actions = []

    class Connection:
        def __init__(self, attempt):
            self.attempt = attempt

        async def __aenter__(self):
            actions.append("connect")
            return self

        async def __aexit__(self, *args):
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.attempt == 0:
                raise ConnectionError("synthetic disconnect")
            raise StopAsyncIteration

    async def run():
        attempts = iter([0, 1])

        async def reconcile():
            actions.append("reconcile")

        async def disconnect():
            actions.append("disconnect")

        async def consume(raw):
            actions.append("message")

        async def pause(delay):
            actions.append("backoff")

        await run_reconnecting(
            "wss://fstream.binance.com/public/stream?streams=btcusdt@depth",
            connect=lambda url: Connection(next(attempts)),
            reconcile=reconcile,
            consume=consume,
            disconnect=disconnect,
            sleep=pause,
            max_sessions=2,
        )

    asyncio.run(run())
    assert actions == [
        "connect",
        "reconcile",
        "disconnect",
        "backoff",
        "connect",
        "reconcile",
        "disconnect",
    ]


def test_cancel_waits_for_inflight_reconciliation_before_final_disconnect():
    import time
    from contextlib import asynccontextmanager

    from services.market_data.collector import collect_for

    events = []

    class Collector:
        symbol = "BTCUSDT"
        interval = "1m"

        def reconcile(self, route, now):
            events.append("reconcile_started")
            time.sleep(1.15)
            events.append("reconcile_finished")

        def consume(self, raw, now):
            pass

        def disconnect(self, route, now):
            events.append("disconnected")

    @asynccontextmanager
    async def connector(url):
        class Socket:
            def __aiter__(self):
                return self

            async def __anext__(self):
                await asyncio.Event().wait()

        yield Socket()

    asyncio.run(collect_for(Collector(), 1, connector=connector))
    assert events.index("reconcile_finished") < events.index("disconnected")
    assert events[-1] == "disconnected" and events.count("disconnected") == 2
