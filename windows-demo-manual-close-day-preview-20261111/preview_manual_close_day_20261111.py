"""Read-only November 11 day preview; preserve the recorded November 9 week and history."""

# SDK imports follow the installed-cohort check when executed via stdin.
# ruff: noqa: E402
import hashlib
import json
from pathlib import Path

INSTALLED_PINS = {
    "alembic.ini": "e7955a0ffb9c32a9c5067d7885ee9197328f17a4a12fb4a5bb556c2b85763aa2",
    "configs/demo-isolated.example.yaml": "d93e85c2e056ffbbc3dac5fab9f0e5bf28032fd16f41bbff1f3ae81fb00882ca",
    "integrations/binance_usdm/demo.py": "767d3a29b59882035bdfbf61d3271b39bfece0bff4b359e636db62ad9a5e84eb",
    "integrations/binance_usdm/demo_rate_limits.py": "d72af60f10a74baea689e11ef8f11b5003f2ab9f89e539290c45b93f2f3d4a4e",
    "integrations/binance_usdm/demo_read_routing.py": "a6e96b21135882a272cb5c97f86ddc14c3a5f4fa79dd115e40397252c679a66d",
    "integrations/binance_usdm/demo_snapshot.py": "f80f6e51505ab1095c25b6ee76af01c258cff911aabd02c201bfcddc636f2258",
    "integrations/binance_usdm/demo_tp_admission.py": "c6a66aff461f2aab59d2f2ae1f42a44c6388268d93893ecf4854c3af111ee0d8",
    "integrations/binance_usdm/demo_tp_weight_model.py": "611948a52ffad41ecc448549f530558cd4df2585e23273d23422c8a995c7cbc3",
    "integrations/binance_usdm/demo_tp_window_budget.py": "9433a307767a2e60a4cdbc31a85a8cb386066ff9799249239633ead1cf76120b",
    "integrations/binance_usdm/filters.py": "0279613f1a17a2bc7ffd50e30be2b6fd1aed8ba240219e8e893b754a067d95ec",
    "integrations/binance_usdm/orders.py": "2ab366265f7d35356ee9636c6aad4b594b518beeb636407515a57386b3e8441f",
    "integrations/binance_usdm/recovery_transport.py": "7495c0436437070644627d5ee175d7a63332dc5dc2134574285a7870e8bbba77",
    "integrations/binance_usdm/rest.py": "9a14edc40fa6365a50fe57c9ddeff4e866044e00287e7c5b680d8bb3dbd0ad52",
    "migrations/env.py": "4256345fc24dd52ba706aa9b712268b9df436d52bafa7238234272d754734086",
    "migrations/script.py.mako": "67042994cfc495007c508ccae15e44d21057d90222461630f9bc04d3657f5f6d",
    "migrations/versions/0001_public_market_data.py": "b62afcb18d6d6c42d710ca0cb0eb42001e174934f11678443dc5ac24d86932f7",
    "migrations/versions/0002_paper.py": "91ffad2a439b9f47e2636939a5aba091490942db7a87bb0d87124a531870c77d",
    "migrations/versions/0003_demo.py": "6b2a26ba34855a3dee6d3b2c67eb9b3ecac644b4e2e31ad6353e907a3c233762",
    "migrations/versions/0004_demo_urgent_alerts.py": "da9e4de16f1f9d4c15d70293d3b252084648fbbcb060f8ec7740b9a85f83e8ad",
    "packages/domain/config.py": "c2bd7a039e3ea007a1e34a6dbdd058cc626e2690cc8d55b0761cfd2085bf311d",
    "packages/domain/contracts.py": "71c0a95faad586d6e3f342d83b6b95a4772cb6e2c1808b44fc51fdf9f1332947",
    "packages/persistence/demo.py": "40f9a33b8eeda28c96f36de35656e4667a2d2461ce9de79dc4b19a687cd5cd5a",
    "pyproject.toml": "54760a9fa52fc4cc877d2302c7952688ddd13e12f1efc8e44a0974aae8914019",
    "scripts/check_demo_disconnect.py": "c85c013412053237eda7ed82a8948aa4b50f9da1951d173360a1ecfb46228a1e",
    "scripts/check_demo_session_ready.py": "18259376c0404f98bc0cb91afeb31e33789f581562e14939989816c2fa8754d9",
    "scripts/check_demo_tp_trigger.py": "a0bdd07027d5e3ca9b54a1468841fe2d40cf072c38c3c91c6c58292c2e67d96b",
    "scripts/demo_key_session.py": "85c698bc8bbad98bad4051c346a5e4e0a3a3bd22f8bbb90a362f835cadc633dd",
    "scripts/review_tp_minute_budget.py": "e8462dddf04d6d9107d01d7fbe8373df9a3a1e0cf6361d5f057021b2f67e9870",
    "services/execution/demo.py": "deb90dd8ddd9df627054a11c76f8dd8b09e364f2aaaca8f82b7fd0c237c444ab",
    "services/execution/demo_api_restart_check.py": "b96ca788094995dafb7a69d9bbbd7f6a6dd16687d30bc9bd4271164c307ab239",
    "services/execution/demo_empty_db_restart.py": "6adcc8284b43050bc4a115f2796c001d424ba7e0261654c099075fb3d950e39f",
    "services/execution/demo_empty_worker_recovery.py": "8e3cac8da89fb80ef9f3654ae8591ffea7be1db1c7dc60070da72b72851b2070",
    "services/execution/demo_event_clock.py": "924f3b88c9092355bb4b4ada3114944848208ab5b44ab73ee5b45d5fc1a29bfb",
    "services/execution/demo_event_inbox.py": "6d9be8c64e144592a5c2343c1db6e4dfc1dee65aa65eeebc08bd52b5aa633281",
    "services/execution/demo_flat_transport.py": "93f878c9869ad397a52546f0aa976bbe8071366eff36c7d0be6b1329df757401",
    "services/execution/demo_models.py": "175def10d7378661e82616b02ec87d6dbee4ffdeb4eb94437ce682206168dfb0",
    "services/execution/demo_preview.py": "58ba6376f61f8467d2b3c9e75923444105f5fe610f9bde4455a94b5fb5d6f267",
    "services/execution/demo_private_stability.py": "d3aee78f2da73c15c3eff5f89403fae666de5109ad3c2402585f2150df1a5123",
    "services/execution/demo_protected_worker_check.py": "70d9f0a6ec74223e8a7a82ec55c976288c10fb13c01c9b478f49f1a0fd408709",
    "services/execution/demo_recovery.py": "ca663f098d2f7c462d1f225f4ade91d743169175f125c574794dc58175c8c76e",
    "services/execution/demo_recovery_budget.py": "2c7aa260fd708948b0ae7854257de03bc8b59c86da85180ea5230b07e8df772b",
    "services/execution/demo_stream.py": "770b0783287dcd607c35dad96b8882acd2bcb6a205928046b9831f9920a6a29f",
    "services/execution/demo_tp_admission_candidate.py": "d8ab1b2ac699b38aaf75977ea97d49fa44ba60a52f518f997fd15f8a2489760a",
    "services/execution/demo_tp_budget_integration.py": "ab8b13e714c3298cb659b34a6ead0948dd6bf6c943548f0a3657cbc031786399",
    "services/execution/demo_tp_fenced_handoff_candidate.py": "9fcdf0c42b60dd7bae9f9fe2257a3d9e659581c3b711191b15c531b05084ee6c",
    "services/execution/demo_tp_first_confirmation_candidate.py": "0b31f29c94fb3f2e56c68c855737eddc312f80871f500867530d078e1190248f",
    "services/execution/demo_tp_staged_confirmation_candidate.py": "76544f006bdbcc17f54d19dae031e6eaebde831dca0537a15f73069f3d43335b",
    "services/execution/demo_tp_trigger_check.py": "f2a0cbac4fc33142fa63befb5aa38e1eb7a6ac2bb10018c0ea04290f7e310a6a",
    "services/execution/demo_tp_uncertain_cancel_candidate.py": "f188463cb0e2d17a160ed4f02fc9aac5b42d30938a44f2d03e8fa468bad48736",
    "services/execution/demo_validation.py": "725b36a93212733bafed401d1ba059d176d0f43b7a8154288d30df03e3b6b3e4",
    "services/execution/models.py": "5d362e33788a304e6f2f63f22868454b3804b103277a6fe4a1297c7e50e10a5d",
    "services/execution/paper.py": "1a847e50fc78d48846fadc9953fad07aed5bbdf83f1e30986d39fef0e0d9fc13",
    "services/execution/readiness_closed_history.py": "0c9cb90bc74402fdaed025f715a98a2e47169eee4d1f9990c75e8f5671e358da",
    "services/risk/paper.py": "7afe00460abae04dda795ab0dba8b8ad84877fa25686342c1fbce3f12ac80832",
    "uv.lock": "49bbe38dc3f559466c4666febe686926db6503413a45bd766088ed2454e01097",
}


