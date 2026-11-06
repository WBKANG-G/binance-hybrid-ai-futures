"""Bounded GET-only, no-lease observation of published request weight and idle read shape."""

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
from integrations.binance_usdm.demo_snapshot import operational_snapshot
from integrations.binance_usdm.recovery_transport import GET_PATHS
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import DemoStore, accounts
from scripts.demo_key_session import credentials
from services.execution.demo_empty_db_restart import empty
from services.execution.demo_models import DemoLedger
from services.execution.demo_recovery import _check_loss_gates, local_period_status

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


ROUNDS = 3
MAX_SECONDS = 45.0
MAX_HTTP_ATTEMPTS = 160
MAX_CLOCK_ATTEMPTS = 6


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
        if method != "GET" or path not in GET_PATHS | {"/fapi/v1/exchangeInfo"}:
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


def observe(store, client, monitor, *, clock=time.monotonic, wait=time.sleep):
    before = local(store)
    ledger = before[-1]
    require(
        not client.settings.demo_trading_enabled
        and client.settings.margin_mode == ledger.margin_mode == "ISOLATED"
        and client.settings.symbols == ledger.symbols == ("BTCUSDT",)
        and client.settings.policy == ledger.policy,
        "READ_ONLY_CONTEXT_REQUIRED",
    )
    require(
        local_period_status(store, ledger)["period_keys_match"] is True, "PERIOD_BASELINE_MISMATCH"
    )
    report = {
        "read_weight_probe": "READ_ONLY_OBSERVATION_FINISHED",
        "rounds_requested": ROUNDS,
        "rounds": [],
        "request_weight_limit_1m": None,
        "limit_source": "exchangeInfo.rateLimits",
        "headroom_stop_percent": 60,
        "additional_weight_reserve_max": 100,
        "http_attempt_cap": MAX_HTTP_ATTEMPTS,
        "request_start_deadline_seconds": MAX_SECONDS,
        "transport_timeout_seconds": 5,
        "database_changed_by_probe": False,
        "leader_acquired": False,
        "order_writes": 0,
        "signal_generated": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
        "next_action": "REVIEW_ONLY_NO_ORDER_FROM_THIS_REPORT",
    }
    try:
        client.sync_clock()
        info = client.public("/fapi/v1/exchangeInfo")
        limit = published_limit(info)
        report["request_weight_limit_1m"] = limit
        client.sync_clock()  # Obtain a fresh baseline after public metadata discovery.
        monitor.enable(limit)
        interval = min(15.0, ledger.policy.max_snapshot_age_seconds / 2)
        report["idle_start_interval_seconds"] = interval
        origin = clock()
        previous_start = None
        for index in range(ROUNDS):
            target = origin + index * interval
            if previous_start is not None:
                target = max(target, previous_start + interval)
            delay = target - clock()
            if delay > 0:
                monitor.check()
                remaining = max(0.0, MAX_SECONDS - (clock() - monitor.started))
                wait(min(delay, remaining))
            monitor.check()
            require(local(store) == before, "LOCAL_STATE_CHANGED")
            require(
                local_period_status(store, ledger)["period_keys_match"] is True,
                "PERIOD_BASELINE_MISMATCH",
            )
            start = clock()
            previous_start = start
            offset = len(monitor.records)
            client.sync_clock()
            samples = [operational_snapshot(client, ledger, store.now()) for _ in range(3)]
            require(
                len({s.fingerprint for s in samples}) == 1
                and all(s.transfers == ledger.income_seen for s in samples),
                "SNAPSHOT_OR_TRANSFERS_CHANGED",
            )
            for snap in samples:
                require(
                    not any(snap.positions.values())
                    and not snap.margin_used
                    and not any(
                        r["status"] in {"NEW", "PARTIALLY_FILLED"} for r in snap.orders.values()
                    )
                    and not any(r["algoStatus"] == "NEW" for r in snap.algos.values())
                    and snap.leverage.get("BTCUSDT") == 2,
                    "EXCHANGE_EMPTY_OR_SETTINGS_NOT_VERIFIED",
                )
            _check_loss_gates(store, ledger, samples[-1].equity)
            monitor.check()
            require(local(store) == before, "LOCAL_STATE_CHANGED")
            report["rounds"].append(
                {
                    "round": index + 1,
                    "started_after_discovery_ms": max(0, int((start - origin) * 1000)),
                    "elapsed_ms": max(0, int((clock() - start) * 1000)),
                    "operational_snapshots": 3,
                    "http_attempts": len(monitor.records) - offset,
                    "remote_empty_verified": True,
                    "last_observed_weight": monitor.latest_weight,
                }
            )
    except ProbeStop as error:
        report["read_weight_probe"] = "STOPPED_KEEP_PAUSED_NO_RETRY"
        report["stop_reason"] = error.code
    except DemoRateLimited:
        report["read_weight_probe"] = "RATE_LIMITED_STOPPED_KEEP_PAUSED"
        report["stop_reason"] = "HTTP_RATE_LIMIT_OBSERVED_NO_RETRY"
    except Exception as error:
        report["read_weight_probe"] = "BLOCKED_KEEP_PAUSED_NO_RETRY"
        report["stop_reason"] = (
            str(error) if isinstance(error, Blocked) else "QUERY_OR_CONTEXT_NOT_VERIFIED"
        )
    # SQL-only final check; no retrying network requests after any stop condition.
    require(local(store) == before, "LOCAL_STATE_CHANGED")
    report.update(
        local_state_unchanged=True,
        completed_rounds=len(report["rounds"]),
        http_attempts=len(monitor.records),
        signed_gets=sum(r["signed"] for r in monitor.records),
        elapsed_ms=max(0, int((clock() - monitor.started) * 1000)),
        rate_limit_diagnostic=client.rate_limit_diagnostics(),
        request_samples=monitor.records,
    )
    report["counter_deltas_include_other_ip_activity"] = True
    report["reset_deltas_omitted"] = True
    report["active_position_or_private_stream_load_measured"] = False
    return report


