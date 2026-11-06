"""Adopt the reviewed operator-attested day baseline; keep entries paused."""

import argparse
import hashlib
import json
import os
import re
import time
from datetime import UTC, datetime, timedelta
from datetime import time as daytime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

import yaml
from sqlalchemy import create_engine, select

from integrations.binance_usdm.demo import (
    DemoClient,
    DemoFailure,
    DemoRateLimited,
    DemoSettings,
    transport,
)
from integrations.binance_usdm.demo_rate_limits import rate_headers
from integrations.binance_usdm.demo_read_routing import TPReadRouting
from integrations.binance_usdm.demo_snapshot import signed_number
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import DemoStore, accounts
from scripts.demo_key_session import credentials
from services.execution.demo_empty_db_restart import empty
from services.execution.demo_models import AttestedDayBaseline, DemoLedger
from services.execution.paper import account_risk
from services.risk.paper import AccountValue, period_keys

ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"

INCIDENT = "041d1f35-870e-46cb-aad2-ed6059bc1095"

RECEIPT = str(uuid5(UUID(INCIDENT), "signed-operator-manual-close-v1"))

NEW_PINS = {
    "configs/demo-isolated.example.yaml": "d93e85c2e056ffbbc3dac5fab9f0e5bf28032fd16f41bbff1f3ae81fb00882ca",
    "integrations/binance_usdm/demo.py": "b052fd6a68b0ffcb358567e1fbf96fc529005a21e2467490207c205c41debb58",
    "integrations/binance_usdm/demo_rate_limits.py": "d72af60f10a74baea689e11ef8f11b5003f2ab9f89e539290c45b93f2f3d4a4e",
    "integrations/binance_usdm/demo_read_routing.py": "a6e96b21135882a272cb5c97f86ddc14c3a5f4fa79dd115e40397252c679a66d",
    "integrations/binance_usdm/demo_snapshot.py": "02e5d789280f3ed8aa2667319248982d5cd4eedd2fb8b72e70aef8785e57ed6a",
    "integrations/binance_usdm/filters.py": "0279613f1a17a2bc7ffd50e30be2b6fd1aed8ba240219e8e893b754a067d95ec",
    "integrations/binance_usdm/orders.py": "2ab366265f7d35356ee9636c6aad4b594b518beeb636407515a57386b3e8441f",
    "integrations/binance_usdm/recovery_transport.py": "7495c0436437070644627d5ee175d7a63332dc5dc2134574285a7870e8bbba77",
    "integrations/binance_usdm/rest.py": "9a14edc40fa6365a50fe57c9ddeff4e866044e00287e7c5b680d8bb3dbd0ad52",
    "packages/domain/config.py": "c2bd7a039e3ea007a1e34a6dbdd058cc626e2690cc8d55b0761cfd2085bf311d",
    "packages/domain/contracts.py": "71c0a95faad586d6e3f342d83b6b95a4772cb6e2c1808b44fc51fdf9f1332947",
    "packages/persistence/demo.py": "40f9a33b8eeda28c96f36de35656e4667a2d2461ce9de79dc4b19a687cd5cd5a",
    "scripts/check_demo_tp_trigger.py": "e9a04c8b0dd877c8cdb1708ee2b75714984ab9c6837d873c067dc0a94e6c2baa",
    "scripts/demo_key_session.py": "85c698bc8bbad98bad4051c346a5e4e0a3a3bd22f8bbb90a362f835cadc633dd",
    "services/execution/demo.py": "deb90dd8ddd9df627054a11c76f8dd8b09e364f2aaaca8f82b7fd0c237c444ab",
    "services/execution/demo_empty_db_restart.py": "6adcc8284b43050bc4a115f2796c001d424ba7e0261654c099075fb3d950e39f",
    "services/execution/demo_event_clock.py": "924f3b88c9092355bb4b4ada3114944848208ab5b44ab73ee5b45d5fc1a29bfb",
    "services/execution/demo_event_inbox.py": "6d9be8c64e144592a5c2343c1db6e4dfc1dee65aa65eeebc08bd52b5aa633281",
    "services/execution/demo_flat_transport.py": "93f878c9869ad397a52546f0aa976bbe8071366eff36c7d0be6b1329df757401",
    "services/execution/demo_models.py": "175def10d7378661e82616b02ec87d6dbee4ffdeb4eb94437ce682206168dfb0",
    "services/execution/demo_protected_worker_check.py": "70d9f0a6ec74223e8a7a82ec55c976288c10fb13c01c9b478f49f1a0fd408709",
    "services/execution/demo_recovery.py": "ca663f098d2f7c462d1f225f4ade91d743169175f125c574794dc58175c8c76e",
    "services/execution/demo_recovery_budget.py": "2c7aa260fd708948b0ae7854257de03bc8b59c86da85180ea5230b07e8df772b",
    "services/execution/demo_stream.py": "770b0783287dcd607c35dad96b8882acd2bcb6a205928046b9831f9920a6a29f",
    "services/execution/demo_tp_trigger_check.py": "f2a0cbac4fc33142fa63befb5aa38e1eb7a6ac2bb10018c0ea04290f7e310a6a",
    "services/execution/demo_validation.py": "54b7c41e679f69c78019fac81781fc8c43954375343ee35e40d43830251d111f",
    "services/execution/models.py": "5d362e33788a304e6f2f63f22868454b3804b103277a6fe4a1297c7e50e10a5d",
    "services/execution/paper.py": "1a847e50fc78d48846fadc9953fad07aed5bbdf83f1e30986d39fef0e0d9fc13",
    "services/risk/paper.py": "7afe00460abae04dda795ab0dba8b8ad84877fa25686342c1fbce3f12ac80832",
}