def source_cohort_valid(pins):
    try:
        return len(pins) == 55 and all(
            Path(n).is_file() and hashlib.sha256(Path(n).read_bytes()).hexdigest() == h
            for n, h in pins.items()
        )
    except (OSError, ValueError, TypeError):
        return False


if __name__ == "__main__":
    if not source_cohort_valid(INSTALLED_PINS):
        print(
            json.dumps(
                dict(
                    manual_close_day_preview="BLOCKED_KEEP_PAUSED_NO_RETRY",
                    stop_reason="INSTALLED_SOURCE_HASH_MISMATCH",
                    database_changed_by_preview=False,
                    order_writes=0,
                    orders_enabled=False,
                    live_trading_enabled=False,
                )
            )
        )
        raise SystemExit(2)


import argparse
import os
import re
import time
from datetime import UTC, datetime, timedelta
from datetime import time as daytime
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid5
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
from services.execution.demo_models import DemoLedger
from services.execution.paper import account_risk
from services.risk.paper import AccountValue, period_keys

ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"

INCIDENT = "041d1f35-870e-46cb-aad2-ed6059bc1095"

RECEIPT = str(uuid5(UUID(INCIDENT), "signed-operator-manual-close-v1"))

NEW_PINS = INSTALLED_PINS


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
        if self.limit is None and path not in {
            "/fapi/v1/time",
            "/fapi/v1/exchangeInfo",
        }:
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
            (r for r in reversed(self.records[:-1]) if r["used_weight_1m"] is not None),
            None,
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
    require(source_cohort_valid(NEW_PINS), "INSTALLED_SOURCE_HASH_MISMATCH")