def compact_report(report):
    result = {key: value for key, value in report.items() if key != "request_samples"}
    samples = report.get("request_samples", [])
    groups = []
    for path in sorted({row["path"] for row in samples}):
        rows = [row for row in samples if row["path"] == path]
        deltas = [row["counter_delta"] for row in rows if row["counter_delta"] is not None]
        groups.append(
            {
                "path": path,
                "http_attempts": len(rows),
                "observed_delta_min": min(deltas) if deltas else None,
                "observed_delta_max": max(deltas) if deltas else None,
            }
        )
    result["response_groups"] = groups
    result["counter_reset_or_window_change_count"] = sum(
        row["counter_reset_or_window_change"] for row in samples
    )
    result["observed_deltas_are_not_endpoint_weight_proof"] = True
    return result


def probe_main():
    engine = route = None
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
        local(store)  # No exchange traffic if receipt/local safety state is not ready.
        route = TPReadRouting(transport)
        monitor = Monitor(route)
        client = DemoClient(cfg, credentials(prompt=False), send=monitor)
        monitor.server_ms = lambda: client.clock_ms() + client.offset
        report = observe(store, client, monitor)
        code = 0 if report["read_weight_probe"] == "READ_ONLY_OBSERVATION_FINISHED" else 2
    except Exception as error:
        report = {
            "read_weight_probe": "BLOCKED_KEEP_PAUSED_NO_RETRY",
            "stop_reason": str(error) if isinstance(error, Blocked) else "CHECK_UNAVAILABLE",
            "database_changed_by_probe": False,
            "order_writes": 0,
            "orders_enabled": False,
            "live_trading_enabled": False,
        }
        code = 2
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
            report = {
                "read_weight_probe": "BLOCKED_KEEP_PAUSED_NO_RETRY",
                "stop_reason": "RESOURCE_CLEANUP_UNVERIFIED",
                "database_changed_by_probe": False,
                "order_writes": 0,
                "orders_enabled": False,
                "live_trading_enabled": False,
            }
            code = 2
    print(json.dumps(compact_report(report)), flush=True)
    raise SystemExit(code)


if __name__ == "__main__":
    probe_main()
