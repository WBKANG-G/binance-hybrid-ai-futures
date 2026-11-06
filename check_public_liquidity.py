"""Bounded public DEMO book sampling; no keys, account, database or order access."""

import json
import os
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, NoReturn

import yaml
from integrations.binance_usdm.demo import REST_HOST, DemoClient, DemoCredentials, DemoSettings
from integrations.binance_usdm.demo_snapshot import signed_number
from integrations.binance_usdm.filters import SymbolRules
from pydantic import SecretStr
from services.risk.paper import minimum_entry_quantity

MAX_QUANTITY = Decimal("0.0007")
MAX_NOTIONAL = Decimal("100")
MAX_SAMPLES = 60
SAMPLE_INTERVAL_SECONDS = 2
SAMPLING_SECONDS = 120


class PublicOnlyClient(DemoClient):
    def listen_key(self, method: Literal["POST", "PUT", "DELETE"]) -> NoReturn:
        raise ValueError("PUBLIC_ONLY_NO_PRIVATE_SESSION")

    def request(self, method: str, path: str, params: Any = None) -> Any:
        raise ValueError("SIGNED_ACCOUNT_AND_ORDER_ROUTES_FORBIDDEN")

    def public(self, path: str, params: Any = None) -> Any:
        allowed = {
            "/fapi/v1/time": None,
            "/fapi/v1/exchangeInfo": None,
            "/fapi/v1/ticker/bookTicker": {"symbol": "BTCUSDT"},
        }
        if path not in allowed or params != allowed[path]:
            raise ValueError("PUBLIC_ROUTE_OR_SYMBOL_FORBIDDEN")
        return super().public(path, params)


def iso_ms(stamp: int) -> str:
    seconds, ms = divmod(stamp, 1000)
    return (datetime.fromtimestamp(seconds, UTC) + timedelta(milliseconds=ms)).isoformat()


def describe_book(raw: Any, rules: SymbolRules, now_ms: int) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("symbol") != "BTCUSDT":
        raise ValueError("BTC_BOOK_REQUIRED")
    stamp = raw.get("time")
    if type(stamp) is not int or not 0 <= now_ms - stamp <= 5000:
        raise ValueError("FRESH_BOOK_REQUIRED")
    bid, ask, bid_qty, ask_qty = (
        signed_number(raw.get(k)) for k in ("bidPrice", "askPrice", "bidQty", "askQty")
    )
    if min(bid, ask, bid_qty, ask_qty) <= 0 or bid > ask:
        raise ValueError("VALID_POSITIVE_BOOK_REQUIRED")
    rules.validate_price(bid)
    rules.validate_price(ask)
    if bid_qty % rules.step_size or ask_qty % rules.step_size:
        raise ValueError("LOT_STEP_BOOK_QUANTITIES_REQUIRED")
    minimum = minimum_entry_quantity(rules, ask)
    cap_fits = (
        rules.min_qty <= minimum <= rules.max_qty
        and minimum % rules.step_size == 0
        and minimum <= MAX_QUANTITY
        and minimum * ask <= MAX_NOTIONAL
    )
    return {
        "book_utc": iso_ms(stamp),
        "quote_age_ms": now_ms - stamp,
        "bid_price": str(bid),
        "ask_price": str(ask),
        "bid_quantity": str(bid_qty),
        "ask_quantity": str(ask_qty),
        "spread_pct": str((ask - bid) / bid * 100),
        "minimum_entry_quantity_estimate": str(minimum),
        "minimum_notional_estimate_usdt": str(minimum * ask),
        "public_minimum_fits_test_cap": cap_fits,
        "within_cap_thin_top_candidate": cap_fits and ask_qty < minimum,
        "partial_fill_guaranteed": False,
    }


def observe(
    client: DemoClient, *, monotonic: Any = time.monotonic, pause: Any = time.sleep
) -> dict[str, Any]:
    client.sync_clock()
    info = client.public("/fapi/v1/exchangeInfo")
    rules = SymbolRules.from_exchange_info(info, "BTCUSDT")
    samples = []
    deadline = monotonic() + SAMPLING_SECONDS
    for index in range(MAX_SAMPLES):
        if monotonic() >= deadline:
            break
        if index:
            wait = min(SAMPLE_INTERVAL_SECONDS, max(0, deadline - monotonic()))
            if wait <= 0:
                break
            pause(wait)
            if monotonic() >= deadline:
                break
        book = client.public("/fapi/v1/ticker/bookTicker", {"symbol": "BTCUSDT"})
        now = client.clock_ms() + client.offset
        samples.append(describe_book(book, rules, now))
    if not samples:
        raise ValueError("NO_SAMPLES")
    thin = sum(row["within_cap_thin_top_candidate"] for row in samples)
    return {
        "public_liquidity": "PUBLIC_ONLY_OBSERVATION_FINISHED",
        "environment": "DEMO",
        "symbol": "BTCUSDT",
        "sample_count": len(samples),
        "within_cap_thin_top_samples": thin,
        "maximum_quantity_cap": str(MAX_QUANTITY),
        "maximum_notional_cap_usdt": str(MAX_NOTIONAL),
        "lot_step": str(rules.step_size),
        "observation_limit_seconds": SAMPLING_SECONDS,
        "maximum_samples": MAX_SAMPLES,
        "sample_interval_seconds": SAMPLE_INTERVAL_SECONDS,
        "first_sample": samples[0],
        "last_sample": samples[-1],
        "minimum_ask_quantity": str(min(Decimal(row["ask_quantity"]) for row in samples)),
        "maximum_ask_quantity": str(max(Decimal(row["ask_quantity"]) for row in samples)),
        "candidate_examples": [row for row in samples if row["within_cap_thin_top_candidate"]][:3],
        "interpretation": "THIN_TOP_OBSERVED_NOT_EXECUTION_PROOF"
        if thin
        else "NO_WITHIN_CAP_THIN_TOP_OBSERVED_IN_THIS_WINDOW",
        "partial_fill_guaranteed": False,
        "signal_generated": False,
        "private_account_checked": False,
        "database_accessed": False,
        "keys_read": False,
        "signed_requests": 0,
        "order_writes": 0,
        "orders_enabled": False,
        "live_trading_enabled": False,
        "next_action": "REVIEW_ONLY_DO_NOT_SUBMIT_FROM_THIS_REPORT",
    }


def main() -> None:
    try:
        if (
            REST_HOST != "https://demo-fapi.binance.com"
            or os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
            or os.getenv("TRADING_MODE", "PAPER").upper() == "LIVE"
        ):
            raise ValueError("DEMO_PUBLIC_CONTEXT_REQUIRED")
        cfg = DemoSettings.model_validate(
            yaml.safe_load(Path("configs/demo-isolated.example.yaml").read_text())
        ).model_copy(update={"demo_trading_enabled": False})
        # Public methods always pass no API key to transport; these placeholders
        # are never read from the key session/environment or sent as headers.
        client = PublicOnlyClient(
            cfg, DemoCredentials(SecretStr("unused-public-only"), SecretStr("unused-public-only"))
        )
        report = observe(client)
    except Exception:  # noqa: BLE001 -- fixed safe error, no raw network/config details
        report = {
            "public_liquidity": "BLOCKED_NO_AUTOMATIC_RETRY",
            "signed_requests": 0,
            "order_writes": 0,
            "orders_enabled": False,
            "live_trading_enabled": False,
        }
    print(json.dumps(report), flush=True)
    if report["public_liquidity"] != "PUBLIC_ONLY_OBSERVATION_FINISHED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
