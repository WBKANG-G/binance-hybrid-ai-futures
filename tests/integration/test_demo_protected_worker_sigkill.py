"""Actual child SIGKILL and natural 60s lease; shared synthetic venue survives it."""

import asyncio
import json
import os
import signal as process_signal
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4, uuid5

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import create_engine, select, text
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from integrations.binance_usdm.demo import DemoClient, DemoCredentials
from packages.domain.contracts import Signal
from packages.persistence.demo import DemoStore, accounts
from services.execution.demo import DemoCoordinator
from services.execution.demo_models import DemoLedger
from services.execution.demo_stream import session
from services.execution.models import Lease
from services.risk.paper import AccountValue, GateSnapshot, RiskRejected, period_keys
from tests.fixtures.demo import FakeDemo
from tests.fixtures.demo_protected_worker import KEY, SECRET, LoopbackTransport
from tests.integration.test_demo import settings

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="isolated PG required")


@pytest.fixture
def case():
    url = os.environ["TEST_DATABASE_URL"]
    schema = "phase4_protected_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": "-csearch_path=" + schema})
    with engine.begin() as conn:
        cfg = Config("alembic.ini")
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    config = settings().model_copy(update={"margin_mode": "ISOLATED", "symbols": ("BTCUSDT",)})
    store = DemoStore(engine)  # unmodified real UTC clock
    now = store.now()
    day, week = period_keys(now, 0)
    store.create(
        "DEMO-test",
        DemoLedger(
            policy=config.policy,
            margin_mode="ISOLATED",
            symbols=("BTCUSDT",),
            value=AccountValue(as_of=now, initial="1"),
            daily_base="1",
            weekly_base="1",
            high_water="1",
            daily_key=day,
            weekly_key=week,
            paused=True,
        ),
    )
    fake = FakeDemo(now)
    fake.positions["BTCUSDT"] = Decimal(0)
    fake.prices["BTCUSDT"] = Decimal(0)
    lock = threading.RLock()
    errors = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # never log signed request bodies or URLs

        def handle_request(self):
            from urllib.parse import parse_qs

            body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
            params = {k: v[0] for k, v in parse_qs(body).items()}
            with lock:
                try:
                    fake.at = datetime.now(UTC)
                    response = fake(self.command, self.path, SecretStr(body), SecretStr(KEY))
                    data = json.loads(response.body)
                    if self.path == "/fapi/v1/exchangeInfo":
                        for f in data["symbols"][0]["filters"]:
                            if f["filterType"] == "LOT_SIZE":
                                f.update(minQty="0.0001", stepSize="0.0001")
                            if f["filterType"] == "MIN_NOTIONAL":
                                f["notional"] = "50"
                    if self.path == "/fapi/v1/ticker/bookTicker":
                        data.update(
                            symbol="BTCUSDT",
                            time=int(fake.at.timestamp() * 1000),
                            bidPrice="80000",
                            askPrice="80000.1",
                        )
                    if self.path == "/fapi/v1/premiumIndex":
                        data.update(
                            symbol="BTCUSDT",
                            time=int(fake.at.timestamp() * 1000),
                            markPrice="80000",
                        )
                    if self.path == "/fapi/v1/symbolConfig":
                        for row in data:
                            row.update(marginType="ISOLATED", isAutoAddMargin="false")
                    if self.path == "/fapi/v3/positionRisk":
                        for row in data:
                            margin = (
                                abs(Decimal(row["positionAmt"])) * Decimal(row["entryPrice"]) / 2
                            )
                            row.update(
                                marginType="isolated",
                                isolatedWallet=str(margin),
                                isolatedMargin=str(margin),
                                liquidationPrice="40000",
                            )
                    if (
                        self.command == "POST"
                        and self.path == "/fapi/v1/order"
                        and params.get("type") == "LIMIT"
                    ):
                        fake.fill(
                            params["newClientOrderId"],
                            Decimal(params["quantity"]),
                            Decimal("80000"),
                        )
                    payload = json.dumps(data).encode()
                    self.send_response(response.status)
                except Exception:
                    errors.append("SYNTHETIC_HANDLER_FAILED")
                    payload = b'{"code":-1007}'
                    self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        do_GET = do_POST = do_DELETE = do_PUT = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    try:
        yield store, config, fake, endpoint, schema
        assert not errors
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def ownership(store):
    with store.engine.connect() as conn:
        return (
            conn.execute(select(accounts).where(accounts.c.account_id == "DEMO-test"))
            .mappings()
            .one()
        )


