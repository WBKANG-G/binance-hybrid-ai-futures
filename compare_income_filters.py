"""Standalone fixed-window GET-only income filter comparison. Never switches runtime policy."""

import hashlib
import json
import os
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID, uuid5

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
from services.execution.demo_models import DemoLedger
from services.execution.demo_recovery import local_period_status

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


def local(store):
    with store.engine.connect() as conn:
        conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout='3s'")
        row = (
            conn.execute(select(accounts).where(accounts.c.account_id == ACCOUNT)).mappings().one()
        )
    state = DemoLedger.model_validate(row["state"])
    require(row["owner"] is None, "ACTIVE_OWNER_BLOCKED")
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
        "/fapi/v3/positionRisk",
        "/fapi/v1/openOrders",
        "/fapi/v1/openAlgoOrders",
        "/fapi/v1/income",
    }
)
MAX_SECONDS = 75.0
MAX_HTTP_ATTEMPTS = 32
MAX_CLOCK_ATTEMPTS = 10
ROW_LIMIT = 1000
WINDOW_MS = 86400000
SETTLE_MS = 10000
PUBLIC_INCOME_TYPES = frozenset(
    [
        "API_REBATE",
        "AUTO_EXCHANGE",
        "BFUSD_REWARD",
        "COIN_SWAP_DEPOSIT",
        "COIN_SWAP_WITHDRAW",
        "COMMISSION",
        "COMMISSION_REBATE",
        "CONTEST_REWARD",
        "CROSS_COLLATERAL_TRANSFER",
        "DELIVERED_SETTELMENT",
        "FEE_RETURN",
        "FUNDING_FEE",
        "INSURANCE_CLEAR",
        "INTERNAL_TRANSFER",
        "OPTIONS_PREMIUM_FEE",
        "OPTIONS_SETTLE_PROFIT",
        "POSITION_LIMIT_INCREASE_FEE",
        "REALIZED_PNL",
        "REFERRAL_KICKBACK",
        "SPECIAL_FUNDING_FEE",
        "STRATEGY_UMFUTURES_TRANSFER",
        "TRANSFER",
        "WELCOME_BONUS",
    ]
)


