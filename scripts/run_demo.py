"""Explicit isolated DEMO CLI. Default is read-only authenticated preflight."""

import argparse
import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import yaml
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from integrations.binance_usdm.demo import (
    DemoClient,
    DemoCredentials,  # noqa: F401 - existing CLI test seam
    DemoFailure,
    DemoSettings,
)
from integrations.binance_usdm.filters import number
from packages.domain.config import UniqueKeyLoader, database_url
from packages.domain.contracts import Signal
from packages.persistence.demo import DemoStore
from scripts.demo_key_session import credentials as session_credentials
from services.execution.demo import DemoCoordinator
from services.execution.demo_models import DemoLedger
from services.execution.demo_preview import PreviewBlocked, preview
from services.execution.demo_stream import session
from services.execution.demo_validation import (
    confirm_exit,
    database_failure,
    safe_failure,
    validate_once,
)
from services.risk.paper import AccountValue, GateSnapshot, RiskRejected, period_keys


async def check_private(coordinator: DemoCoordinator, seconds: float = 20) -> None:
    """Bounded session check on an empty reconciled DEMO account; no entry callback."""
    ready = False

    def check_empty() -> None:
        ledger = coordinator.store.read(coordinator.lease.account_id)
        if (
            not ledger.reconciled
            or ledger.risk_state.value != "NORMAL"
            or any(ledger.positions.values())
            or any(o.remaining or o.open_quantity for o in ledger.orders.values())
        ):
            raise DemoFailure("Private check requires an empty reconciled NORMAL DEMO account")

    check_empty()

    def on_ready() -> None:
        nonlocal ready
        check_empty()
        ledger = coordinator.store.read(coordinator.lease.account_id)
        if not ledger.stream_ready:
            raise DemoFailure("DEMO private stream not ready")
        ready = True
        print(
            json.dumps(
                {
                    "private_stream_check": "CONNECTED",
                    "environment": "DEMO",
                    "account_reconciled": True,
                    "private_stream_ready": True,
                    "entry_signal_supplied": False,
                    "live_trading_enabled": False,
                }
            ),
            flush=True,
        )

    try:
        await asyncio.wait_for(session(coordinator, on_ready=on_ready), timeout=seconds)
    except TimeoutError:
        if not ready:
            raise DemoFailure("DEMO private check timed out before readiness") from None
    if not ready:
        raise DemoFailure("DEMO private check ended before readiness")
    print(
        json.dumps(
            {
                "private_stream_check": "OBSERVATION_FINISHED",
                "environment": "DEMO",
                "entry_signal_supplied": False,
                "live_trading_enabled": False,
            }
        ),
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--account",
        required=True,
        help="Dedicated DEMO- account; never share the underlying exchange account",
    )
    parser.add_argument(
        "--initialize", action="store_true", help="Create a new DEMO ledger (never overwrite)"
    )
    parser.add_argument(
        "--enable-demo", action="store_true", help="Explicit opt-in to DEMO writes only"
    )
    parser.add_argument(
        "--watch", action="store_true", help="Run protected private-stream supervision"
    )
    parser.add_argument(
        "--check-private",
        action="store_true",
        help="20-second private session check; empty DEMO only",
    )
    parser.add_argument(
        "--preview-entry", action="store_true", help="Read-only non-executable order illustrations"
    )
    parser.add_argument(
        "--confirm-flat",
        action="store_true",
        help="Confirm existing exit fills and clean leftover protection; no entry or exit POST",
    )
    parser.add_argument(
        "--signal", type=Path, help="One explicit DEMO Signal + GateSnapshot; requires --watch"
    )
    parser.add_argument(
        "--quantity-cap", type=number, help="Explicit maximum entry quantity; requires --signal"
    )
    parser.add_argument("--command", choices=["PAUSE_ENTRIES", "SAFE_STOP", "EMERGENCY_FLATTEN"])
    parser.add_argument("--operation-id", type=UUID)
    parser.add_argument(
        "--entry-style",
        choices=["PASSIVE", "OPPOSITE_QUOTE"],
        default="PASSIVE",
        help="Explicit diagnostic limit-price source; never market conversion",
    )
    parser.add_argument(
        "--validate-minimum",
        choices=["LONG", "SHORT"],
        help="One capped DEMO-only operational entry with final flatten verification",
    )
    parser.add_argument(
        "--resume-entries",
        action="store_true",
        help="Explicitly resume NORMAL/reconciled entries inside --watch; never clears hard locks",
    )
    args = parser.parse_args()
    if (
        os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
        or os.getenv("TRADING_MODE", "PAPER") == "LIVE"
    ):
        parser.error("LIVE context is forbidden in the DEMO CLI")
    config = DemoSettings.model_validate(yaml.load(args.config.read_text(), Loader=UniqueKeyLoader))
    if args.enable_demo:
        config = DemoSettings.model_validate(config.model_dump() | {"demo_trading_enabled": True})
    if args.entry_style != "PASSIVE" and not (args.validate_minimum or args.preview_entry):
        parser.error("--entry-style requires --validate-minimum or read-only --preview-entry")
    if args.validate_minimum and (
        not args.enable_demo
        or not args.operation_id
        or args.watch
        or args.signal
        or args.command
        or args.initialize
        or args.check_private
        or args.preview_entry
        or args.quantity_cap is not None
        or args.resume_entries
        or args.confirm_flat
    ):
        parser.error(
            "--validate-minimum requires --enable-demo and --operation-id; no other actions"
        )
    if args.validate_minimum and (
        config.margin_mode != "ISOLATED" or config.symbols != ("BTCUSDT",)
    ):
        parser.error("--validate-minimum requires explicit ISOLATED BTCUSDT scope")
    if args.preview_entry and (
        config.demo_trading_enabled
        or args.watch
        or args.check_private
        or args.signal
        or args.command
        or args.resume_entries
        or args.initialize
        or args.confirm_flat
    ):
        parser.error("--preview-entry requires writes disabled and no other execution flags")
    if (
        args.watch or args.check_private or args.command or args.signal or args.confirm_flat
    ) and not config.demo_trading_enabled:
        parser.error("DEMO writes require explicit --enable-demo or a reviewed DEMO config")
    if args.check_private and (
        args.watch
        or args.signal
        or args.command
        or args.resume_entries
        or args.initialize
        or args.confirm_flat
    ):
        parser.error(
            "--check-private cannot initialize, submit signals, resume or execute commands"
        )
    if args.confirm_flat and (
        not args.enable_demo
        or args.watch
        or args.signal
        or args.command
        or args.resume_entries
        or args.initialize
        or args.check_private
        or args.preview_entry
        or args.validate_minimum
        or args.operation_id
        or args.quantity_cap is not None
        or args.entry_style != "PASSIVE"
        or config.margin_mode != "ISOLATED"
        or config.symbols != ("BTCUSDT",)
    ):
        parser.error(
            "--confirm-flat requires explicit enabled ISOLATED BTCUSDT and no other actions"
        )
    if args.resume_entries and (not args.watch or not args.operation_id):
        parser.error("--resume-entries requires --watch and a stable --operation-id")
    if args.signal and not args.watch:
        parser.error("--signal requires continuous --watch protection")
    if args.quantity_cap is not None and not args.signal:
        parser.error("--quantity-cap requires --signal and protected --watch execution")
    if args.command and not args.operation_id:
        parser.error("Commands require a stable --operation-id")
    try:
        credentials = session_credentials()
    except ValueError as error:
        parser.error(str(error))
    engine = create_engine(database_url().get_secret_value(), connect_args={"connect_timeout": 2})
    coordinator = None
    lease = None
    store = DemoStore(engine)
    try:
        now = store.now()
        if args.initialize:
            day, week = period_keys(now, config.policy.week_start_kst)
            store.create(
                args.account,
                DemoLedger(
                    policy=config.policy,
                    margin_mode=config.margin_mode,
                    symbols=config.symbols,
                    value=AccountValue(as_of=now, initial=Decimal(1)),
                    daily_base=Decimal(1),
                    weekly_base=Decimal(1),
                    high_water=Decimal(1),
                    daily_key=day,
                    weekly_key=week,
                ),
            )
        lease = store.acquire(args.account, uuid4())
        # The private connection diagnostic must inspect occupancy before enabling
        # session mutations; ordinary enabled reconciliation may protect open positions.
        initial_config = (
            config.model_copy(update={"demo_trading_enabled": False})
            if args.check_private or args.validate_minimum or args.confirm_flat
            else config
        )
        client = DemoClient(initial_config, credentials)
        client.sync_clock()
        coordinator = DemoCoordinator(store, lease, client)
        coordinator.refresh_metadata()
        coordinator.reconcile()
        if args.command:
            result = coordinator.command(args.operation_id, args.command)
            print(
                json.dumps(
                    {
                        "environment": "DEMO",
                        "operation_result": result,
                        "live_trading_enabled": False,
                    }
                )
            )
        elif args.confirm_flat:
            client.settings = config
            result = asyncio.run(confirm_exit(coordinator))
            ledger = store.read(args.account)
            print(
                json.dumps(
                    {
                        "completion_confirmation": result,
                        "confirmation_stage": coordinator.flat_confirmation_stage,
                        "confirmation_diagnostic": coordinator.flat_confirmation_diagnostic,
                        "environment": "DEMO",
                        "risk_state": ledger.risk_state.value,
                        "paused": ledger.paused,
                        "orders_enabled": False,
                        "live_trading_enabled": False,
                    }
                )
            )
            if result != "VERIFIED_FLAT":
                raise DemoFailure("Existing completion is unresolved; no entry or exit submitted")
        elif args.validate_minimum:
            client.settings = config
            asyncio.run(
                validate_once(
                    coordinator,
                    args.operation_id,
                    args.validate_minimum,
                    entry_style=args.entry_style,
                )
            )
        elif args.preview_entry:
            print(
                json.dumps(preview(client, store.read(args.account), entry_style=args.entry_style))
            )
        elif args.check_private:
            client.settings = config
            asyncio.run(check_private(coordinator))
        elif args.watch:

            def on_ready() -> None:
                if args.resume_entries:
                    coordinator.resume_entries(args.operation_id)
                if args.signal:
                    raw = json.loads(args.signal.read_text())
                    signal = Signal.model_validate(raw["signal"])
                    coordinator.reserve(
                        signal,
                        GateSnapshot.model_validate(raw["gate"]),
                        quantity_cap=args.quantity_cap,
                    )
                    coordinator.submit(str(signal.signal_id))

            asyncio.run(session(coordinator, on_ready=on_ready))
        else:
            ledger = store.read(args.account)
            print(
                json.dumps(
                    {
                        "environment": "DEMO",
                        "preflight": "READ_ONLY",
                        "account_reconciled": ledger.reconciled,
                        "private_stream_ready": ledger.stream_ready,
                        "orders_enabled": False,
                        "live_trading_enabled": False,
                        "risk_state": ledger.risk_state.value,
                        "excluded_symbols": coordinator.excluded_symbols,
                        "margin_mode": config.margin_mode,
                        "configured_symbols": config.symbols,
                        "verified_symbol_settings": {
                            symbol: {
                                "margin_mode": ledger.margin_types.get(symbol),
                                "leverage": str(ledger.verified_leverage.get(symbol)),
                            }
                            for symbol in config.symbols
                        },
                    }
                )
            )
    except PreviewBlocked as error:
        raise SystemExit(
            json.dumps(
                {
                    "preview": "BLOCKED",
                    "orders_enabled": False,
                    "live_trading_enabled": False,
                    "reason": str(error),
                }
            )
        ) from None
    except KeyboardInterrupt:
        # No silent assumption of flat. On restart the new leader must reconcile.
        print("DEMO supervisor stopped; inspect positions/protection and reconcile before entry")
    except SQLAlchemyError as error:
        raise SystemExit(
            json.dumps(
                {
                    "operation": "BLOCKED",
                    "database_failure": database_failure(error),
                    **(
                        {
                            "confirmation_stage": getattr(
                                locals().get("coordinator"), "flat_confirmation_stage", "PREFLIGHT"
                            ),
                            "confirmation_diagnostic": getattr(
                                locals().get("coordinator"), "flat_confirmation_diagnostic", {}
                            ),
                        }
                        if args.confirm_flat
                        else {}
                    ),
                    "orders_enabled": False,
                    "live_trading_enabled": False,
                }
            )
        ) from None
    except (DemoFailure, RiskRejected, ValueError, KeyError, OSError) as error:
        if args.confirm_flat:
            raise SystemExit(
                json.dumps(
                    {
                        "completion_confirmation": "BLOCKED_KEEP_LOCK",
                        "failure": getattr(error, "code", safe_failure(error))
                        if getattr(error, "code", "")
                        in {
                            "REQUEST_AUDIT_TIME_BUDGET",
                            "REQUEST_AUDIT_QUERY_BUDGET",
                            "REQUEST_AUDIT_CLOCK_BUDGET",
                        }
                        else safe_failure(error),
                        "confirmation_stage": getattr(
                            locals().get("coordinator"), "flat_confirmation_stage", "PREFLIGHT"
                        ),
                        "confirmation_diagnostic": getattr(
                            locals().get("coordinator"), "flat_confirmation_diagnostic", {}
                        ),
                        "orders_enabled": False,
                        "live_trading_enabled": False,
                    }
                )
            ) from None
        raise SystemExit(
            "DEMO operation blocked or unresolved; inspect sanitized ledger/status, network and runtime credential bindings"
        ) from None
    finally:
        if lease is not None:
            try:
                store.release(lease)
            except Exception:
                pass
        engine.dispose()


if __name__ == "__main__":
    main()
