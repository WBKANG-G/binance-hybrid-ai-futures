"""Explicit single capped DEMO entry, bounded TP observation, original safety cleanup."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import yaml
from sqlalchemy import create_engine

from integrations.binance_usdm.demo import DemoClient, DemoSettings, transport
from integrations.binance_usdm.demo_read_routing import TPReadRouting
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import DemoStore
from scripts.capture_demo_wss_hints import Capture, replay
from scripts.demo_key_session import credentials
from scripts.observe_demo_private import ACCOUNT
from services.execution import demo_validation
from services.execution.demo_api_restart_check import empty
from services.execution.demo_tp_trigger_check import (
    OppositeQuoteTPTriggerCoordinator,
    PooledReadTPTriggerCoordinator,
    TPTriggerClient,
    TPTriggerCoordinator,
    run,
)
from services.execution.demo_validation import safe_failure


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable-demo", action="store_true", required=True)
    parser.add_argument("--enable-tp-trigger-observation", action="store_true", required=True)
    parser.add_argument("--operation-id", type=UUID, required=True)
    parser.add_argument("--entry-style", choices=["PASSIVE", "OPPOSITE_QUOTE"], default="PASSIVE")
    parser.add_argument("--enable-opposite-quote-tp-observation", action="store_true")
    parser.add_argument("--enable-pooled-read-tp-observation", action="store_true")
    args = parser.parse_args()
    if (args.entry_style == "OPPOSITE_QUOTE") != args.enable_opposite_quote_tp_observation:
        parser.error("Opposite-quote TP requires both explicit style and additional enable flag")
    if args.enable_pooled_read_tp_observation and not args.enable_opposite_quote_tp_observation:
        parser.error("Pooled TP reads require the explicit opposite-quote TP scope")
    engine = store = lease = None
    client = None
    routed = None
    report = {
        "tp_trigger_check": "BLOCKED",
        "failure": "CHECK_FAILED",
        "orders_enabled": False,
        "live_trading_enabled": False,
    }
    success = False
    try:
        if (
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
            or os.getenv("TRADING_MODE", "PAPER") == "LIVE"
        ):
            raise ValueError("LIVE context forbidden")
        key = credentials(prompt=False)
        cfg = DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
            )
        )
        client = TPTriggerClient(cfg.model_copy(update={"demo_trading_enabled": False}), key)
        if args.enable_pooled_read_tp_observation:
            if client.send is not transport:
                raise ValueError("Pooled TP reads require the standard SDK transport")
            routed = TPReadRouting(client.send)
            client.send = routed
        client.sync_clock()
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        store = DemoStore(engine)
        state = store.read(ACCOUNT)
        if (
            str(args.operation_id) in state.orders
            or str(uuid5(args.operation_id, "tp-trigger-observation")) in state.operations
        ):
            raise ValueError("Existing operation must not be submitted again")
        lease = store.acquire(ACCOUNT, uuid4())
        coordinator = (
            PooledReadTPTriggerCoordinator(store, lease, client)
            if args.enable_pooled_read_tp_observation
            else OppositeQuoteTPTriggerCoordinator(store, lease, client)
            if args.entry_style == "OPPOSITE_QUOTE"
            else TPTriggerCoordinator(store, lease, client)
        )
        empty(coordinator)
        coordinator.refresh_metadata()
        coordinator.reconcile()
        client.settings = cfg.model_copy(update={"demo_trading_enabled": True})
        if args.entry_style == "OPPOSITE_QUOTE":
            capture = Capture()
            original_session = demo_validation.session
            demo_validation.session = capture.supervise
            try:
                report = asyncio.run(
                    run(coordinator, args.operation_id, entry_style="OPPOSITE_QUOTE")
                )
            finally:
                demo_validation.session = original_session
            # Replay cannot erase the order result or cause another order attempt.
            try:
                replayed = asyncio.run(replay(capture.hints))
            except Exception:
                replayed = {"hint_replay": "NOT_VERIFIED_NO_AUTOMATIC_RETRY"}
            report["hint_capture"] = {
                "captured_hints": capture.hints,
                "captured_hint_count": len(capture.hints),
                "full_private_frames_recorded": False,
                "replay": replayed,
            }
        else:
            report = asyncio.run(run(coordinator, args.operation_id))
        success = report["tp_trigger_check"] == "TP_FILLED_THEN_REMAINING_SL_CANCELED_FLAT_VERIFIED"
    except Exception as error:
        report["failure"] = safe_failure(error)
    finally:
        try:
            if store is not None and lease is not None:
                store.release(lease)
        except Exception:
            success = False
            report["tp_trigger_check"] = "BLOCKED"
            report["failure"] = "LEASE_RELEASE_FAILED"
        finally:
            try:
                if engine is not None:
                    engine.dispose()
            except Exception:
                success = False
                report["tp_trigger_check"] = "BLOCKED"
                report["failure"] = "ENGINE_DISPOSE_FAILED"
            finally:
                if routed is not None:
                    try:
                        routed.close()
                        report["tp_read_transport_diagnostic"] = routed.diagnostics()
                    except Exception:
                        success = False
                        report["tp_trigger_check"] = "BLOCKED"
                        report["failure"] = "TP_READ_TRANSPORT_CLEANUP_FAILED"
                    finally:
                        if client is not None:
                            client.send = routed.original
    if isinstance(client, DemoClient):
        report["demo_rate_limit_diagnostic"] = client.rate_limit_diagnostics()
    print(json.dumps(report), flush=True)
    if not success:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