class IncomeCompareClient(DemoClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.window = None

    def public(self, path, params=None):
        require(
            path in {"/fapi/v1/time", "/fapi/v1/exchangeInfo"} and not params,
            "PUBLIC_SCOPE_FORBIDDEN",
        )
        return super().public(path, params)

    def request(self, method, path, params=None):
        require(method == "GET", "MUTATION_FORBIDDEN")
        values = dict(params or {})
        if path == "/fapi/v1/income":
            require(self.window is not None, "FIXED_WINDOW_REQUIRED")
            expected = {
                "startTime": str(self.window[0]),
                "endTime": str(self.window[1]),
                "limit": str(ROW_LIMIT),
            }
            require(
                set(values) in (set(expected), set(expected) | {"incomeType"})
                and all(values.get(k) == v for k, v in expected.items())
                and (
                    "incomeType" not in values
                    or values["incomeType"] in {"TRANSFER", "WELCOME_BONUS"}
                ),
                "INCOME_QUERY_SCOPE_FORBIDDEN",
            )
        else:
            require(
                path in {"/fapi/v3/positionRisk", "/fapi/v1/openOrders", "/fapi/v1/openAlgoOrders"}
                and not values,
                "SIGNED_SCOPE_FORBIDDEN",
            )
        return super().request(method, path, values)

    def listen_key(self, *args, **kwargs):
        raise ProbeStop("LISTEN_KEY_FORBIDDEN")


def current_flat(client):
    client.sync_clock()
    positions = client.request("GET", "/fapi/v3/positionRisk")
    regular = client.request("GET", "/fapi/v1/openOrders")
    algos = client.request("GET", "/fapi/v1/openAlgoOrders")
    require(
        isinstance(positions, list) and isinstance(regular, list) and isinstance(algos, list),
        "CURRENT_STATE_SHAPE_INVALID",
    )
    require(
        all(isinstance(row, dict) and "positionAmt" in row for row in positions),
        "CURRENT_POSITION_SHAPE_INVALID",
    )
    require(
        not any(signed_number(row["positionAmt"]) for row in positions)
        and not regular
        and not algos,
        "CURRENT_EXCHANGE_NOT_EMPTY",
    )


def canonical_rows(rows, window, expected_type=None):
    require(isinstance(rows, list) and len(rows) <= ROW_LIMIT, "INCOME_RESPONSE_SHAPE_INVALID")
    require(len(rows) < ROW_LIMIT, "INCOME_RESULT_AT_LIMIT_COVERAGE_UNPROVEN")
    result = {}
    for row in rows:
        require(isinstance(row, dict), "INCOME_ROW_INVALID")
        kind, identifier, asset, stamp = (
            row.get(k) for k in ("incomeType", "tranId", "asset", "time")
        )
        require(
            isinstance(kind, str)
            and 1 <= len(kind) <= 64
            and kind.isascii()
            and kind.replace("_", "").isalnum()
            and kind.upper() == kind,
            "INCOME_TYPE_INVALID",
        )
        require(expected_type is None or kind == expected_type, "FILTER_IGNORED_OR_WRONG_TYPE")
        require(
            type(identifier) in {int, str}
            and str(identifier).isascii()
            and str(identifier).isdecimal()
            and len(str(identifier)) <= 32,
            "INCOME_ID_INVALID",
        )
        require(
            isinstance(asset, str)
            and 1 <= len(asset) <= 32
            and asset.isascii()
            and asset.isalnum()
            and asset.upper() == asset,
            "INCOME_ASSET_INVALID",
        )
        require(
            type(stamp) is int and window[0] <= stamp <= window[1],
            "INCOME_TIME_OUTSIDE_FIXED_WINDOW",
        )
        amount = signed_number(row.get("income"))
        if kind in {"TRANSFER", "WELCOME_BONUS"}:
            require(asset == "USDT", "TRANSFER_ASSET_UNSUPPORTED")
        key = kind, str(int(identifier))
        require(key not in result, "DUPLICATE_INCOME_ID")
        result[key] = asset, amount, stamp
    return result


def scan(client, monitor, kind=None):
    client.sync_clock()
    start = len(monitor.records)
    params = {
        "startTime": str(client.window[0]),
        "endTime": str(client.window[1]),
        "limit": str(ROW_LIMIT),
    }
    if kind is not None:
        params["incomeType"] = kind
    rows = canonical_rows(client.request("GET", "/fapi/v1/income", params), client.window, kind)
    records = monitor.records[start:]
    deltas = [r["counter_delta"] for r in records]
    return rows, {
        "label": kind or "UNFILTERED",
        "http_attempts": len(records),
        "rows": len(rows),
        "observed_counter_delta_sum": sum(deltas) if all(x is not None for x in deltas) else None,
        "counter_reset_or_window_change": any(r["counter_reset_or_window_change"] for r in records),
        "clock_warmup_excluded": True,
    }


def compare(store, client, monitor):
    before = local(store)
    ledger = before[-1]
    require(
        not client.settings.demo_trading_enabled
        and client.settings.margin_mode == ledger.margin_mode == "ISOLATED"
        and client.settings.symbols == ledger.symbols == ("BTCUSDT",)
        and client.settings.policy == ledger.policy,
        "READ_ONLY_CONTEXT_REQUIRED",
    )
    period = local_period_status(store, ledger)
    try:
        client.sync_clock()
        limit = published_limit(client.public("/fapi/v1/exchangeInfo"))
        monitor.enable(limit)
        current_flat(client)
        end = client.clock_ms() + client.offset - SETTLE_MS
        start = max(ledger.income_since_ms, end - WINDOW_MS)
        require(type(start) is int and 0 <= start < end, "NO_FINISHED_INCOME_WINDOW")
        client.window = start, end
        whole_first, a = scan(client, monitor)
        transfer, b = scan(client, monitor, "TRANSFER")
        bonus, c = scan(client, monitor, "WELCOME_BONUS")
        whole_second, d = scan(client, monitor)
        require(whole_first == whole_second, "UNFILTERED_WINDOW_CHANGED")
        projection = {k: v for k, v in whole_first.items() if k[0] in {"TRANSFER", "WELCOME_BONUS"}}
        require(projection == transfer | bonus, "FILTERED_UNION_MISMATCH")
        current_flat(client)
        require(local(store) == before, "LOCAL_STATE_CHANGED")
        count_transfer, len_bonus = len(transfer), len(bonus)
        return {
            "income_filter_review": "BOUNDED_WINDOW_MATCH",
            "window_start_ms": start,
            "window_end_ms": end,
            "maximum_window_hours": 24,
            "window_scope": "LOCAL_INCOME_START_OR_LAST24H_ONLY",
            "server_settle_margin_ms": SETTLE_MS,
            "unfiltered_runs_match": True,
            "filtered_union_matches": True,
            "transfer_rows": count_transfer,
            "welcome_bonus_rows": len_bonus,
            "other_income_rows": len(whole_first) - len(projection),
            "non_target_type_counts": {
                kind: sum(key[0] == kind for key in whole_first)
                for kind in sorted(PUBLIC_INCOME_TYPES - {"TRANSFER", "WELCOME_BONUS"})
                if any(key[0] == kind for key in whole_first)
            },
            "unknown_income_type_rows": sum(
                key[0] not in PUBLIC_INCOME_TYPES for key in whole_first
            ),
            "target_types_are_current_ledger_logic_not_all_financial_classification": True,
            "both_target_types_observed": bool(count_transfer and len_bonus),
            "event_type_coverage": "BOTH_TYPES_OBSERVED"
            if count_transfer and len_bonus
            else "TARGET_TYPES_INCOMPLETE",
            "period_keys_match": period["period_keys_match"],
            "period_mismatch_does_not_authorize_trading": True,
            "scan_sequence": [a, b, c, d],
            "http_attempts": len(monitor.records),
            "clock_attempts": monitor.clock_attempts,
            "published_weight_limit_1m": limit,
            "max_observed_weight": max(
                (r["used_weight_1m"] or 0 for r in monitor.records), default=0
            ),
            "weight_deltas_include_other_ip_activity": True,
            "observed_deltas_are_not_weight_proof": True,
            "current_exchange_empty_at_read_checks": True,
            "local_state_unchanged": True,
            "database_changed_by_probe": False,
            "leader_acquired": False,
            "order_writes": 0,
            "orders_enabled": False,
            "live_trading_enabled": False,
            "optimization_approved": False,
            "next_action": "REVIEW_ONLY_NO_RUNTIME_SWITCH",
        }
    finally:
        require(local(store) == before, "LOCAL_STATE_CHANGED")


def safe_reason(error):
    if isinstance(error, DemoRateLimited):
        return "RATE_LIMITED_NO_RETRY"
    if isinstance(error, (Blocked, ProbeStop)) and str(error) in STOP_CODES:
        return str(error)
    if isinstance(error, DemoFailure):
        return "SIGNED_QUERY_OR_FILTER_SUPPORT_UNVERIFIED"
    return "CHECK_UNAVAILABLE"


def blocked(reason):
    return {
        "income_filter_review": "BLOCKED_KEEP_PAUSED_NO_RETRY",
        "stop_reason": reason,
        "database_changed_by_probe": False,
        "leader_acquired": False,
        "order_writes": 0,
        "orders_enabled": False,
        "live_trading_enabled": False,
        "optimization_approved": False,
        "next_action": "REVIEW_ONLY_NO_RUNTIME_SWITCH",
    }


def main():
    engine = route = None
    code = 2
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
        local(store)
        route = TPReadRouting(transport)
        monitor = Monitor(route)
        try:
            key = credentials(prompt=False)
        except Exception:
            raise Blocked("DEMO_CREDENTIAL_SESSION_UNAVAILABLE") from None
        client = IncomeCompareClient(cfg, key, send=monitor)
        monitor.server_ms = lambda: client.clock_ms() + client.offset
        report = compare(store, client, monitor)
        code = 0
    except Exception as error:
        report = blocked(safe_reason(error))
    finally:
        cleanup_failed = False
        if route is not None:
            try:
                route.close()
            except Exception:
                cleanup_failed = True
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                cleanup_failed = True
        if cleanup_failed:
            report = blocked("RESOURCE_CLEANUP_UNVERIFIED")
            code = 2
    print(json.dumps(report), flush=True)
    raise SystemExit(code)


STOP_CODES = frozenset(
    [
        "DEMO_CREDENTIAL_SESSION_UNAVAILABLE",
        "ACTIVE_OWNER_BLOCKED",
        "CLOCK_BUDGET_REACHED",
        "CURRENT_EXCHANGE_NOT_EMPTY",
        "CURRENT_POSITION_SHAPE_INVALID",
        "CURRENT_STATE_SHAPE_INVALID",
        "DUPLICATE_INCOME_ID",
        "FAILED_TRIAL_MARKER_CHANGED",
        "FILTERED_UNION_MISMATCH",
        "FILTER_IGNORED_OR_WRONG_TYPE",
        "FIXED_WINDOW_REQUIRED",
        "HTTP_BUDGET_REACHED",
        "INCOME_ASSET_INVALID",
        "INCOME_ID_INVALID",
        "INCOME_QUERY_SCOPE_FORBIDDEN",
        "INCOME_RESPONSE_SHAPE_INVALID",
        "INCOME_RESULT_AT_LIMIT_COVERAGE_UNPROVEN",
        "INCOME_ROW_INVALID",
        "INCOME_TIME_OUTSIDE_FIXED_WINDOW",
        "INCOME_TYPE_INVALID",
        "INITIALIZED_LEDGER_REQUIRED",
        "INSTALLED_SOURCE_HASH_MISMATCH",
        "LIMIT_DISCOVERY_REQUIRED",
        "LISTEN_KEY_FORBIDDEN",
        "LIVE_CONTEXT_FORBIDDEN",
        "LOCAL_STATE_CHANGED",
        "MANUAL_CLOSE_LOCAL_SCOPE_MISMATCH",
        "MANUAL_CLOSE_RECEIPT_MISMATCH",
        "MUTATION_FORBIDDEN",
        "NON_READ_OR_UNKNOWN_ROUTE_FORBIDDEN",
        "NO_FINISHED_INCOME_WINDOW",
        "PUBLIC_SCOPE_FORBIDDEN",
        "PUBLISHED_LIMIT_AMBIGUOUS_OR_INVALID",
        "PUBLISHED_LIMIT_INVALID",
        "PUBLISHED_LIMIT_UNAVAILABLE",
        "RATE_LIMIT_ALREADY_OBSERVED",
        "READ_ONLY_CONTEXT_REQUIRED",
        "SIGNED_SCOPE_FORBIDDEN",
        "TIME_BUDGET_REACHED",
        "TRANSFER_ASSET_UNSUPPORTED",
        "UNFILTERED_WINDOW_CHANGED",
        "WEIGHT_HEADER_UNAVAILABLE",
        "WEIGHT_HEADROOM_THRESHOLD",
    ]
)

if __name__ == "__main__":
    main()
