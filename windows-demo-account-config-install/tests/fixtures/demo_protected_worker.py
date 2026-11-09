"""Test-only child process; fixed synthetic credentials and loopback transports only."""

import asyncio
import os
import re
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener
from uuid import UUID, uuid5

from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from websockets.asyncio.client import connect

from integrations.binance_usdm.demo import DemoClient, DemoCredentials, DemoSettings
from integrations.binance_usdm.rest import NoRedirect, RESTResponse
from packages.domain.contracts import Signal
from packages.persistence.demo import DemoStore
from services.execution.demo import DemoCoordinator
from services.execution.demo_stream import session
from services.risk.paper import GateSnapshot

KEY = "SYNTHETIC_PROTECTED_WORKER_KEY"
SECRET = "SYNTHETIC_PROTECTED_WORKER_SECRET"


def loopback_endpoint(value: str, scheme: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != scheme
        or parsed.hostname != "127.0.0.1"
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.port is None
    ):
        raise ValueError("TEST_LOOPBACK_ENDPOINT_REQUIRED")
    return value.rstrip("/")


class LoopbackTransport:
    def __init__(self, endpoint: str):
        self.endpoint = loopback_endpoint(endpoint, "http")
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def __call__(self, method, path, query, api_key):
        if api_key is not None and api_key.get_secret_value() != KEY:
            raise ValueError("SYNTHETIC_CREDENTIALS_ONLY")
        if not path.startswith("/fapi/") or "?" in path or "#" in path:
            raise ValueError("TEST_PATH_INVALID")
        request = Request(
            self.endpoint + path,
            data=query.get_secret_value().encode(),
            method=method,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            response = self.opener.open(request, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            body = response.read(4_194_305)
            if len(body) > 4_194_304:
                raise ValueError("TEST_RESPONSE_TOO_LARGE")
            return RESTResponse(response.status, body, {})


def test_context() -> tuple[str, str]:
    if os.getenv("TEST_ONLY_PROTECTED_WORKER") != "1" or any(
        value for name, value in os.environ.items() if name.startswith("BINANCE_")
    ):
        raise ValueError("SYNTHETIC_TEST_CONTEXT_REQUIRED")
    schema = os.environ["TEST_PROTECTED_SCHEMA"]
    if not re.fullmatch(r"phase4_protected_[a-f0-9]{32}", schema):
        raise ValueError("PRIVATE_TEST_SCHEMA_REQUIRED")
    url = make_url(os.environ["TEST_PROTECTED_DB_URL"])
    if url.drivername != "postgresql+psycopg" or url.host != "127.0.0.1":
        raise ValueError("LOOPBACK_TEST_POSTGRES_REQUIRED")
    return str(schema), url.render_as_string(hide_password=False)


def main() -> None:
    schema, database = test_context()
    http = loopback_endpoint(os.environ["TEST_PROTECTED_HTTP"], "http")
    websocket_url = loopback_endpoint(os.environ["TEST_PROTECTED_WS"], "ws")
    owner, operation = UUID(sys.argv[1]), UUID(sys.argv[2])
    config = DemoSettings.model_validate_json(os.environ["TEST_PROTECTED_CONFIG"])
    engine = create_engine(database, connect_args={"options": "-csearch_path=" + schema})
    store = DemoStore(engine)  # real UTC clock, original 60s lease
    lease = store.acquire("DEMO-test", owner)
    client = DemoClient(
        config, DemoCredentials(SecretStr(KEY), SecretStr(SECRET)), send=LoopbackTransport(http)
    )
    client.sync_clock()
    coordinator = DemoCoordinator(store, lease, client)
    coordinator.refresh_metadata()

    def ready() -> None:
        coordinator.resume_entries(uuid5(operation, "resume"))
        now = datetime.now(UTC)
        ledger = store.read("DEMO-test")
        signal = Signal(
            signal_id=operation,
            environment="DEMO",
            symbol="BTCUSDT",
            strategy=None,
            purpose="DEMO_VALIDATION",
            direction="LONG",
            signal_closed_candle_at=now.replace(second=0, microsecond=0) - timedelta(minutes=1),
            expires_at=now + timedelta(seconds=10),
            suggested_limit_price="80000",
            initial_stop_price="79600",
            target_price="80800",
            risk_profile_version=config.policy.risk.version,
        )
        gate = GateSnapshot(
            market_at=now,
            account_at=ledger.last_reconciled,
            correlation_at=ledger.last_reconciled,
            bid="80000",
            ask="80000.1",
            mark="80000",
            correlation_group="SYNTHETIC_SINGLE_BTC",
            evaluation="RULES_ONLY",
        )
        from decimal import Decimal

        coordinator.reserve(signal, gate, quantity_cap=Decimal("0.0007"))
        coordinator.submit(str(operation))
        current = store.read("DEMO-test")
        order = current.orders[str(operation)]
        if (
            not order.protected
            or order.remaining
            or not order.open_quantity
            or not current.validation_evidence[str(operation)].get("stop_verified")
            or not current.validation_evidence[str(operation)].get("target_verified")
        ):
            raise ValueError("PROTECTED_READY_NOT_VERIFIED")
        coordinator.command(uuid5(operation, "pause-new"), "PAUSE_ENTRIES")
        store.change(
            lease,
            "TEST_PROTECTED_WORKER_READY",
            lambda state: state.operations.__setitem__(str(operation), "PROTECTED_WORKER_READY"),
        )

    @asynccontextmanager
    async def connector(url):
        async with connect(websocket_url) as socket:
            yield socket

    try:
        asyncio.run(session(coordinator, connector=connector, on_ready=ready))
    finally:
        # SIGKILL must bypass this marker; the parent verifies that it is absent.
        store.change(
            lease,
            "TEST_PROTECTED_WORKER_FINALLY",
            lambda state: state.operations.__setitem__(
                str(uuid5(operation, "finally")), "FINALLY_RAN"
            ),
        )
        store.release(lease)
        engine.dispose()


if __name__ == "__main__":
    main()
