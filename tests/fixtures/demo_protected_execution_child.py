"""Test-only process adapter; actual new child code, fixed loopback transports."""

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import create_engine
from websockets.asyncio.client import connect

from integrations.binance_usdm.demo import DemoCredentials, DemoSettings
from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo import DemoStore
from services.execution.demo_protected_worker_check import (
    CappedCoordinator,
    ScopedClient,
    child_run,
    control_stop,
)
from tests.fixtures.demo_protected_worker import (
    KEY,
    SECRET,
    LoopbackTransport,
    loopback_endpoint,
    test_context,
)


class Transport(LoopbackTransport):
    def __call__(self, method, path, query, api_key):
        if method == "GET" and path == "/fapi/v1/klines":
            import json

            start = int(datetime.now(UTC).timestamp()) // 60 * 60000 - 60000
            return RESTResponse(
                200,
                json.dumps(
                    [[start, "80000", "80100", "79900", "80000", "1", start + 59999]]
                ).encode(),
                {},
            )
        return super().__call__(method, path, query, api_key)


def main():
    schema, database = test_context()
    endpoint = loopback_endpoint(os.environ["TEST_PROTECTED_HTTP"], "http")
    ws = loopback_endpoint(os.environ["TEST_PROTECTED_WS"], "ws")
    owner, operation = UUID(sys.argv[1]), UUID(sys.argv[2])
    parent = os.getppid()
    engine = create_engine(database, connect_args={"options": "-csearch_path=" + schema})
    store = DemoStore(engine)
    cfg = DemoSettings.model_validate_json(os.environ["TEST_PROTECTED_CONFIG"])
    client = ScopedClient(
        cfg,
        DemoCredentials(SecretStr(KEY), SecretStr(SECRET)),
        send=Transport(endpoint),
        account="DEMO-test",
        operation=operation,
        entry_enabled=True,
    )
    client.sync_clock()
    lease = store.acquire("DEMO-test", owner)
    c = CappedCoordinator(store, lease, client)
    c.refresh_metadata()

    @asynccontextmanager
    async def connector(url):
        async with connect(ws) as socket:
            yield socket

    try:
        asyncio.run(child_run(c, operation, stop=lambda: control_stop(parent), connector=connector))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