class Blocked(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise Blocked(code)


def local(store, lease=None):
    with store.engine.connect() as conn:
        conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout='3s'")
        row = (
            conn.execute(select(accounts).where(accounts.c.account_id == ACCOUNT)).mappings().one()
        )
    state = DemoLedger.model_validate(row["state"])
    if lease is None:
        require(row["owner"] is None, "ACTIVE_OWNER_BLOCKED")
    else:
        require(
            row["owner"] == str(lease.owner)
            and row["fence"] == lease.fence
            and row["lease_until"] is not None
            and row["lease_until"] > store.now(),
            "LEASE_EXPIRED_OR_FENCED",
        )
    empty(state)
    require(state.income_since_ms is not None, "INITIALIZED_LEDGER_REQUIRED")
    order = state.orders.get(INCIDENT)
    require(
        order is not None
        and order.quantity == order.filled == Decimal("0.0007")
        and order.state.value == "FILLED"
        and order.position_state.value == "CLOSED"
        and not order.open_quantity
        and not order.actual_risk
        and not order.remaining
        and not order.protected
        and order.exit_id is None
        and order.exit_state == "NONE"
        and str(order.server_order_id) == "28668115919",
        "MANUAL_CLOSE_LOCAL_SCOPE_MISMATCH",
    )
    receipt = json.loads(state.operations.get(RECEIPT, "{}"))
    require(isinstance(receipt, dict), "MANUAL_CLOSE_RECEIPT_MISMATCH")
    try:
        net = Decimal(receipt.get("net_usdt", "NaN"))
    except (InvalidOperation, ValueError, TypeError):
        raise Blocked("MANUAL_CLOSE_RECEIPT_MISMATCH") from None
    require(
        receipt.get("incident") == INCIDENT
        and receipt.get("source") == "SIGNED_OPERATOR_MANUAL_CLOSE"
        and receipt.get("entry_order_id") == "28668115919"
        and receipt.get("manual_close_order_id") == "28668119491"
        and receipt.get("trade_ids") == ["554837511", "554838531"]
        and net == Decimal("-0.07032487"),
        "MANUAL_CLOSE_RECEIPT_MISMATCH",
    )
    require(
        state.operations.get(str(uuid5(UUID(INCIDENT), "tp-trigger-observation")))
        == "TP_TRIGGER:NOT_ACCEPTED",
        "FAILED_TRIAL_MARKER_CHANGED",
    )
    return row["owner"], row["fence"], row["lease_until"], state


class ProbeStop(DemoFailure):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class Monitor:
    def __init__(self, route, *, clock=time.monotonic):
        self.route, self.clock = route, clock
        self.started = clock()
        self.records = []
        self.clock_attempts = 0
        self.limit = None
        self.latest_weight = None
        self.last_had_weight = False
        self.server_ms = lambda: 0
        self.limited = False

    def enable(self, limit):
        require(type(limit) is int and limit > 0, "PUBLISHED_LIMIT_INVALID")
        if not self.last_had_weight:
            raise ProbeStop("WEIGHT_HEADER_UNAVAILABLE")
        self.limit = limit

    def check(self):
        elapsed = self.clock() - self.started
        if not 0 <= elapsed < MAX_SECONDS:
            raise ProbeStop("TIME_BUDGET_REACHED")
        if len(self.records) >= MAX_HTTP_ATTEMPTS:
            raise ProbeStop("HTTP_BUDGET_REACHED")
        if self.limited:
            raise ProbeStop("RATE_LIMIT_ALREADY_OBSERVED")
        if self.limit is not None:
            reserve = max(1, min(100, self.limit // 10))
            if self.latest_weight is None or self.latest_weight + reserve >= self.limit * 3 // 5:
                raise ProbeStop("WEIGHT_HEADROOM_THRESHOLD")

    def __call__(self, method, path, query, key):
        if method != "GET" or path not in READ_PATHS:
            raise ProbeStop("NON_READ_OR_UNKNOWN_ROUTE_FORBIDDEN")
        if self.limit is None and path not in {"/fapi/v1/time", "/fapi/v1/exchangeInfo"}:
            raise ProbeStop("LIMIT_DISCOVERY_REQUIRED")
        self.check()
        if path == "/fapi/v1/time":
            if self.clock_attempts >= MAX_CLOCK_ATTEMPTS:
                raise ProbeStop("CLOCK_BUDGET_REACHED")
            self.clock_attempts += 1
        before = self.clock()
        record = {
            "seq": len(self.records) + 1,
            "path": path,
            "signed": key is not None,
            "http_status": None,
            "used_weight_1m": None,
            "counter_delta": None,
            "counter_reset_or_window_change": False,
        }
        self.records.append(record)
        response = self.route(method, path, query, key)
        metadata = rate_headers(response.headers)
        weight = (
            int(metadata["x-mbx-used-weight-1m"]) if "x-mbx-used-weight-1m" in metadata else None
        )
        bucket = self.server_ms() // 60000
        previous = next(
            (r for r in reversed(self.records[:-1]) if r["used_weight_1m"] is not None), None
        )
        reset = bool(
            previous
            and (
                bucket != previous.get("server_minute_hint")
                or weight is not None
                and weight < previous["used_weight_1m"]
            )
        )
        record.update(
            http_status=response.status,
            used_weight_1m=weight,
            server_minute_hint=bucket,
            counter_reset_or_window_change=reset,
            elapsed_ms=max(0, int((self.clock() - before) * 1000)),
        )
        if weight is not None and previous and not reset:
            record["counter_delta"] = weight - previous["used_weight_1m"]
        self.last_had_weight = weight is not None
        if weight is not None:
            self.latest_weight = weight
        if response.status in {418, 429}:
            self.limited = True
            return response  # Let the original SDK set cooldown and classify the response.
        if self.limit is not None and weight is None:
            raise ProbeStop("WEIGHT_HEADER_UNAVAILABLE")
        return response


def published_limit(info):
    require(
        isinstance(info, dict) and isinstance(info.get("rateLimits"), list),
        "PUBLISHED_LIMIT_UNAVAILABLE",
    )
    rows = [
        r
        for r in info["rateLimits"]
        if isinstance(r, dict)
        and r.get("rateLimitType") == "REQUEST_WEIGHT"
        and r.get("interval") == "MINUTE"
        and type(r.get("intervalNum")) is int
        and r["intervalNum"] == 1
    ]
    require(
        len(rows) == 1 and type(rows[0].get("limit")) is int and rows[0]["limit"] > 0,
        "PUBLISHED_LIMIT_AMBIGUOUS_OR_INVALID",
    )
    return rows[0]["limit"]


def verify_sources():
    require(
        all(
            Path(n).is_file() and hashlib.sha256(Path(n).read_bytes()).hexdigest() == h
            for n, h in NEW_PINS.items()
        ),
        "INSTALLED_SOURCE_HASH_MISMATCH",
    )


READ_PATHS = frozenset(
    {
        "/fapi/v1/time",
        "/fapi/v1/exchangeInfo",
        "/fapi/v1/positionSide/dual",
        "/fapi/v1/multiAssetsMargin",
        "/fapi/v1/symbolConfig",
        "/fapi/v3/account",
        "/fapi/v3/positionRisk",
        "/fapi/v1/openOrders",
        "/fapi/v1/openAlgoOrders",
        "/fapi/v1/order",
        "/fapi/v1/userTrades",
        "/fapi/v1/income",
    }
)
MAX_SECONDS = 75.0
MAX_HTTP_ATTEMPTS = 48
MAX_CLOCK_ATTEMPTS = 10
ROW_LIMIT = 1000
ENTRY = "28668115919"
CLOSE = "28668119491"
QUANTITY = Decimal("0.0007")
EXPECTED_TRADES = {
    "554837511": (ENTRY, "BUY", "75725.10", "0", "0.02120302", 1793947690123),
    "554838531": (CLOSE, "SELL", "75685.20", "-0.02793000", "0.02119185", 1793947973987),
}
TRADE_START = 1793947690122
CLOSE_MS = 1793947973987


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def utc(value):
    require(isinstance(value, str), "ADOPTION_TIME_MISSING")
    try:
        result = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise Blocked("ADOPTION_TIME_INVALID") from None
    require(
        result.tzinfo is not None and result.utcoffset() == timedelta(0), "ADOPTION_TIME_INVALID"
    )
    return result.astimezone(UTC)


def context(store, ledger, client):
    require(
        not client.settings.demo_trading_enabled
        and client.settings.policy == ledger.policy
        and client.settings.margin_mode == ledger.margin_mode == "ISOLATED"
        and client.settings.symbols == ledger.symbols == ("BTCUSDT",),
        "READ_ONLY_CONTEXT_REQUIRED",
    )
    receipt = json.loads(ledger.operations.get(RECEIPT, "{}"))
    require(isinstance(receipt, dict), "MANUAL_RECEIPT_INVALID")
    anchor = ledger.value.as_of
    adopted = utc(receipt.get("acknowledged_at_utc"))
    close = datetime.fromtimestamp(CLOSE_MS / 1000, UTC)
    now = store.now()
    day, week = period_keys(now, ledger.policy.week_start_kst)
    boundary = datetime.combine(
        now.astimezone(ZoneInfo("Asia/Seoul")).date(), daytime(), ZoneInfo("Asia/Seoul")
    ).astimezone(UTC)
    require(
        close < anchor <= adopted < boundary <= now
        and 0 <= (adopted - anchor).total_seconds() <= ledger.policy.max_snapshot_age_seconds
        and 0 < (now - close).total_seconds() <= 7 * 86400,
        "MANUAL_ANCHOR_WINDOW_UNSUPPORTED",
    )
    require(
        ledger.daily_key == anchor.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
        and ledger.daily_key < day
        and ledger.weekly_key == week,
        "DAY_OR_WEEK_CONTEXT_UNSUPPORTED",
    )
    require(
        ledger.income_since_ms is not None
        and ledger.income_since_ms <= int(anchor.timestamp() * 1000)
        and not ledger.income_seen
        and not ledger.net_transfers
        and not ledger.value.net_deposits,
        "NONZERO_OR_UNKNOWN_TRANSFER_BASIS",
    )
    require(
        isinstance(receipt.get("manual_close_client_id"), str)
        and receipt["manual_close_client_id"].startswith("web_")
        and receipt["manual_close_client_id"] not in ledger.requests
        and isinstance(receipt.get("original_order"), dict)
        and receipt["original_order"].get("client_id") == ledger.orders[INCIDENT].client_id
        and all(
            isinstance(receipt.get(k), str) and re.fullmatch("[0-9a-f]{64}", receipt[k])
            for k in ("prior_ledger_sha256", "exchange_fingerprint")
        ),
        "MANUAL_RECEIPT_PROVENANCE_INVALID",
    )
    return receipt, anchor, day, week, boundary


class DayPreviewClient(DemoClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.anchor_ms = None
        self.cutoff_ms = None

    def public(self, path, params=None):
        require(
            path in {"/fapi/v1/time", "/fapi/v1/exchangeInfo"} and not params,
            "PUBLIC_SCOPE_FORBIDDEN",
        )
        return super().public(path, params)

    def request(self, method, path, params=None):
        require(method == "GET" and path in READ_PATHS, "MUTATION_OR_ROUTE_FORBIDDEN")
        p = dict(params or {})
        if path == "/fapi/v1/order":
            require(
                set(p) == {"symbol", "orderId"}
                and p["symbol"] == "BTCUSDT"
                and p["orderId"] in {ENTRY, CLOSE},
                "ORDER_SCOPE_FORBIDDEN",
            )
        elif path in {"/fapi/v1/income", "/fapi/v1/userTrades"}:
            require(
                self.anchor_ms is not None and self.cutoff_ms is not None, "FIXED_WINDOW_REQUIRED"
            )
            expected = {
                "startTime": str(self.anchor_ms if path.endswith("income") else TRADE_START),
                "endTime": str(self.cutoff_ms),
                "limit": str(ROW_LIMIT),
            }
            if path.endswith("userTrades"):
                expected["symbol"] = "BTCUSDT"
            require(p == expected, "HISTORY_QUERY_SCOPE_FORBIDDEN")
        else:
            require(not p, "UNEXPECTED_QUERY_PARAMETERS")
        return super().request(method, path, p)

    def listen_key(self, *args, **kwargs):
        raise ProbeStop("LISTEN_KEY_FORBIDDEN")


def read_state(client, ledger):
    client.sync_clock()
    mode = client.request("GET", "/fapi/v1/positionSide/dual")
    assets = client.request("GET", "/fapi/v1/multiAssetsMargin")
    require(
        isinstance(mode, dict)
        and mode.get("dualSidePosition") is False
        and isinstance(assets, dict)
        and assets.get("multiAssetsMargin") is False,
        "ACCOUNT_MODE_UNSUPPORTED",
    )
    account = client.request("GET", "/fapi/v3/account")
    configs = client.request("GET", "/fapi/v1/symbolConfig")
    positions = client.request("GET", "/fapi/v3/positionRisk")
    regular = client.request("GET", "/fapi/v1/openOrders")
    algos = client.request("GET", "/fapi/v1/openAlgoOrders")
    require(
        isinstance(account, dict)
        and isinstance(account.get("assets"), list)
        and all(isinstance(x, list) for x in (configs, positions, regular, algos)),
        "ACCOUNT_RESPONSE_INVALID",
    )
    require(
        all(isinstance(r, dict) and r.get("positionSide") == "BOTH" for r in positions),
        "POSITION_SHAPE_INVALID",
    )
    position = {r["symbol"]: signed_number(r.get("positionAmt")) for r in positions}
    require(
        len(position) == len(positions)
        and not any(position.values())
        and not regular
        and not algos,
        "CURRENT_EXPOSURE_OR_OPEN_ORDER",
    )
    cfg = [r for r in configs if isinstance(r, dict) and r.get("symbol") == "BTCUSDT"]
    require(
        len(cfg) == 1
        and cfg[0].get("marginType") == "ISOLATED"
        and signed_number(cfg[0].get("leverage")) == 2
        and (cfg[0].get("isAutoAddMargin") is False or cfg[0].get("isAutoAddMargin") == "false"),
        "ISOLATED_SETTINGS_CHANGED",
    )
    usdt = [r for r in account["assets"] if isinstance(r, dict) and r.get("asset") == "USDT"]
    require(len(usdt) == 1, "USDT_ASSET_SCOPE_INVALID")
    wallet = signed_number(usdt[0].get("walletBalance"))
    require(
        wallet > 0
        and signed_number(usdt[0].get("marginBalance")) == wallet
        and signed_number(usdt[0].get("unrealizedProfit")) == 0
        and signed_number(account.get("totalWalletBalance")) == wallet
        and signed_number(account.get("totalMarginBalance")) == wallet
        and signed_number(account.get("totalUnrealizedProfit")) == 0
        and signed_number(account.get("totalInitialMargin")) == 0
        and signed_number(account.get("availableBalance")) == wallet,
        "USDT_BALANCE_OR_MARGIN_MISMATCH",
    )
    require(wallet == ledger.value.equity, "WALLET_CHANGED_FROM_ADOPTION")
    return {
        "wallet": wallet,
        "positions": position,
        "scope": "ISOLATED_BTCUSDT_LEVERAGE2_SINGLE_ASSET_ONEWAY",
    }


def signed_close(client, ledger, receipt):
    for identity, side, kind, reduce, price in [
        (ENTRY, "BUY", "LIMIT", False, "75725.10"),
        (CLOSE, "SELL", "MARKET", True, "75685.20"),
    ]:
        r = client.request("GET", "/fapi/v1/order", {"symbol": "BTCUSDT", "orderId": identity})
        require(
            isinstance(r, dict)
            and str(r.get("orderId")) == identity
            and r.get("symbol") == "BTCUSDT"
            and r.get("side") == side
            and r.get("positionSide") == "BOTH"
            and r.get("status") == "FILLED"
            and r.get("type") == kind
            and r.get("reduceOnly") is reduce
            and signed_number(r.get("origQty")) == signed_number(r.get("executedQty")) == QUANTITY
            and signed_number(r.get("avgPrice")) == Decimal(price),
            "SIGNED_ORDER_PROOF_MISMATCH",
        )
        if identity == ENTRY:
            require(
                r.get("clientOrderId") == ledger.orders[INCIDENT].client_id
                and signed_number(r.get("price"))
                == ledger.orders[INCIDENT].signal.suggested_limit_price,
                "ENTRY_IDENTITY_MISMATCH",
            )
        else:
            require(
                r.get("clientOrderId") == receipt["manual_close_client_id"]
                and type(r.get("time")) is int
                and r["time"] == CLOSE_MS
                and r.get("updateTime") == CLOSE_MS,
                "CLOSE_IDENTITY_OR_TIME_MISMATCH",
            )


def history(client):
    raw = client.request(
        "GET",
        "/fapi/v1/userTrades",
        {
            "symbol": "BTCUSDT",
            "startTime": str(TRADE_START),
            "endTime": str(client.cutoff_ms),
            "limit": str(ROW_LIMIT),
        },
    )
    require(isinstance(raw, list) and len(raw) < ROW_LIMIT, "TRADE_HISTORY_INCOMPLETE")
    require(
        all(isinstance(t, dict) for t in raw)
        and len(raw) == 2
        and {str(t.get("id")) for t in raw} == set(EXPECTED_TRADES),
        "EXTRA_OR_CHANGED_BTC_TRADES",
    )
    for t in raw:
        order, side, price, pnl, fee, stamp = EXPECTED_TRADES[str(t["id"])]
        require(
            t.get("symbol") == "BTCUSDT"
            and str(t.get("orderId")) == order
            and t.get("side") == side
            and t.get("positionSide") == "BOTH"
            and signed_number(t.get("qty")) == QUANTITY
            and signed_number(t.get("price")) == Decimal(price)
            and signed_number(t.get("realizedPnl")) == Decimal(pnl)
            and signed_number(t.get("commission")) == Decimal(fee)
            and t.get("commissionAsset") == "USDT"
            and type(t.get("time")) is int
            and t["time"] == stamp
            and TRADE_START <= stamp <= client.cutoff_ms,
            "SIGNED_TRADE_PROOF_MISMATCH",
        )
    incomes = client.request(
        "GET",
        "/fapi/v1/income",
        {
            "startTime": str(client.anchor_ms),
            "endTime": str(client.cutoff_ms),
            "limit": str(ROW_LIMIT),
        },
    )
    require(isinstance(incomes, list) and len(incomes) < ROW_LIMIT, "POST_ANCHOR_INCOME_INCOMPLETE")
    require(not incomes, "POST_ANCHOR_INCOME_PRESENT")


def preview(store, client, monitor, *, attest=False, lease=None):
    require(attest is True, "OPERATOR_ATTESTATION_REQUIRED")
    before = local(store, lease)
    ledger = before[-1]
    receipt, anchor, day, week, boundary = context(store, ledger, client)
    try:
        client.sync_clock()
        limit = published_limit(client.public("/fapi/v1/exchangeInfo"))
        monitor.enable(limit)
        client.anchor_ms = int(anchor.timestamp() * 1000)
        first = read_state(client, ledger)
        signed_close(client, ledger, receipt)
        client.cutoff_ms = client.clock_ms() + client.offset
        require(
            0 < client.cutoff_ms - TRADE_START <= 604800000, "SIGNED_HISTORY_WINDOW_UNSUPPORTED"
        )
        history(client)
        client.sync_clock()
        client.cutoff_ms = client.clock_ms() + client.offset
        history(client)
        final_state_started = store.now()
        final = read_state(client, ledger)
        require(first == final, "CURRENT_STATE_CHANGED")
        require(
            period_keys(store.now(), ledger.policy.week_start_kst) == (day, week),
            "PERIOD_CHANGED_DURING_PREVIEW",
        )
        wallet = final["wallet"]
        for base, cap in [
            (ledger.daily_base, "daily_loss_limit_pct"),
            (ledger.weekly_base, "weekly_loss_limit_pct"),
            (ledger.high_water, "max_drawdown_pct"),
        ]:
            require(
                base > 0 and (base - wallet) / base * 100 < ledger.policy.limit(cap),
                "EXISTING_LOSS_LIMIT_REACHED",
            )
        require(ledger.high_water >= wallet, "HIGH_WATER_REQUIRES_SEPARATE_REVIEW")
        probe = ledger.model_copy(deep=True)
        probe.daily_key, probe.daily_base = day, wallet
        probe.value = AccountValue(
            as_of=store.now(),
            initial=ledger.value.initial,
            realized=wallet - ledger.value.initial - ledger.net_transfers,
            net_deposits=ledger.net_transfers,
        )
        account_risk(probe, store.now())
        require(
            probe.risk_state == ledger.risk_state
            and probe.weekly_base == ledger.weekly_base
            and probe.high_water == ledger.high_water,
            "PROPOSED_RISK_GATES_FAILED",
        )
        require(local(store, lease) == before, "LOCAL_STATE_CHANGED")
        return {
            "manual_close_day_preview": "OPERATOR_ATTESTED_PREVIEW_NO_DATABASE_CHANGE",
            "source": "OPERATOR_ATTESTED_SIGNED_EXTERNAL_CLOSE_FLAT_ANCHOR",
            "old_day_kst": ledger.daily_key,
            "proposed_day_kst": day,
            "current_week_kst": week,
            "old_daily_base_usdt": str(ledger.daily_base),
            "proposed_daily_base_usdt": str(wallet),
            "weekly_base_usdt_preserved": str(ledger.weekly_base),
            "high_water_usdt_preserved": str(ledger.high_water),
            "boundary_utc": boundary.isoformat(),
            "adoption_snapshot_anchor_utc": anchor.isoformat(),
            "attested_since_manual_close_utc": datetime.fromtimestamp(
                CLOSE_MS / 1000, UTC
            ).isoformat(),
            "signed_history_cutoff_ms": client.cutoff_ms,
            "final_state_started_at_utc": final_state_started.isoformat(),
            "operator_attestation_used": True,
            "exact_midnight_valuation_verified": False,
            "account_wide_trade_export_verified": False,
            "signed_btc_trade_cohort_reverified": True,
            "post_anchor_income_empty": True,
            "current_exchange_empty_at_read_checks": True,
            "old_valuation_timestamp_preserved": True,
            "request_history_preserved": True,
            "manual_close_receipt_preserved": True,
            "daily_baseline_changed": False,
            "weekly_base_changed": False,
            "high_water_changed": False,
            "risk_state": ledger.risk_state.value,
            "paused": ledger.paused,
            "leader_acquired": False,
            "database_changed_by_preview": False,
            "order_writes": 0,
            "orders_enabled": False,
            "live_trading_enabled": False,
            "baseline_adoption_approved": False,
            "review_token_printed": False,
            "http_attempts": len(monitor.records),
            "clock_attempts": monitor.clock_attempts,
            "next_action": "REVIEW_VALUES_NO_APPLY_OR_RESUME",
        }
    finally:
        require(local(store, lease) == before, "LOCAL_STATE_CHANGED")


EXPECTED = {
    "old_day_kst": "2026-11-06",
    "proposed_day_kst": "2026-11-07",
    "current_week_kst": "2026-11-02",
    "old_daily_base_usdt": "4998.95579884",
    "proposed_daily_base_usdt": "4998.74183608",
    "weekly_base_usdt_preserved": "4998.99761493",
    "high_water_usdt_preserved": "5000.00000000",
    "adoption_snapshot_anchor_utc": "2026-11-06T07:25:25.109073+00:00",
}
APPLY_KEY = str(uuid5(UUID(INCIDENT), "operator-attested-external-close-day-2026-11-07-v1"))
OPERATION = "ADOPT_EXTERNAL_CLOSE_ATTESTED_DAY_BASELINE_V1"
SOURCE = "OPERATOR_ATTESTED_FLAT_WITH_SIGNED_USDT_EVIDENCE"


def reviewed(report, *, evidence=True):
    for key, value in EXPECTED.items():
        actual = report.get(key)
        if key.endswith("usdt") or key.endswith("preserved"):
            require(Decimal(str(actual)) == Decimal(value), "REVIEWED_VALUES_CHANGED")
        else:
            require(actual == value, "REVIEWED_VALUES_CHANGED")
    require(
        not evidence
        or (
            report.get("operator_attestation_used") is True
            and report.get("current_exchange_empty_at_read_checks") is True
            and report.get("post_anchor_income_empty") is True
            and report.get("exact_midnight_valuation_verified") is False
            and report.get("account_wide_trade_export_verified") is False
        ),
        "REVIEWED_EVIDENCE_CHANGED",
    )


def preserved(ledger):
    data = ledger.model_dump(mode="json")
    for key in ("daily_key", "daily_base", "stream_ready", "reconciled"):
        data.pop(key)
    for key in ("operations", "attested_day_baselines"):
        data[key].pop(APPLY_KEY, None)
    return digest(data)


def recorded(ledger):
    value = ledger.operations.get(APPLY_KEY)
    receipt = ledger.attested_day_baselines.get(APPLY_KEY)
    if value is None and receipt is None:
        require(
            APPLY_KEY not in ledger.recovery_receipts
            and APPLY_KEY not in ledger.cancelled_exposure_corrections
            and APPLY_KEY not in ledger.exit_anchored_day_baselines
            and APPLY_KEY not in ledger.attested_period_baselines,
            "DAY_OPERATION_CONFLICT",
        )
        return False
    require(value is not None and receipt is not None, "DAY_OPERATION_CONFLICT")
    try:
        record = json.loads(value)
    except (ValueError, TypeError):
        raise Blocked("DAY_OPERATION_CONFLICT") from None
    require(
        isinstance(record, dict)
        and record.get("operation") == OPERATION
        and record.get("reviewed_values") == EXPECTED
        and record.get("preserved_ledger_sha256") == preserved(ledger)
        and record.get("typed_receipt_sha256") == digest(receipt.model_dump(mode="json"))
        and receipt.review_token == digest(EXPECTED)
        and receipt.source == SOURCE
        and ledger.daily_key == EXPECTED["proposed_day_kst"]
        and ledger.daily_base == Decimal(EXPECTED["proposed_daily_base_usdt"]),
        "DAY_OPERATION_CONFLICT",
    )
    return True


def result(ledger, *, changed, released):
    return {
        "manual_close_day_adoption": "OPERATOR_ATTESTED_DAY_ADOPTED_ENTRIES_STILL_PAUSED"
        if changed
        else "ALREADY_RECORDED_NO_ACTION",
        "operation_id": APPLY_KEY,
        "source": "OPERATOR_ATTESTED_SIGNED_EXTERNAL_CLOSE_FLAT_ANCHOR",
        "day_kst": ledger.daily_key,
        "daily_base_usdt": str(ledger.daily_base),
        "weekly_base_usdt_preserved": str(ledger.weekly_base),
        "high_water_usdt_preserved": str(ledger.high_water),
        "risk_state": ledger.risk_state.value,
        "paused": ledger.paused,
        "database_changed": changed,
        "leader_released": released,
        "history_and_manual_close_receipt_preserved": True,
        "old_valuation_timestamp_preserved": True,
        "exact_midnight_valuation_verified": False,
        "account_wide_exchange_export_verified": False,
        "order_writes": 0,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }


def adopt(store, client, monitor, *, attest=False, progress=None):
    require(attest is True, "OPERATOR_ATTESTATION_REQUIRED")
    progress = progress if progress is not None else {}
    before = local(store)[-1]
    if recorded(before):
        return result(before, changed=False, released=True)
    # Bind the screenshot values before taking a lease or making any network call.
    _, anchor, day, week, _ = context(store, before, client)
    reviewed(
        {
            **EXPECTED,
            "old_day_kst": before.daily_key,
            "proposed_day_kst": day,
            "current_week_kst": week,
            "old_daily_base_usdt": str(before.daily_base),
            "proposed_daily_base_usdt": str(before.value.equity),
            "weekly_base_usdt_preserved": str(before.weekly_base),
            "high_water_usdt_preserved": str(before.high_water),
            "adoption_snapshot_anchor_utc": anchor.isoformat(),
        },
        evidence=False,
    )
    progress["lease_change_possible"] = True
    lease = store.acquire(ACCOUNT, uuid4())
    try:
        current = local(store, lease)[-1]
        require(
            current
            == before.model_copy(deep=True, update={"reconciled": False, "stream_ready": False}),
            "LOCAL_STATE_CHANGED",
        )

        # Hold the fenced row lock for proof and commit; all exchange requests are GET.
        def write(ledger):
            require(ledger == current and not recorded(ledger), "LOCAL_STATE_CHANGED")
            report = preview(store, client, monitor, attest=True, lease=lease)
            reviewed(report)
            monitor.check()
            require(
                0
                <= (
                    store.now() - datetime.fromisoformat(report["final_state_started_at_utc"])
                ).total_seconds()
                <= ledger.policy.max_snapshot_age_seconds,
                "FINAL_STATE_PROOF_STALE",
            )
            require(
                period_keys(store.now(), ledger.policy.week_start_kst)
                == (EXPECTED["proposed_day_kst"], EXPECTED["current_week_kst"]),
                "PERIOD_CHANGED_DURING_PREVIEW",
            )
            original_hash = preserved(ledger)
            proof_hash = digest({"report": report, "reviewed_values": EXPECTED})
            receipt = AttestedDayBaseline(
                review_token=digest(EXPECTED),
                source=SOURCE,
                boundary_utc=datetime.fromisoformat(report["boundary_utc"]),
                baseline_usdt=Decimal(EXPECTED["proposed_daily_base_usdt"]),
                old_daily_key=ledger.daily_key,
                old_daily_base=ledger.daily_base,
                anchor_utc=ledger.value.as_of,
                known_exit_utc=datetime.fromtimestamp(CLOSE_MS / 1000, UTC),
                attested_until_utc=store.now(),
                signed_evidence_hash=proof_hash,
                exchange_fingerprint=digest(
                    {
                        "scope": "GLOBAL_CURRENT_FLAT_USDT",
                        "wallet": EXPECTED["proposed_daily_base_usdt"],
                        "cutoff_ms": report["signed_history_cutoff_ms"],
                    }
                ),
            )
            ledger.daily_key = EXPECTED["proposed_day_kst"]
            ledger.daily_base = receipt.baseline_usdt
            ledger.attested_day_baselines[APPLY_KEY] = receipt
            ledger.operations[APPLY_KEY] = json.dumps(
                {
                    "operation": OPERATION,
                    "reviewed_values": EXPECTED,
                    "evidence_source": "OPERATOR_ATTESTED_SIGNED_EXTERNAL_CLOSE_FLAT_ANCHOR",
                    "preserved_ledger_sha256": original_hash,
                    "typed_receipt_sha256": digest(receipt.model_dump(mode="json")),
                    "signed_history_cutoff_ms": report["signed_history_cutoff_ms"],
                    "exact_midnight_valuation_verified": False,
                    "account_wide_exchange_export_verified": False,
                },
                sort_keys=True,
            )
            require(
                preserved(ledger) == original_hash and recorded(ledger), "UNRELATED_LEDGER_CHANGE"
            )

        progress["application_may_have_completed"] = True
        store.change(lease, "DEMO_EXTERNAL_CLOSE_ATTESTED_DAY_BASELINE", write)
    finally:
        store.release(lease)
    after = local(store)[-1]
    require(recorded(after), "POSTCHECK_FAILED")
    return result(after, changed=True, released=True)


def blocked(reason, progress=None):
    return {
        "manual_close_day_adoption": "BLOCKED_KEEP_PAUSED_NO_AUTOMATIC_RETRY",
        "stop_reason": reason,
        "application_may_have_completed": bool(
            (progress or {}).get("application_may_have_completed")
        ),
        "lease_change_possible": bool((progress or {}).get("lease_change_possible")),
        "order_writes": 0,
        "orders_enabled": False,
        "live_trading_enabled": False,
        "next_action": "SHARE_CODE_NO_RETRY_OR_RESUME",
    }


def safe_reason(error):
    if isinstance(error, DemoRateLimited):
        return "RATE_LIMITED_NO_RETRY"
    if isinstance(error, (Blocked, ProbeStop)) and str(error) in STOP_CODES:
        return str(error)
    if isinstance(error, DemoFailure):
        return "SIGNED_QUERY_UNVERIFIED"
    return "CHECK_UNAVAILABLE"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--attest-no-activity-since-manual-close", action="store_true", required=True
    )
    parser.add_argument("--apply", action="store_true", required=True)
    args = parser.parse_args()
    engine = route = None
    code = 2
    progress = {}
    report = blocked("CHECK_UNAVAILABLE")
    try:
        require(
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"
            and os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE",
            "LIVE_CONTEXT_FORBIDDEN",
        )
        verify_sources()
        cfg = DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
            )
        ).model_copy(update={"demo_trading_enabled": False})
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        store = DemoStore(engine)
        # Already-committed outcome is recognizable without credentials/network.
        initial = local(store)[-1]
        if recorded(initial):
            report = result(initial, changed=False, released=True)
        else:
            route = TPReadRouting(transport)
            monitor = Monitor(route)
            try:
                key = credentials(prompt=False)
            except Exception:
                raise Blocked("DEMO_CREDENTIAL_SESSION_UNAVAILABLE") from None
            client = DayPreviewClient(cfg, key, send=monitor)
            monitor.server_ms = lambda: client.clock_ms() + client.offset
            report = adopt(
                store,
                client,
                monitor,
                attest=args.attest_no_activity_since_manual_close,
                progress=progress,
            )
        code = 0
    except Exception as error:
        report = blocked(safe_reason(error), progress)
    finally:
        failed = False
        if route is not None:
            try:
                route.close()
            except Exception:
                failed = True
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                failed = True
        if failed:
            report = blocked("RESOURCE_CLEANUP_UNVERIFIED", progress)
            code = 2
    print(json.dumps(report), flush=True)
    raise SystemExit(code)


STOP_CODES = frozenset(
    [
        "ACCOUNT_MODE_UNSUPPORTED",
        "ACCOUNT_RESPONSE_INVALID",
        "ACTIVE_OWNER_BLOCKED",
        "ADOPTION_TIME_INVALID",
        "ADOPTION_TIME_MISSING",
        "CLOCK_BUDGET_REACHED",
        "CLOSE_IDENTITY_OR_TIME_MISMATCH",
        "CURRENT_EXPOSURE_OR_OPEN_ORDER",
        "CURRENT_STATE_CHANGED",
        "DAY_OPERATION_CONFLICT",
        "DAY_OR_WEEK_CONTEXT_UNSUPPORTED",
        "DEMO_CREDENTIAL_SESSION_UNAVAILABLE",
        "ENTRY_IDENTITY_MISMATCH",
        "EXISTING_LOSS_LIMIT_REACHED",
        "EXTRA_OR_CHANGED_BTC_TRADES",
        "FAILED_TRIAL_MARKER_CHANGED",
        "FINAL_STATE_PROOF_STALE",
        "FIXED_WINDOW_REQUIRED",
        "HIGH_WATER_REQUIRES_SEPARATE_REVIEW",
        "HISTORY_QUERY_SCOPE_FORBIDDEN",
        "HTTP_BUDGET_REACHED",
        "INITIALIZED_LEDGER_REQUIRED",
        "INSTALLED_SOURCE_HASH_MISMATCH",
        "ISOLATED_SETTINGS_CHANGED",
        "LEASE_EXPIRED_OR_FENCED",
        "LIMIT_DISCOVERY_REQUIRED",
        "LISTEN_KEY_FORBIDDEN",
        "LIVE_CONTEXT_FORBIDDEN",
        "LOCAL_STATE_CHANGED",
        "MANUAL_ANCHOR_WINDOW_UNSUPPORTED",
        "MANUAL_CLOSE_LOCAL_SCOPE_MISMATCH",
        "MANUAL_CLOSE_RECEIPT_MISMATCH",
        "MANUAL_RECEIPT_INVALID",
        "MANUAL_RECEIPT_PROVENANCE_INVALID",
        "MUTATION_OR_ROUTE_FORBIDDEN",
        "NONZERO_OR_UNKNOWN_TRANSFER_BASIS",
        "NON_READ_OR_UNKNOWN_ROUTE_FORBIDDEN",
        "OPERATOR_ATTESTATION_REQUIRED",
        "ORDER_SCOPE_FORBIDDEN",
        "PERIOD_CHANGED_DURING_PREVIEW",
        "POSITION_SHAPE_INVALID",
        "POSTCHECK_FAILED",
        "POST_ANCHOR_INCOME_INCOMPLETE",
        "POST_ANCHOR_INCOME_PRESENT",
        "PROPOSED_RISK_GATES_FAILED",
        "PUBLIC_SCOPE_FORBIDDEN",
        "PUBLISHED_LIMIT_AMBIGUOUS_OR_INVALID",
        "PUBLISHED_LIMIT_INVALID",
        "PUBLISHED_LIMIT_UNAVAILABLE",
        "RATE_LIMIT_ALREADY_OBSERVED",
        "READ_ONLY_CONTEXT_REQUIRED",
        "REVIEWED_EVIDENCE_CHANGED",
        "REVIEWED_VALUES_CHANGED",
        "SIGNED_HISTORY_WINDOW_UNSUPPORTED",
        "SIGNED_ORDER_PROOF_MISMATCH",
        "SIGNED_TRADE_PROOF_MISMATCH",
        "TIME_BUDGET_REACHED",
        "TRADE_HISTORY_INCOMPLETE",
        "UNEXPECTED_QUERY_PARAMETERS",
        "UNRELATED_LEDGER_CHANGE",
        "USDT_ASSET_SCOPE_INVALID",
        "USDT_BALANCE_OR_MARGIN_MISMATCH",
        "WALLET_CHANGED_FROM_ADOPTION",
        "WEIGHT_HEADER_UNAVAILABLE",
        "WEIGHT_HEADROOM_THRESHOLD",
    ]
)

if __name__ == "__main__":
    main()