def fresh_signal(config):
    now = datetime.now(UTC)
    signal = Signal(
        signal_id=uuid4(),
        environment="DEMO",
        symbol="BTCUSDT",
        direction="LONG",
        strategy=None,
        purpose="DEMO_VALIDATION",
        signal_closed_candle_at=now.replace(second=0, microsecond=0) - timedelta(minutes=1),
        expires_at=now + timedelta(seconds=10),
        suggested_limit_price="80000",
        initial_stop_price="79600",
        target_price="80800",
        risk_profile_version=config.policy.risk.version,
    )
    gate = GateSnapshot(
        market_at=now,
        account_at=now,
        correlation_at=now,
        bid="80000",
        ask="80000.1",
        mark="80000",
        correlation_group="SYNTHETIC_SINGLE_BTC",
        evaluation="RULES_ONLY",
    )
    return signal, gate


async def exercise(case):
    store, config, fake, endpoint, schema = case
    owner, operation = uuid4(), uuid4()
    child = None
    connections = 0

    async def handler(socket):
        nonlocal connections
        connections += 1
        await socket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        ws = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        environment = {k: v for k, v in os.environ.items() if not k.startswith("BINANCE_")}
        environment.update(
            TEST_ONLY_PROTECTED_WORKER="1",
            TEST_PROTECTED_DB_URL=store.engine.url.render_as_string(hide_password=False),
            TEST_PROTECTED_SCHEMA=schema,
            TEST_PROTECTED_CONFIG=config.model_dump_json(),
            TEST_PROTECTED_HTTP=endpoint,
            TEST_PROTECTED_WS=ws,
            PYTHONPATH=os.getcwd(),
        )
        try:
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "tests.fixtures.demo_protected_worker",
                    str(owner),
                    str(operation),
                ],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise AssertionError(
                        "worker exited before protected readiness: " + child.stderr.read().decode()
                    )
                current = store.read("DEMO-test")
                if current.operations.get(str(operation)) == "PROTECTED_WORKER_READY":
                    break
                await asyncio.sleep(0.02)
            else:
                raise AssertionError("protected worker did not become ready")
            order = current.orders[str(operation)]
            assert order.filled == order.quantity == Decimal("0.0007")
            assert not order.remaining and order.protected and current.paused
            assert current.validation_evidence[str(operation)] == {
                "stop_verified": True,
                "target_verified": True,
            }
            before = ownership(store)
            old_lease = Lease(account_id="DEMO-test", owner=owner, fence=before["fence"])
            observer = DemoClient(
                config.model_copy(update={"demo_trading_enabled": False}),
                DemoCredentials(SecretStr(KEY), SecretStr(SECRET)),
                send=LoopbackTransport(endpoint),
            )

            def protections_present():
                observer.sync_clock()
                for cid, kind in [
                    (order.algo_id, "STOP_MARKET"),
                    (order.tp_algo_id, "TAKE_PROFIT_MARKET"),
                ]:
                    row = observer.request("GET", "/fapi/v1/algoOrder", {"clientAlgoId": cid})
                    assert row["clientAlgoId"] == cid and row["algoStatus"] == "NEW"
                    assert row["orderType"] == kind and row["closePosition"] is True
                positions = observer.request("GET", "/fapi/v3/positionRisk")
                assert positions[0]["positionAmt"] == str(order.filled)

            protections_present()
            child.kill()
            assert await asyncio.to_thread(child.wait, 5) == -process_signal.SIGKILL
            assert not child.stderr.read(), "worker must not emit private diagnostics"
            after_kill = ownership(store)
            assert after_kill["owner"] == str(owner) and after_kill["fence"] == before["fence"]
            assert str(uuid5(operation, "finally")) not in store.read("DEMO-test").operations
            with pytest.raises(RiskRejected, match="already active"):
                store.acquire("DEMO-test", uuid4())
            protections_present()
            remaining = (after_kill["lease_until"] - store.now()).total_seconds()
            assert remaining > 0
            started = time.monotonic()
            deadline = started + 75
            probes = 1
            while store.now() < after_kill["lease_until"]:
                assert time.monotonic() < deadline, "natural expiry timeout"
                await asyncio.sleep(
                    min(2, max(0.02, (after_kill["lease_until"] - store.now()).total_seconds()))
                )
                protections_present()
                probes += 1
            elapsed = time.monotonic() - started
            assert elapsed + 0.25 >= remaining
            assert ownership(store)["lease_until"] == after_kill["lease_until"]
            lease = store.acquire("DEMO-test", uuid4())
            assert lease.fence == old_lease.fence + 1
            effects = []
            with pytest.raises(RiskRejected):
                store.change(old_lease, "STALE_DELETE", lambda ledger: effects.append(True))
            with pytest.raises(RiskRejected):
                store.renew(old_lease)
            assert not effects
            new_client = DemoClient(
                config,
                DemoCredentials(SecretStr(KEY), SecretStr(SECRET)),
                send=LoopbackTransport(endpoint),
            )
            new_client.sync_clock()
            successor = DemoCoordinator(store, lease, new_client)
            successor.refresh_metadata()
            untrusted = store.read("DEMO-test")
            assert not untrusted.stream_ready and not untrusted.reconciled
            with pytest.raises(RiskRejected, match="gate locked"):
                successor._fresh(untrusted.model_copy(update={"paused": False}, deep=True))
            sig, gate = fresh_signal(config)
            with pytest.raises(RiskRejected):
                successor.reserve(sig, gate, quantity_cap=Decimal("0.0007"))

            @asynccontextmanager
            async def connector(url):
                async with connect(ws) as socket:
                    yield socket

            def recovered():
                ledger = store.read("DEMO-test")
                restored = ledger.orders[str(operation)]
                assert ledger.stream_ready and ledger.reconciled and ledger.paused
                assert restored.protected and restored.algo_id == order.algo_id
                assert (
                    restored.tp_algo_id == order.tp_algo_id
                    and restored.client_id == order.client_id
                )
                assert restored.server_order_id == order.server_order_id
                assert (
                    restored.filled == order.filled
                    and restored.open_quantity == order.open_quantity
                )
                successor.submit(str(operation))  # queried existing FILLED intent, never new POST

            await session(successor, connector=connector, on_ready=recovered, max_messages=0)
            assert connections == 2
            entry_posts = [
                p for m, path, p in fake.calls if m == "POST" and path == "/fapi/v1/order"
            ]
            protection_posts = [
                p for m, path, p in fake.calls if m == "POST" and path == "/fapi/v1/algoOrder"
            ]
            assert len(entry_posts) == 1 and len(protection_posts) == 2
            protections_present()
            stable = store.read("DEMO-test")
            for field in [
                "daily_key",
                "daily_base",
                "weekly_key",
                "weekly_base",
                "net_transfers",
                "policy",
            ]:
                assert getattr(stable, field) == getattr(current, field)
            assert stable.high_water >= current.high_water
            successor.command(uuid4(), "EMERGENCY_FLATTEN")
            assert successor.confirm_flat() == "VERIFIED_FLAT"
            store.release(lease)
            assert not any(fake.positions.values())
            return {
                "scope": "ACTUAL_PROTECTED_CHILD_SIGKILL_REAL_PG_NATURAL_LEASE_LOCAL_VENUE",
                "child_exit_code": child.returncode,
                "finally_marker_absent": True,
                "natural_wait_ms": int(elapsed * 1000),
                "remaining_at_wait_start_ms": int(remaining * 1000),
                "lease_until_unchanged": True,
                "protection_probe_count": probes,
                "fence_before": old_lease.fence,
                "fence_after": lease.fence,
                "stale_effect_and_renew_blocked": True,
                "wss_connections": connections,
                "entry_posts_before_cleanup": len(entry_posts),
                "protection_posts": len(protection_posts),
                "same_entry_and_protection_ids_recovered": True,
                "final_flat": "VERIFIED_FLAT",
                "real_exchange_calls": 0,
            }
        finally:
            if child is not None:
                if child.poll() is None:
                    child.kill()
                    await asyncio.to_thread(child.wait, 5)
                child.stderr.close()


def test_protected_worker_real_sigkill_natural_lease_and_same_id_takeover(case):
    result = asyncio.run(exercise(case))
    print("PROTECTED_WORKER_RESULT_JSON=" + json.dumps(result), flush=True)
