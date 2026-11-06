"""Explicit offline PAPER replay. Never starts with the API or contacts Binance."""

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import yaml
from sqlalchemy import create_engine

from integrations.binance_usdm.filters import SymbolRules, number
from packages.domain.config import UniqueKeyLoader, database_url, load_config, validate_startup
from packages.domain.contracts import Signal
from packages.persistence.paper import PaperStore
from services.execution.models import Ledger
from services.execution.paper import PaperCoordinator
from services.execution.simulator import PaperExchange
from services.risk.paper import AccountValue, GateSnapshot, PaperPolicy, period_keys


def demo_replay() -> dict[str, Any]:
    now = datetime(2026, 10, 19, 7, tzinfo=UTC)
    signal = dict(
        signal_id=str(uuid5(NAMESPACE_URL, "phase3/synthetic/partial-cancel")),
        environment="PAPER",
        symbol="BTCUSDT",
        strategy="TREND_FOLLOWING",
        direction="LONG",
        signal_closed_candle_at=(now - timedelta(minutes=1)).isoformat(),
        expires_at=(now + timedelta(seconds=20)).isoformat(),
        suggested_limit_price="100",
        initial_stop_price="90",
        target_price="120",
        risk_profile_version="paper-synthetic-v1",
    )
    snapshot = dict(
        market_at=now.isoformat(),
        account_at=now.isoformat(),
        correlation_at=now.isoformat(),
        bid="100",
        ask="100.1",
        mark="100",
        evaluation="RULES_ONLY",
        correlation_group="ALL_CRYPTO",
    )
    return dict(
        synthetic=True,
        as_of=now.isoformat(),
        initial_equity="10000",
        rules=[
            dict(
                symbol="BTCUSDT",
                min_price="0.1",
                max_price="1000000",
                tick_size="0.1",
                min_qty="0.001",
                max_qty="1000",
                step_size="0.001",
                min_notional="5",
                multiplier_down="0.9",
                multiplier_up="1.1",
            )
        ],
        events=[
            dict(kind="SIGNAL", at=now.isoformat(), signal=signal, snapshot=snapshot),
            dict(
                kind="FILL",
                at=(now + timedelta(seconds=1)).isoformat(),
                signal_id=signal["signal_id"],
                fill_id="synthetic-fill-1",
                fraction="0.3",
                price="100",
            ),
            dict(
                kind="CANCEL",
                at=(now + timedelta(seconds=2)).isoformat(),
                signal_id=signal["signal_id"],
            ),
            dict(
                kind="MARK",
                at=(now + timedelta(seconds=3)).isoformat(),
                symbol="BTCUSDT",
                price="120",
            ),
        ],
    )


def replay(
    store: PaperStore,
    account_id: str,
    policy: PaperPolicy,
    data: dict[str, Any],
    *,
    resume: bool = False,
) -> Ledger:
    now = datetime.fromisoformat(data["as_of"])
    value = AccountValue(as_of=now, initial=data["initial_equity"])
    day, week = period_keys(now, policy.week_start_kst)
    rules = {
        raw["symbol"]: SymbolRules(
            symbol=raw["symbol"],
            min_price=number(raw["min_price"], allow_zero=True),
            max_price=number(raw["max_price"]),
            tick_size=number(raw["tick_size"]),
            min_qty=number(raw["min_qty"]),
            max_qty=number(raw["max_qty"]),
            step_size=number(raw["step_size"]),
            min_notional=number(raw["min_notional"]),
            multiplier_down=number(raw["multiplier_down"]),
            multiplier_up=number(raw["multiplier_up"]),
        )
        for raw in data["rules"]
    }
    if resume:
        existing = store.read(account_id)
        if existing.policy != policy:
            raise ValueError("Cannot change the immutable policy during recovery")
    else:
        store.create(
            account_id,
            Ledger(
                policy=policy,
                value=value,
                daily_key=day,
                weekly_key=week,
                daily_base=value.initial,
                weekly_base=value.initial,
                high_water=value.initial,
            ),
        )
    lease = store.acquire(account_id, uuid4(), now)
    exchange = PaperExchange(store, lease)
    coordinator = PaperCoordinator(store, lease, exchange)
    coordinator.reconcile(now)
    previous = now
    for event in data["events"]:
        now = datetime.fromisoformat(event["at"])
        if now < previous:
            raise ValueError("Replay events must be ordered in UTC")
        previous = now
        kind = event["kind"]
        if kind == "SIGNAL":
            signal = Signal.model_validate(event["signal"])
            coordinator.reserve(
                signal, GateSnapshot.model_validate(event["snapshot"]), rules[signal.symbol], now
            )
            coordinator.submit(str(signal.signal_id), now)
        elif kind == "FILL":
            order = store.read(account_id).orders[event["signal_id"]]
            # Explicit synthetic liquidity fraction, not an inferred fill probability.
            quantity = rules[order.signal.symbol].floor_quantity(
                order.quantity * number(event["fraction"])
            )
            exchange.fill(order.client_id, event["fill_id"], quantity, number(event["price"]), now)
            coordinator.reconcile(now)
        elif kind == "MARK":
            exchange.mark(event["symbol"], number(event["price"]), now)
            coordinator.reconcile(now)
        elif kind == "CANCEL":
            coordinator.cancel(event["signal_id"], now)
        elif kind == "COMMAND":
            coordinator.command(event["operation_id"], event["command"], now)
        elif kind == "RECONCILE":
            coordinator.reconcile(now)
        else:
            raise ValueError("Unknown PAPER replay event")
        coordinator.expire(now)
    return store.read(account_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo", action="store_true")
    source.add_argument("--replay", type=Path)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Recover existing PAPER account after its lease expires",
    )
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument(
        "--account", required=True, help="New, dedicated PAPER- account; never overwritten"
    )
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    args = parser.parse_args()
    config = load_config()
    validate_startup(config)
    if config.mode != "PAPER":
        parser.error("PAPER mode required")
    policy = PaperPolicy.model_validate(yaml.load(args.policy.read_text(), Loader=UniqueKeyLoader))
    data = demo_replay() if args.demo else json.loads(args.replay.read_text())
    # Fail before creating an account if output already exists.
    args.output.mkdir(parents=True, exist_ok=False)
    engine = create_engine(database_url().get_secret_value(), connect_args={"connect_timeout": 2})
    try:
        ledger = replay(PaperStore(engine), args.account, policy, data, resume=args.resume)
        (args.output / "input.json").write_text(json.dumps(data, indent=2) + "\n")
        (args.output / "ledger.json").write_text(ledger.model_dump_json(indent=2) + "\n")
        report = dict(
            environment="PAPER",
            sandbox_only=True,
            synthetic=data.get("synthetic", False),
            binance_connected=False,
            live_trading_enabled=False,
            orders=len(ledger.orders),
            filled_orders=sum(o.filled > 0 for o in ledger.orders.values()),
            open_positions=sum(o.open_quantity > 0 for o in ledger.orders.values()),
            risk_state=ledger.risk_state.value,
            reconciled=ledger.reconciled,
            as_of=ledger.last_reconciled.isoformat() if ledger.last_reconciled else None,
            limitation="Planned risk can be exceeded by gaps/slippage. No claim of actual fill probability or profitability.",
        )
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
