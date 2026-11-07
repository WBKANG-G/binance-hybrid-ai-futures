"""Bounded DEMO private-stream observation while entries stay paused. No order writes."""

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from sqlalchemy import create_engine

from integrations.binance_usdm.demo import DemoClient, DemoFailure, DemoSettings
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import DemoStore
from scripts.demo_key_session import credentials
from services.execution.demo import DemoCoordinator
from services.execution.demo_session_diagnostics import failure_diagnostic
from services.execution.demo_stream import session


class ObservationClient(DemoClient):
    """Session lifecycle is allowed; all order/account mutations are denied."""

    def request(self, method: str, path: str, params: Mapping[str, str] | None = None) -> Any:
        if method != "GET":
            raise DemoFailure("Private observation forbids order/account mutations")
        return super().request(method, path, params)


ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"


def empty_paused(store: DemoStore) -> None:
    ledger = store.read(ACCOUNT)
    if (
        not ledger.paused
        or ledger.risk_state.value not in {"NORMAL", "EMERGENCY"}
        or ledger.margin_mode != "ISOLATED"
        or ledger.symbols != ("BTCUSDT",)
        or any(ledger.positions.values())
        or any(
            o.remaining or o.open_quantity or o.position_state.value != "CLOSED"
            for o in ledger.orders.values()
        )
    ):
        raise DemoFailure("Paused empty isolated DEMO required")


async def observe(coordinator: DemoCoordinator, seconds: float = 30) -> None:
    ready = False

    def connected() -> None:
        nonlocal ready
        empty_paused(coordinator.store)
        ready = True

    try:
        await asyncio.wait_for(session(coordinator, on_ready=connected), timeout=seconds)
    except TimeoutError:
        pass  # A normal bounded cancellation is not itself a session error.
    if not ready or coordinator.session_failed:
        raise DemoFailure("Private observation failed or did not become ready")


def main() -> None:
    engine = None
    store = None
    lease = None
    coordinator = None
    status = "BLOCKED"
    try:
        key = credentials()
        cfg = DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
            )
        ).model_copy(update={"demo_trading_enabled": True})
        client = ObservationClient(cfg, key)
        client.sync_clock()
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        store = DemoStore(engine)
        empty_paused(store)
        lease = store.acquire(ACCOUNT, uuid4())
        coordinator = DemoCoordinator(store, lease, client)
        coordinator.session_diagnostic.stage = "RECONCILE"
        coordinator.refresh_metadata()
        coordinator.reconcile()
        empty_paused(store)
        asyncio.run(observe(coordinator))
        status = "OBSERVATION_FINISHED_NO_ENTRY"
    except Exception as error:
        status = "FAILED_OR_BLOCKED_NO_ENTRY"
        if coordinator is not None and coordinator.session_diagnostic.code == "NONE":
            coordinator.session_failed = True
            coordinator.session_diagnostic = failure_diagnostic(
                error, coordinator.session_diagnostic
            )
    finally:
        try:
            if lease is not None and store is not None:
                store.release(lease)
        except Exception:
            status = "LEASE_RELEASE_NOT_CONFIRMED_KEEP_PAUSED"
        finally:
            if engine is not None:
                engine.dispose()
    print(
        json.dumps(
            {
                "private_observation": status,
                "private_session_diagnostic": coordinator.session_diagnostic.model_dump(mode="json")
                if coordinator
                else None,
                "entry_signal_supplied": False,
                "orders_enabled": False,
                "live_trading_enabled": False,
            }
        )
    )
    if status != "OBSERVATION_FINISHED_NO_ENTRY":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