READ_PATHS = frozenset(
    {
        "/fapi/v1/time",
        "/fapi/v1/exchangeInfo",
        "/fapi/v1/accountConfig",
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
    "554838531": (
        CLOSE,
        "SELL",
        "75685.20",
        "-0.02793000",
        "0.02119185",
        1793947973987,
    ),
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
        result.tzinfo is not None and result.utcoffset() == timedelta(0),
        "ADOPTION_TIME_INVALID",
    )
    return result.astimezone(UTC)


PRIOR_DAY_KEY = str(uuid5(UUID(INCIDENT), "operator-attested-external-close-day-2026-11-07-v1"))
EXPECTED = {
    "old_day_kst": "2026-11-07",
    "proposed_day_kst": "2026-11-09",
    "old_week_kst": "2026-11-02",
    "proposed_week_kst": "2026-11-09",
    "old_daily_base_usdt": "4998.74183608",
    "proposed_daily_base_usdt": "4998.74183608",
    "old_weekly_base_usdt": "4998.99761493",
    "proposed_weekly_base_usdt": "4998.74183608",
    "high_water_usdt_preserved": "5000.00000000",
    "day_boundary_utc": "2026-11-08T15:00:00+00:00",
    "week_boundary_utc": "2026-11-08T15:00:00+00:00",
    "adoption_snapshot_anchor_utc": "2026-11-06T07:25:25.109073+00:00",
    "prior_attested_day_operation_id": PRIOR_DAY_KEY,
    "operator_attested_day_kst": "2026-11-09",
}
APPLY_KEY = str(uuid5(UUID(INCIDENT), "operator-attested-external-close-periods-2026-11-09-v1"))
OPERATION = "ADOPT_EXTERNAL_CLOSE_ATTESTED_PERIOD_BASELINES_V1"
SOURCE = "OPERATOR_ATTESTED_FLAT_PERIODS_WITH_SIGNED_USDT_EVIDENCE"


def preserved(ledger):
    data = ledger.model_dump(mode="json")
    for key in (
        "daily_key",
        "daily_base",
        "weekly_key",
        "weekly_base",
        "stream_ready",
        "reconciled",
    ):
        data.pop(key)
    for key in ("operations", "attested_period_baselines"):
        data[key].pop(APPLY_KEY, None)
    return digest(data)


def recorded(ledger):
    raw = ledger.operations.get(APPLY_KEY)
    receipt = ledger.attested_period_baselines.get(APPLY_KEY)
    if raw is None and receipt is None:
        require(
            not any(
                APPLY_KEY in x
                for x in (
                    ledger.recovery_receipts,
                    ledger.cancelled_exposure_corrections,
                    ledger.attested_day_baselines,
                    ledger.exit_anchored_day_baselines,
                )
            ),
            "PERIOD_OPERATION_CONFLICT",
        )
        return False
    require(raw is not None and receipt is not None, "PERIOD_OPERATION_CONFLICT")
    try:
        record = json.loads(raw)
    except (ValueError, TypeError):
        raise Blocked("PERIOD_OPERATION_CONFLICT") from None
    require(
        isinstance(record, dict)
        and record.get("operation") == OPERATION
        and record.get("reviewed_values") == EXPECTED
        and record.get("evidence_source") == "OPERATOR_ATTESTED_SIGNED_EXTERNAL_CLOSE_FLAT_ANCHOR"
        and type(record.get("signed_history_cutoff_ms")) is int
        and datetime.fromtimestamp(record["signed_history_cutoff_ms"] / 1000, UTC)
        == receipt.signed_evidence_until_utc
        and record.get("preserved_ledger_sha256") == preserved(ledger)
        and record.get("typed_receipt_sha256") == digest(receipt.model_dump(mode="json"))
        and record.get("exact_day_midnight_valuation_verified") is False
        and record.get("exact_week_midnight_valuation_verified") is False
        and record.get("account_wide_exchange_export_verified") is False,
        "PERIOD_OPERATION_CONFLICT",
    )
    require(
        receipt.review_token == digest(EXPECTED)
        and receipt.source == SOURCE
        and receipt.old_daily_key == EXPECTED["old_day_kst"]
        and receipt.old_weekly_key == EXPECTED["old_week_kst"]
        and receipt.old_daily_base == Decimal(EXPECTED["old_daily_base_usdt"])
        and receipt.old_weekly_base == Decimal(EXPECTED["old_weekly_base_usdt"])
        and receipt.day_boundary_utc == datetime.fromisoformat(EXPECTED["day_boundary_utc"])
        and receipt.week_boundary_utc == datetime.fromisoformat(EXPECTED["week_boundary_utc"])
        and receipt.anchor_utc
        == ledger.value.as_of
        == datetime.fromisoformat(EXPECTED["adoption_snapshot_anchor_utc"])
        and ledger.daily_key == EXPECTED["proposed_day_kst"]
        and ledger.weekly_key == EXPECTED["proposed_week_kst"]
        and ledger.daily_base
        == ledger.weekly_base
        == receipt.baseline_usdt
        == Decimal(EXPECTED["proposed_daily_base_usdt"])
        and ledger.high_water == Decimal(EXPECTED["high_water_usdt_preserved"])
        and ledger.paused
        and ledger.risk_state.value == "NORMAL"
        and not ledger.reconciled
        and not ledger.stream_ready,
        "PERIOD_OPERATION_CONFLICT",
    )
    require(
        all(
            re.fullmatch("[0-9a-f]{64}", x)
            for x in (receipt.signed_evidence_hash, receipt.exchange_fingerprint)
        )
        and receipt.anchor_utc
        < receipt.week_boundary_utc
        <= receipt.day_boundary_utc
        <= receipt.signed_evidence_until_utc
        <= receipt.attested_until_utc,
        "PERIOD_OPERATION_CONFLICT",
    )
    return True


CURRENT_ATTESTED_DAY = "2026-11-11"


def context(store, ledger, client):
    require(
        not client.settings.demo_trading_enabled
        and client.settings.policy == ledger.policy
        and client.settings.margin_mode == ledger.margin_mode == "ISOLATED"
        and client.settings.symbols == ledger.symbols == ("BTCUSDT",),
        "READ_ONLY_CONTEXT_REQUIRED",
    )
    require(recorded(ledger), "PRIOR_PERIOD_RECEIPT_REQUIRED")
    receipt = json.loads(ledger.operations.get(RECEIPT, "{}"))
    require(isinstance(receipt, dict), "MANUAL_RECEIPT_INVALID")
    anchor = ledger.value.as_of
    adopted = utc(receipt.get("acknowledged_at_utc"))
    close = datetime.fromtimestamp(CLOSE_MS / 1000, UTC)
    now = store.now()
    day, week = period_keys(now, ledger.policy.week_start_kst)
    require(day == CURRENT_ATTESTED_DAY, "CURRENT_DAY_ATTESTATION_REQUIRED")
    boundary = datetime.combine(
        datetime.fromisoformat(day).date(), daytime(), ZoneInfo("Asia/Seoul")
    ).astimezone(UTC)
    week_boundary = datetime.combine(
        datetime.fromisoformat(week).date(), daytime(), ZoneInfo("Asia/Seoul")
    ).astimezone(UTC)
    prior = ledger.attested_period_baselines[APPLY_KEY]
    require(
        close < anchor <= adopted < week_boundary < boundary <= now
        and 0 <= (adopted - anchor).total_seconds() <= ledger.policy.max_snapshot_age_seconds
        and 0 < (now - close).total_seconds() <= 7 * 86400
        and prior.attested_until_utc <= now
        and prior.signed_evidence_until_utc <= now
        and ledger.daily_key == "2026-11-09" < day
        and ledger.weekly_key == week == "2026-11-09"
        and prior.day_boundary_utc < boundary,
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
    return receipt, anchor, day, week, boundary, week_boundary


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
                self.anchor_ms is not None and self.cutoff_ms is not None,
                "FIXED_WINDOW_REQUIRED",
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
    mode = client.request("GET", "/fapi/v1/accountConfig")
    require(
        isinstance(mode, dict)
        and mode.get("dualSidePosition") is False
        and mode.get("multiAssetsMargin") is False,
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
    require(
        isinstance(incomes, list) and len(incomes) < ROW_LIMIT,
        "POST_ANCHOR_INCOME_INCOMPLETE",
    )
    require(not incomes, "POST_ANCHOR_INCOME_PRESENT")


def preview(store, client, monitor, *, attest=False, attested_day=None):
    require(attest is True, "OPERATOR_ATTESTATION_REQUIRED")
    before = local(store)
    ledger = before[-1]
    receipt, anchor, day, week, boundary, week_boundary = context(store, ledger, client)
    require(attested_day == day, "CURRENT_DAY_ATTESTATION_REQUIRED")
    try:
        client.sync_clock()
        limit = published_limit(client.public("/fapi/v1/exchangeInfo"))
        monitor.enable(limit)
        client.anchor_ms = int(anchor.timestamp() * 1000)
        first = read_state(client, ledger)
        signed_close(client, ledger, receipt)
        client.cutoff_ms = client.clock_ms() + client.offset
        require(
            0 < client.cutoff_ms - TRADE_START <= 604800000,
            "SIGNED_HISTORY_WINDOW_UNSUPPORTED",
        )
        history(client)
        client.sync_clock()
        client.cutoff_ms = client.clock_ms() + client.offset
        history(client)
        final = read_state(client, ledger)
        require(first == final, "CURRENT_STATE_CHANGED")
        require(
            period_keys(store.now(), ledger.policy.week_start_kst) == (day, week)
            and period_keys(
                datetime.fromtimestamp(client.cutoff_ms / 1000, UTC),
                ledger.policy.week_start_kst,
            )
            == (day, week),
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
            and probe.daily_key == day
            and probe.weekly_key == week
            and probe.daily_base == wallet
            and probe.weekly_key == ledger.weekly_key
            and probe.weekly_base == ledger.weekly_base
            and probe.high_water == ledger.high_water,
            "PROPOSED_RISK_GATES_FAILED",
        )
        require(local(store) == before, "LOCAL_STATE_CHANGED")
        return {
            "manual_close_day_preview": "OPERATOR_ATTESTED_DAY_PREVIEW_NO_DATABASE_CHANGE",
            "source": "OPERATOR_ATTESTED_SIGNED_EXTERNAL_CLOSE_FLAT_ANCHOR",
            "old_day_kst": ledger.daily_key,
            "proposed_day_kst": day,
            "old_week_kst": ledger.weekly_key,
            "proposed_week_kst": week,
            "old_daily_base_usdt": str(ledger.daily_base),
            "proposed_daily_base_usdt": str(wallet),
            "old_weekly_base_usdt": str(ledger.weekly_base),
            "proposed_weekly_base_usdt": str(ledger.weekly_base),
            "high_water_usdt_preserved": str(ledger.high_water),
            "day_boundary_utc": boundary.isoformat(),
            "week_boundary_utc": week_boundary.isoformat(),
            "prior_attested_period_operation_id": APPLY_KEY,
            "prior_attested_period_receipt_verified": True,
            "operator_attested_day_kst": attested_day,
            "adoption_snapshot_anchor_utc": anchor.isoformat(),
            "attested_since_manual_close_utc": datetime.fromtimestamp(
                CLOSE_MS / 1000, UTC
            ).isoformat(),
            "signed_history_cutoff_ms": client.cutoff_ms,
            "operator_attestation_used": True,
            "exact_day_midnight_valuation_verified": False,
            "exact_week_midnight_valuation_verified": False,
            "weekly_baseline_adoption_requested": False,
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
        require(local(store) == before, "LOCAL_STATE_CHANGED")


def blocked(reason):
    return {
        "manual_close_day_preview": "BLOCKED_KEEP_PAUSED_NO_RETRY",
        "stop_reason": reason,
        "database_changed_by_preview": False,
        "order_writes": 0,
        "orders_enabled": False,
        "live_trading_enabled": False,
        "baseline_adoption_approved": False,
        "next_action": "SHARE_CODE_NO_BASELINE_CHANGE",
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
    parser.add_argument("--attested-day-kst", required=True)
    args = parser.parse_args()
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
                Path("configs/demo-isolated.example.yaml").read_text(),
                Loader=UniqueKeyLoader,
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
        client = DayPreviewClient(cfg, key, send=monitor)
        monitor.server_ms = lambda: client.clock_ms() + client.offset
        report = preview(
            store,
            client,
            monitor,
            attest=args.attest_no_activity_since_manual_close,
            attested_day=args.attested_day_kst,
        )
        code = 0
    except Exception as error:
        report = blocked(safe_reason(error))
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
            report = blocked("RESOURCE_CLEANUP_UNVERIFIED")
            code = 2
    print(json.dumps(report), flush=True)
    raise SystemExit(code)


STOP_CODES = frozenset(
    [
        "PRIOR_PERIOD_RECEIPT_REQUIRED",
        "PERIOD_OPERATION_CONFLICT",
        "ACCOUNT_MODE_UNSUPPORTED",
        "ACCOUNT_RESPONSE_INVALID",
        "ACTIVE_OWNER_BLOCKED",
        "ADOPTION_TIME_INVALID",
        "ADOPTION_TIME_MISSING",
        "CHECK_UNAVAILABLE",
        "CLOCK_BUDGET_REACHED",
        "CLOSE_IDENTITY_OR_TIME_MISMATCH",
        "CURRENT_DAY_ATTESTATION_REQUIRED",
        "CURRENT_EXPOSURE_OR_OPEN_ORDER",
        "CURRENT_STATE_CHANGED",
        "DAY_OR_WEEK_CONTEXT_UNSUPPORTED",
        "DEMO_CREDENTIAL_SESSION_UNAVAILABLE",
        "ENTRY_IDENTITY_MISMATCH",
        "EXISTING_LOSS_LIMIT_REACHED",
        "EXTRA_OR_CHANGED_BTC_TRADES",
        "FAILED_TRIAL_MARKER_CHANGED",
        "FIXED_WINDOW_REQUIRED",
        "HIGH_WATER_REQUIRES_SEPARATE_REVIEW",
        "HISTORY_QUERY_SCOPE_FORBIDDEN",
        "HTTP_BUDGET_REACHED",
        "INITIALIZED_LEDGER_REQUIRED",
        "INSTALLED_SOURCE_HASH_MISMATCH",
        "ISOLATED_SETTINGS_CHANGED",
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
        "POST_ANCHOR_INCOME_INCOMPLETE",
        "POST_ANCHOR_INCOME_PRESENT",
        "PRIOR_DAY_PROVENANCE_CHANGED",
        "PRIOR_DAY_RECEIPT_INVALID",
        "PRIOR_DAY_RECEIPT_REQUIRED",
        "PRIOR_DAY_VALUES_CHANGED",
        "PROPOSED_RISK_GATES_FAILED",
        "PUBLIC_SCOPE_FORBIDDEN",
        "PUBLISHED_LIMIT_AMBIGUOUS_OR_INVALID",
        "PUBLISHED_LIMIT_INVALID",
        "PUBLISHED_LIMIT_UNAVAILABLE",
        "RATE_LIMIT_ALREADY_OBSERVED",
        "READ_ONLY_CONTEXT_REQUIRED",
        "RESOURCE_CLEANUP_UNVERIFIED",
        "SIGNED_HISTORY_WINDOW_UNSUPPORTED",
        "SIGNED_ORDER_PROOF_MISMATCH",
        "SIGNED_TRADE_PROOF_MISMATCH",
        "TIME_BUDGET_REACHED",
        "TRADE_HISTORY_INCOMPLETE",
        "UNEXPECTED_QUERY_PARAMETERS",
        "USDT_ASSET_SCOPE_INVALID",
        "USDT_BALANCE_OR_MARGIN_MISMATCH",
        "WALLET_CHANGED_FROM_ADOPTION",
        "WEIGHT_HEADER_UNAVAILABLE",
        "WEIGHT_HEADROOM_THRESHOLD",
    ]
)
if __name__ == "__main__":
    main()
