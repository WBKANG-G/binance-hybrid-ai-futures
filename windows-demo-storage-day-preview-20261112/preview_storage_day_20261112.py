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


CURRENT_ATTESTED_DAY = "2026-11-12"


def context(store, ledger, client):
    require(
        not client.settings.demo_trading_enabled
        and client.settings.policy == ledger.policy
        and client.settings.margin_mode == ledger.margin_mode == "ISOLATED"
        and client.settings.symbols == ledger.symbols == ("BTCUSDT",),
        "READ_ONLY_CONTEXT_REQUIRED",
    )
    require(verify_previous_day_and_setup(ledger), "PRIOR_DAY_RECEIPT_INVALID")
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
        and ledger.daily_key == "2026-11-11" < day
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


REVIEW_SHA256 = "22e2a79ef1d4509951e421d5993519b1092d976ea2941b310d1b20c43185ca73"
REVIEW_SOURCE = '"""Read-only post-install controls and optional bounded DEMO evidence; no activation."""\n\n# ruff: noqa: E402\nimport hashlib\nimport json\nimport os\nimport re\nfrom datetime import UTC, datetime\nfrom decimal import Decimal\nfrom pathlib import Path\nfrom uuid import UUID, uuid5\n\n\nclass Blocked(ValueError):\n    pass\n\n\nINSTALLED_PINS = {\n    "alembic.ini": "e7955a0ffb9c32a9c5067d7885ee9197328f17a4a12fb4a5bb556c2b85763aa2",\n    "configs/demo-isolated.example.yaml": "d93e85c2e056ffbbc3dac5fab9f0e5bf28032fd16f41bbff1f3ae81fb00882ca",\n    "integrations/binance_usdm/demo.py": "767d3a29b59882035bdfbf61d3271b39bfece0bff4b359e636db62ad9a5e84eb",\n    "integrations/binance_usdm/demo_rate_limits.py": "d72af60f10a74baea689e11ef8f11b5003f2ab9f89e539290c45b93f2f3d4a4e",\n    "integrations/binance_usdm/demo_read_routing.py": "a6e96b21135882a272cb5c97f86ddc14c3a5f4fa79dd115e40397252c679a66d",\n    "integrations/binance_usdm/demo_snapshot.py": "f80f6e51505ab1095c25b6ee76af01c258cff911aabd02c201bfcddc636f2258",\n    "integrations/binance_usdm/demo_tp_admission.py": "c6a66aff461f2aab59d2f2ae1f42a44c6388268d93893ecf4854c3af111ee0d8",\n    "integrations/binance_usdm/demo_tp_weight_model.py": "611948a52ffad41ecc448549f530558cd4df2585e23273d23422c8a995c7cbc3",\n    "integrations/binance_usdm/demo_tp_window_budget.py": "9433a307767a2e60a4cdbc31a85a8cb386066ff9799249239633ead1cf76120b",\n    "integrations/binance_usdm/filters.py": "0279613f1a17a2bc7ffd50e30be2b6fd1aed8ba240219e8e893b754a067d95ec",\n    "integrations/binance_usdm/orders.py": "2ab366265f7d35356ee9636c6aad4b594b518beeb636407515a57386b3e8441f",\n    "integrations/binance_usdm/recovery_transport.py": "7495c0436437070644627d5ee175d7a63332dc5dc2134574285a7870e8bbba77",\n    "integrations/binance_usdm/rest.py": "9a14edc40fa6365a50fe57c9ddeff4e866044e00287e7c5b680d8bb3dbd0ad52",\n    "migrations/env.py": "4256345fc24dd52ba706aa9b712268b9df436d52bafa7238234272d754734086",\n    "migrations/script.py.mako": "67042994cfc495007c508ccae15e44d21057d90222461630f9bc04d3657f5f6d",\n    "migrations/versions/0001_public_market_data.py": "b62afcb18d6d6c42d710ca0cb0eb42001e174934f11678443dc5ac24d86932f7",\n    "migrations/versions/0002_paper.py": "91ffad2a439b9f47e2636939a5aba091490942db7a87bb0d87124a531870c77d",\n    "migrations/versions/0003_demo.py": "6b2a26ba34855a3dee6d3b2c67eb9b3ecac644b4e2e31ad6353e907a3c233762",\n    "migrations/versions/0004_demo_urgent_alerts.py": "da9e4de16f1f9d4c15d70293d3b252084648fbbcb060f8ec7740b9a85f83e8ad",\n    "packages/domain/config.py": "c2bd7a039e3ea007a1e34a6dbdd058cc626e2690cc8d55b0761cfd2085bf311d",\n    "packages/domain/contracts.py": "71c0a95faad586d6e3f342d83b6b95a4772cb6e2c1808b44fc51fdf9f1332947",\n    "packages/persistence/demo.py": "40f9a33b8eeda28c96f36de35656e4667a2d2461ce9de79dc4b19a687cd5cd5a",\n    "pyproject.toml": "54760a9fa52fc4cc877d2302c7952688ddd13e12f1efc8e44a0974aae8914019",\n    "scripts/check_demo_disconnect.py": "c85c013412053237eda7ed82a8948aa4b50f9da1951d173360a1ecfb46228a1e",\n    "scripts/check_demo_session_ready.py": "18259376c0404f98bc0cb91afeb31e33789f581562e14939989816c2fa8754d9",\n    "scripts/check_demo_tp_trigger.py": "a0bdd07027d5e3ca9b54a1468841fe2d40cf072c38c3c91c6c58292c2e67d96b",\n    "scripts/demo_key_session.py": "85c698bc8bbad98bad4051c346a5e4e0a3a3bd22f8bbb90a362f835cadc633dd",\n    "scripts/review_tp_minute_budget.py": "e8462dddf04d6d9107d01d7fbe8373df9a3a1e0cf6361d5f057021b2f67e9870",\n    "services/execution/demo.py": "deb90dd8ddd9df627054a11c76f8dd8b09e364f2aaaca8f82b7fd0c237c444ab",\n    "services/execution/demo_api_restart_check.py": "b96ca788094995dafb7a69d9bbbd7f6a6dd16687d30bc9bd4271164c307ab239",\n    "services/execution/demo_empty_db_restart.py": "6adcc8284b43050bc4a115f2796c001d424ba7e0261654c099075fb3d950e39f",\n    "services/execution/demo_empty_worker_recovery.py": "8e3cac8da89fb80ef9f3654ae8591ffea7be1db1c7dc60070da72b72851b2070",\n    "services/execution/demo_event_clock.py": "924f3b88c9092355bb4b4ada3114944848208ab5b44ab73ee5b45d5fc1a29bfb",\n    "services/execution/demo_event_inbox.py": "6d9be8c64e144592a5c2343c1db6e4dfc1dee65aa65eeebc08bd52b5aa633281",\n    "services/execution/demo_flat_transport.py": "93f878c9869ad397a52546f0aa976bbe8071366eff36c7d0be6b1329df757401",\n    "services/execution/demo_models.py": "175def10d7378661e82616b02ec87d6dbee4ffdeb4eb94437ce682206168dfb0",\n    "services/execution/demo_preview.py": "58ba6376f61f8467d2b3c9e75923444105f5fe610f9bde4455a94b5fb5d6f267",\n    "services/execution/demo_private_stability.py": "d3aee78f2da73c15c3eff5f89403fae666de5109ad3c2402585f2150df1a5123",\n    "services/execution/demo_protected_worker_check.py": "70d9f0a6ec74223e8a7a82ec55c976288c10fb13c01c9b478f49f1a0fd408709",\n    "services/execution/demo_recovery.py": "ca663f098d2f7c462d1f225f4ade91d743169175f125c574794dc58175c8c76e",\n    "services/execution/demo_recovery_budget.py": "2c7aa260fd708948b0ae7854257de03bc8b59c86da85180ea5230b07e8df772b",\n    "services/execution/demo_stream.py": "770b0783287dcd607c35dad96b8882acd2bcb6a205928046b9831f9920a6a29f",\n    "services/execution/demo_tp_admission_candidate.py": "d8ab1b2ac699b38aaf75977ea97d49fa44ba60a52f518f997fd15f8a2489760a",\n    "services/execution/demo_tp_budget_integration.py": "ab8b13e714c3298cb659b34a6ead0948dd6bf6c943548f0a3657cbc031786399",\n    "services/execution/demo_tp_fenced_handoff_candidate.py": "9fcdf0c42b60dd7bae9f9fe2257a3d9e659581c3b711191b15c531b05084ee6c",\n    "services/execution/demo_tp_first_confirmation_candidate.py": "0b31f29c94fb3f2e56c68c855737eddc312f80871f500867530d078e1190248f",\n    "services/execution/demo_tp_staged_confirmation_candidate.py": "76544f006bdbcc17f54d19dae031e6eaebde831dca0537a15f73069f3d43335b",\n    "services/execution/demo_tp_trigger_check.py": "f2a0cbac4fc33142fa63befb5aa38e1eb7a6ac2bb10018c0ea04290f7e310a6a",\n    "services/execution/demo_tp_uncertain_cancel_candidate.py": "f188463cb0e2d17a160ed4f02fc9aac5b42d30938a44f2d03e8fa468bad48736",\n    "services/execution/demo_validation.py": "725b36a93212733bafed401d1ba059d176d0f43b7a8154288d30df03e3b6b3e4",\n    "services/execution/models.py": "5d362e33788a304e6f2f63f22868454b3804b103277a6fe4a1297c7e50e10a5d",\n    "services/execution/paper.py": "1a847e50fc78d48846fadc9953fad07aed5bbdf83f1e30986d39fef0e0d9fc13",\n    "services/execution/readiness_closed_history.py": "0c9cb90bc74402fdaed025f715a98a2e47169eee4d1f9990c75e8f5671e358da",\n    "services/risk/paper.py": "7afe00460abae04dda795ab0dba8b8ad84877fa25686342c1fbce3f12ac80832",\n    "uv.lock": "49bbe38dc3f559466c4666febe686926db6503413a45bd766088ed2454e01097",\n}\n\n\ndef source_cohort_valid(pins):\n    try:\n        return len(pins) == 55 and all(\n            (\n                Path(n).is_file() and hashlib.sha256(Path(n).read_bytes()).hexdigest() == h\n                for n, h in pins.items()\n            )\n        )\n    except (OSError, ValueError, TypeError):\n        return False\n\n\nif __name__ == "__main__":\n    if not source_cohort_valid(INSTALLED_PINS):\n        print(\n            json.dumps(\n                {\n                    "runtime_readiness_review": "BLOCKED_KEEP_PAUSED",\n                    "stop_reason": "INSTALLED_SOURCE_HASH_MISMATCH",\n                    "order_writes": 0,\n                    "execution_approved": False,\n                    "orders_enabled": False,\n                    "live_trading_enabled": False,\n                }\n            )\n        )\n        raise SystemExit(2)\n\nfrom sqlalchemy import create_engine, select\n\nfrom packages.domain.config import database_url\nfrom packages.persistence.demo import DemoStore, accounts, events, outbox\nfrom services.execution.demo_empty_db_restart import empty\nfrom services.execution.demo_models import DemoLedger\nfrom services.risk.paper import period_keys\n\nACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"\n\nINCIDENT = "041d1f35-870e-46cb-aad2-ed6059bc1095"\n\nRECEIPT = str(uuid5(UUID(INCIDENT), "signed-operator-manual-close-v1"))\n\n\ndef require(ok, code):\n    if not ok:\n        raise Blocked(code)\n\n\nTRADE_START = 1793947690122\n\nCLOSE_MS = 1793947973987\n\n\ndef digest(value):\n    return hashlib.sha256(\n        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()\n    ).hexdigest()\n\n\nPRIOR_DAY_KEY = str(uuid5(UUID(INCIDENT), "operator-attested-external-close-day-2026-11-07-v1"))\n\nEXPECTED = {\n    "old_day_kst": "2026-11-07",\n    "proposed_day_kst": "2026-11-09",\n    "old_week_kst": "2026-11-02",\n    "proposed_week_kst": "2026-11-09",\n    "old_daily_base_usdt": "4998.74183608",\n    "proposed_daily_base_usdt": "4998.74183608",\n    "old_weekly_base_usdt": "4998.99761493",\n    "proposed_weekly_base_usdt": "4998.74183608",\n    "high_water_usdt_preserved": "5000.00000000",\n    "day_boundary_utc": "2026-11-08T15:00:00+00:00",\n    "week_boundary_utc": "2026-11-08T15:00:00+00:00",\n    "adoption_snapshot_anchor_utc": "2026-11-06T07:25:25.109073+00:00",\n    "prior_attested_day_operation_id": PRIOR_DAY_KEY,\n    "operator_attested_day_kst": "2026-11-09",\n}\n\nAPPLY_KEY = str(uuid5(UUID(INCIDENT), "operator-attested-external-close-periods-2026-11-09-v1"))\n\nOPERATION = "ADOPT_EXTERNAL_CLOSE_ATTESTED_PERIOD_BASELINES_V1"\n\nSOURCE = "OPERATOR_ATTESTED_FLAT_PERIODS_WITH_SIGNED_USDT_EVIDENCE"\n\n\ndef preserved(ledger):\n    data = ledger.model_dump(mode="json")\n    for key in (\n        "daily_key",\n        "daily_base",\n        "weekly_key",\n        "weekly_base",\n        "stream_ready",\n        "reconciled",\n    ):\n        data.pop(key)\n    for key in ("operations", "attested_period_baselines"):\n        data[key].pop(APPLY_KEY, None)\n    return digest(data)\n\n\ndef recorded(ledger):\n    raw = ledger.operations.get(APPLY_KEY)\n    receipt = ledger.attested_period_baselines.get(APPLY_KEY)\n    if raw is None and receipt is None:\n        require(\n            not any(\n                APPLY_KEY in x\n                for x in (\n                    ledger.recovery_receipts,\n                    ledger.cancelled_exposure_corrections,\n                    ledger.attested_day_baselines,\n                    ledger.exit_anchored_day_baselines,\n                )\n            ),\n            "PERIOD_OPERATION_CONFLICT",\n        )\n        return False\n    require(raw is not None and receipt is not None, "PERIOD_OPERATION_CONFLICT")\n    try:\n        record = json.loads(raw)\n    except (ValueError, TypeError):\n        raise Blocked("PERIOD_OPERATION_CONFLICT") from None\n    require(\n        isinstance(record, dict)\n        and record.get("operation") == OPERATION\n        and (record.get("reviewed_values") == EXPECTED)\n        and (record.get("evidence_source") == "OPERATOR_ATTESTED_SIGNED_EXTERNAL_CLOSE_FLAT_ANCHOR")\n        and (type(record.get("signed_history_cutoff_ms")) is int)\n        and (\n            datetime.fromtimestamp(record["signed_history_cutoff_ms"] / 1000, UTC)\n            == receipt.signed_evidence_until_utc\n        )\n        and (record.get("preserved_ledger_sha256") == preserved(ledger))\n        and (record.get("typed_receipt_sha256") == digest(receipt.model_dump(mode="json")))\n        and (record.get("exact_day_midnight_valuation_verified") is False)\n        and (record.get("exact_week_midnight_valuation_verified") is False)\n        and (record.get("account_wide_exchange_export_verified") is False),\n        "PERIOD_OPERATION_CONFLICT",\n    )\n    require(\n        receipt.review_token == digest(EXPECTED)\n        and receipt.source == SOURCE\n        and (receipt.old_daily_key == EXPECTED["old_day_kst"])\n        and (receipt.old_weekly_key == EXPECTED["old_week_kst"])\n        and (receipt.old_daily_base == Decimal(EXPECTED["old_daily_base_usdt"]))\n        and (receipt.old_weekly_base == Decimal(EXPECTED["old_weekly_base_usdt"]))\n        and (receipt.day_boundary_utc == datetime.fromisoformat(EXPECTED["day_boundary_utc"]))\n        and (receipt.week_boundary_utc == datetime.fromisoformat(EXPECTED["week_boundary_utc"]))\n        and (\n            receipt.anchor_utc\n            == ledger.value.as_of\n            == datetime.fromisoformat(EXPECTED["adoption_snapshot_anchor_utc"])\n        )\n        and (ledger.daily_key == EXPECTED["proposed_day_kst"])\n        and (ledger.weekly_key == EXPECTED["proposed_week_kst"])\n        and (\n            ledger.daily_base\n            == ledger.weekly_base\n            == receipt.baseline_usdt\n            == Decimal(EXPECTED["proposed_daily_base_usdt"])\n        )\n        and (ledger.high_water == Decimal(EXPECTED["high_water_usdt_preserved"]))\n        and ledger.paused\n        and (ledger.risk_state.value == "NORMAL")\n        and (not ledger.reconciled)\n        and (not ledger.stream_ready),\n        "PERIOD_OPERATION_CONFLICT",\n    )\n    require(\n        all(\n            re.fullmatch("[0-9a-f]{64}", x)\n            for x in (receipt.signed_evidence_hash, receipt.exchange_fingerprint)\n        )\n        and receipt.anchor_utc\n        < receipt.week_boundary_utc\n        <= receipt.day_boundary_utc\n        <= receipt.signed_evidence_until_utc\n        <= receipt.attested_until_utc,\n        "PERIOD_OPERATION_CONFLICT",\n    )\n    return True\n\n\nDAY_KEY = str(uuid5(UUID(INCIDENT), "operator-attested-external-close-day-2026-11-11-v1"))\n\nDAY_SOURCE = "OPERATOR_ATTESTED_FLAT_WITH_SIGNED_USDT_EVIDENCE"\n\nDAY_OPERATION = "ADOPT_EXTERNAL_CLOSE_ATTESTED_DAY_BASELINE_20261111_V1"\n\nREVIEWED = {\n    "old_day_kst": "2026-11-09",\n    "proposed_day_kst": "2026-11-11",\n    "old_week_kst": "2026-11-09",\n    "proposed_week_kst": "2026-11-09",\n    "old_daily_base_usdt": "4998.74183608",\n    "proposed_daily_base_usdt": "4998.74183608",\n    "old_weekly_base_usdt": "4998.74183608",\n    "proposed_weekly_base_usdt": "4998.74183608",\n    "high_water_usdt_preserved": "5000.00000000",\n    "day_boundary_utc": "2026-11-10T15:00:00+00:00",\n    "week_boundary_utc": "2026-11-08T15:00:00+00:00",\n    "prior_attested_period_operation_id": APPLY_KEY,\n    "adoption_snapshot_anchor_utc": "2026-11-06T07:25:25.109073+00:00",\n    "operator_attested_day_kst": "2026-11-11",\n}\n\n\ndef day_preserved(ledger):\n    data = ledger.model_dump(mode="json")\n    for key in ("daily_key", "daily_base", "reconciled", "stream_ready"):\n        data.pop(key)\n    data["operations"].pop(DAY_KEY, None)\n    data["attested_day_baselines"].pop(DAY_KEY, None)\n    return digest(data)\n\n\ndef day_recorded(ledger):\n    raw = ledger.operations.get(DAY_KEY)\n    receipt = ledger.attested_day_baselines.get(DAY_KEY)\n    if raw is None and receipt is None:\n        require(\n            not any(\n                DAY_KEY in x\n                for x in (\n                    ledger.attested_period_baselines,\n                    ledger.recovery_receipts,\n                    ledger.cancelled_exposure_corrections,\n                    ledger.exit_anchored_day_baselines,\n                )\n            ),\n            "DAY_OPERATION_CONFLICT",\n        )\n        return False\n    require(raw is not None and receipt is not None, "DAY_OPERATION_CONFLICT")\n    try:\n        record = json.loads(raw)\n    except (ValueError, TypeError):\n        raise Blocked("DAY_OPERATION_CONFLICT") from None\n    require(\n        isinstance(record, dict)\n        and record.get("operation") == DAY_OPERATION\n        and (record.get("reviewed_values") == REVIEWED)\n        and (record.get("evidence_source") == "OPERATOR_ATTESTED_SIGNED_EXTERNAL_CLOSE_FLAT_ANCHOR")\n        and (record.get("preserved_ledger_sha256") == day_preserved(ledger))\n        and (record.get("typed_receipt_sha256") == digest(receipt.model_dump(mode="json")))\n        and (record.get("exact_midnight_valuation_verified") is False)\n        and (record.get("account_wide_exchange_export_verified") is False),\n        "DAY_OPERATION_CONFLICT",\n    )\n    require(\n        receipt.review_token == digest(REVIEWED)\n        and receipt.source == DAY_SOURCE\n        and (receipt.old_daily_key == REVIEWED["old_day_kst"])\n        and (receipt.old_daily_base == Decimal(REVIEWED["old_daily_base_usdt"]))\n        and (ledger.daily_key == REVIEWED["proposed_day_kst"])\n        and (\n            ledger.daily_base\n            == receipt.baseline_usdt\n            == Decimal(REVIEWED["proposed_daily_base_usdt"])\n        )\n        and (receipt.boundary_utc == datetime.fromisoformat(REVIEWED["day_boundary_utc"]))\n        and (\n            receipt.anchor_utc\n            == ledger.value.as_of\n            == datetime.fromisoformat(REVIEWED["adoption_snapshot_anchor_utc"])\n        )\n        and (receipt.known_exit_utc == datetime.fromtimestamp(CLOSE_MS / 1000, UTC))\n        and ledger.paused\n        and (ledger.risk_state.value == "NORMAL")\n        and (not ledger.reconciled)\n        and (not ledger.stream_ready),\n        "DAY_OPERATION_CONFLICT",\n    )\n    cutoff = record.get("signed_history_cutoff_ms")\n    require(type(cutoff) is int and 0 < cutoff - TRADE_START <= 604800000, "DAY_OPERATION_CONFLICT")\n    signed_until = datetime.fromtimestamp(cutoff / 1000, UTC)\n    require(\n        receipt.anchor_utc < receipt.boundary_utc <= signed_until <= receipt.attested_until_utc\n        and period_keys(signed_until, ledger.policy.week_start_kst)\n        == (REVIEWED["proposed_day_kst"], REVIEWED["proposed_week_kst"])\n        and all(\n            re.fullmatch("[0-9a-f]{64}", h)\n            for h in (receipt.signed_evidence_hash, receipt.exchange_fingerprint)\n        ),\n        "DAY_OPERATION_CONFLICT",\n    )\n    prior = ledger.model_copy(deep=True)\n    prior.daily_key, prior.daily_base = (receipt.old_daily_key, receipt.old_daily_base)\n    prior.operations.pop(DAY_KEY)\n    prior.attested_day_baselines.pop(DAY_KEY)\n    require(recorded(prior), "PRIOR_PERIOD_RECEIPT_REQUIRED")\n    return True\n\n\nTARGET_PINS = {\n    "integrations/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",\n    "integrations/binance_usdm/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",\n    "integrations/binance_usdm/demo.py": "767d3a29b59882035bdfbf61d3271b39bfece0bff4b359e636db62ad9a5e84eb",\n    "integrations/binance_usdm/demo_rate_limits.py": "d72af60f10a74baea689e11ef8f11b5003f2ab9f89e539290c45b93f2f3d4a4e",\n    "integrations/binance_usdm/demo_snapshot.py": "f80f6e51505ab1095c25b6ee76af01c258cff911aabd02c201bfcddc636f2258",\n    "integrations/binance_usdm/demo_tp_admission.py": "c6a66aff461f2aab59d2f2ae1f42a44c6388268d93893ecf4854c3af111ee0d8",\n    "integrations/binance_usdm/demo_tp_weight_model.py": "611948a52ffad41ecc448549f530558cd4df2585e23273d23422c8a995c7cbc3",\n    "integrations/binance_usdm/demo_tp_window_budget.py": "9433a307767a2e60a4cdbc31a85a8cb386066ff9799249239633ead1cf76120b",\n    "integrations/binance_usdm/filters.py": "0279613f1a17a2bc7ffd50e30be2b6fd1aed8ba240219e8e893b754a067d95ec",\n    "integrations/binance_usdm/orders.py": "2ab366265f7d35356ee9636c6aad4b594b518beeb636407515a57386b3e8441f",\n    "integrations/binance_usdm/recovery_transport.py": "7495c0436437070644627d5ee175d7a63332dc5dc2134574285a7870e8bbba77",\n    "integrations/binance_usdm/rest.py": "9a14edc40fa6365a50fe57c9ddeff4e866044e00287e7c5b680d8bb3dbd0ad52",\n    "packages/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",\n    "packages/domain/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",\n    "packages/domain/config.py": "c2bd7a039e3ea007a1e34a6dbdd058cc626e2690cc8d55b0761cfd2085bf311d",\n    "packages/domain/contracts.py": "71c0a95faad586d6e3f342d83b6b95a4772cb6e2c1808b44fc51fdf9f1332947",\n    "packages/persistence/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",\n    "packages/persistence/demo.py": "40f9a33b8eeda28c96f36de35656e4667a2d2461ce9de79dc4b19a687cd5cd5a",\n    "packages/persistence/demo_alerts.py": "fb8986d57ef850c7b707d59730b002ead125429e1d23d310a89b3c7e69385330",\n    "packages/persistence/demo_order_quota.py": "c1ebdc1ccf9d469bd04950716ce84969fafbf18770a43d69aba6b99d117bc4b1",\n    "packages/persistence/demo_shared_checkpoint.py": "14142d1a0ed1201688a71155c3d1772440580e7cc9127eb318040fdc9fa9c537",\n    "packages/persistence/demo_shared_weight.py": "57696024477a81c35b69530facec75420c049b45272d00472e61ef04589fccac",\n    "packages/persistence/market.py": "89265a7bc7d181863983f4d24e6bdbe7499bb5b8154a6b22c6aea46c0d216050",\n    "packages/persistence/paper.py": "312f2ab4478e2cb7f980045c56ac1329d080d8e54d60f960a93ae0f2fe1d2bd7",\n    "services/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",\n    "services/execution/demo.py": "deb90dd8ddd9df627054a11c76f8dd8b09e364f2aaaca8f82b7fd0c237c444ab",\n    "services/execution/demo_api_restart_check.py": "b96ca788094995dafb7a69d9bbbd7f6a6dd16687d30bc9bd4271164c307ab239",\n    "services/execution/demo_event_clock.py": "924f3b88c9092355bb4b4ada3114944848208ab5b44ab73ee5b45d5fc1a29bfb",\n    "services/execution/demo_event_inbox.py": "6d9be8c64e144592a5c2343c1db6e4dfc1dee65aa65eeebc08bd52b5aa633281",\n    "services/execution/demo_execution_proof.py": "09c36ceab43021c5cf0d10da08af897124008be2e083deb560572640b3cbe0d4",\n    "services/execution/demo_flat_transport.py": "93f878c9869ad397a52546f0aa976bbe8071366eff36c7d0be6b1329df757401",\n    "services/execution/demo_initial_fill.py": "aa5bcaa7f8b9c6112893c89e8e8e90c74eb16704f1dc16becc6c22bdaec24ba5",\n    "services/execution/demo_managed_order_quota.py": "8d50abbe4da256ef7fe8200b0308be46bc2447b056be63782d461c977dfe6439",\n    "services/execution/demo_managed_runtime.py": "6fb008830fded93d3f6063fc70e559e60a4ebf0489ed666624d4dfe996dbb02a",\n    "services/execution/demo_models.py": "175def10d7378661e82616b02ec87d6dbee4ffdeb4eb94437ce682206168dfb0",\n    "services/execution/demo_order_admission.py": "a6804fa63566b8f7b77927f1a7e0bfe9c3b1b808d6639ffd762a901ee376baa8",\n    "services/execution/demo_preview.py": "58ba6376f61f8467d2b3c9e75923444105f5fe610f9bde4455a94b5fb5d6f267",\n    "services/execution/demo_query_contract.py": "76a81b477465c9b0493f142030fbdeb271510138b30f9fc182ebf4ecf81ba81c",\n    "services/execution/demo_quota_startup_review.py": "459056f3ac54f7d22d76535d133d0ac75b2a899e264f0cab71aacdc4d60fb0a4",\n    "services/execution/demo_reconciliation.py": "7554e0fdcdab08dc222700bde8254ba7979c44c0ec4d82c1d2009ea0e0bb5040",\n    "services/execution/demo_recovery_budget.py": "2c7aa260fd708948b0ae7854257de03bc8b59c86da85180ea5230b07e8df772b",\n    "services/execution/demo_runtime_capacity.py": "75efd11305368ab939eafde160109d64ee71bf6e8f322bf0743935a61ea0cd4d",\n    "services/execution/demo_runtime_confirmation.py": "e56aa98da24654a9f58710aa299014593df5a2648f01fe0b29eccd6ed1693edd",\n    "services/execution/demo_runtime_cost_contract.py": "2ac8942803461666cfde468828ca87d911c21ec84ecd141e235a2adbdc9a2694",\n    "services/execution/demo_runtime_guards.py": "bd8fa5a1c68a5781c30fe27980036e6528e5a0bd5de22efa1b903131b75cd325",\n    "services/execution/demo_runtime_observation.py": "0d4f3c6f08cbc2efd965c892e466438c28552ddbf4b183f81968677fa5092901",\n    "services/execution/demo_runtime_storage.py": "120411bcad5bab49b8233fc57433956621f6fe19fdfa20dcde4c1e649e9284cc",\n    "services/execution/demo_session_diagnostics.py": "78d2bbeadf90bda14a08f5c7dcd8cc8285f9ee55389b07ffb4a8742d052d520b",\n    "services/execution/demo_shared_weight_budget.py": "ca11a8a245ea6e2dcb6dbfaa549a04260c009a8373709cca05a6c8ad9daf393a",\n    "services/execution/demo_stream.py": "770b0783287dcd607c35dad96b8882acd2bcb6a205928046b9831f9920a6a29f",\n    "services/execution/demo_tp_admission_candidate.py": "d8ab1b2ac699b38aaf75977ea97d49fa44ba60a52f518f997fd15f8a2489760a",\n    "services/execution/demo_tp_budget_integration.py": "ab8b13e714c3298cb659b34a6ead0948dd6bf6c943548f0a3657cbc031786399",\n    "services/execution/demo_tp_fenced_handoff_candidate.py": "9fcdf0c42b60dd7bae9f9fe2257a3d9e659581c3b711191b15c531b05084ee6c",\n    "services/execution/demo_tp_first_confirmation_candidate.py": "0b31f29c94fb3f2e56c68c855737eddc312f80871f500867530d078e1190248f",\n    "services/execution/demo_tp_staged_confirmation_candidate.py": "76544f006bdbcc17f54d19dae031e6eaebde831dca0537a15f73069f3d43335b",\n    "services/execution/demo_tp_trigger_check.py": "f2a0cbac4fc33142fa63befb5aa38e1eb7a6ac2bb10018c0ea04290f7e310a6a",\n    "services/execution/demo_tp_uncertain_cancel_candidate.py": "f188463cb0e2d17a160ed4f02fc9aac5b42d30938a44f2d03e8fa468bad48736",\n    "services/execution/demo_validation.py": "725b36a93212733bafed401d1ba059d176d0f43b7a8154288d30df03e3b6b3e4",\n    "services/execution/models.py": "5d362e33788a304e6f2f63f22868454b3804b103277a6fe4a1297c7e50e10a5d",\n    "services/execution/paper.py": "1a847e50fc78d48846fadc9953fad07aed5bbdf83f1e30986d39fef0e0d9fc13",\n    "services/execution/simulator.py": "fa3955b49de8b2a92ae67bea4b85d1f8d1e50581f967df2b96708bdfb3cdc2bd",\n    "services/market_data/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",\n    "services/market_data/integrity.py": "4669f0339610bf1fd183913f542fef7aae9a447ad72d5e57fe2b34e7724d3d8b",\n    "services/market_data/normalization.py": "ba06f00a0778cea08c39442fa9911d4140f1b0b1cf2f916ef7bc50c54cf6bb64",\n    "services/protection/paper.py": "b60ac5ae41db6e7f1bbd8cb294e5adec18685220720a597a18828d83a411655a",\n    "services/risk/paper.py": "7afe00460abae04dda795ab0dba8b8ad84877fa25686342c1fbce3f12ac80832",\n}\n\nBASELINE_PINS = {\n    "integrations/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",\n    "integrations/binance_usdm/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",\n    "integrations/binance_usdm/demo.py": "767d3a29b59882035bdfbf61d3271b39bfece0bff4b359e636db62ad9a5e84eb",\n    "integrations/binance_usdm/demo_rate_limits.py": "d72af60f10a74baea689e11ef8f11b5003f2ab9f89e539290c45b93f2f3d4a4e",\n    "integrations/binance_usdm/demo_snapshot.py": "f80f6e51505ab1095c25b6ee76af01c258cff911aabd02c201bfcddc636f2258",\n    "integrations/binance_usdm/demo_tp_admission.py": "c6a66aff461f2aab59d2f2ae1f42a44c6388268d93893ecf4854c3af111ee0d8",\n    "integrations/binance_usdm/demo_tp_weight_model.py": "611948a52ffad41ecc448549f530558cd4df2585e23273d23422c8a995c7cbc3",\n    "integrations/binance_usdm/demo_tp_window_budget.py": "9433a307767a2e60a4cdbc31a85a8cb386066ff9799249239633ead1cf76120b",\n    "integrations/binance_usdm/filters.py": "0279613f1a17a2bc7ffd50e30be2b6fd1aed8ba240219e8e893b754a067d95ec",\n    "integrations/binance_usdm/orders.py": "2ab366265f7d35356ee9636c6aad4b594b518beeb636407515a57386b3e8441f",\n    "integrations/binance_usdm/recovery_transport.py": "7495c0436437070644627d5ee175d7a63332dc5dc2134574285a7870e8bbba77",\n    "integrations/binance_usdm/rest.py": "9a14edc40fa6365a50fe57c9ddeff4e866044e00287e7c5b680d8bb3dbd0ad52",\n    "packages/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",\n    "packages/domain/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",\n    "packages/domain/config.py": "c2bd7a039e3ea007a1e34a6dbdd058cc626e2690cc8d55b0761cfd2085bf311d",\n    "packages/domain/contracts.py": "71c0a95faad586d6e3f342d83b6b95a4772cb6e2c1808b44fc51fdf9f1332947",\n    "packages/persistence/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",\n    "packages/persistence/demo.py": "40f9a33b8eeda28c96f36de35656e4667a2d2461ce9de79dc4b19a687cd5cd5a",\n    "packages/persistence/demo_alerts.py": "fb8986d57ef850c7b707d59730b002ead125429e1d23d310a89b3c7e69385330",\n    "packages/persistence/demo_order_quota.py": None,\n    "packages/persistence/demo_shared_checkpoint.py": None,\n    "packages/persistence/demo_shared_weight.py": None,\n    "packages/persistence/market.py": "89265a7bc7d181863983f4d24e6bdbe7499bb5b8154a6b22c6aea46c0d216050",\n    "packages/persistence/paper.py": "312f2ab4478e2cb7f980045c56ac1329d080d8e54d60f960a93ae0f2fe1d2bd7",\n    "services/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",\n    "services/execution/demo.py": "deb90dd8ddd9df627054a11c76f8dd8b09e364f2aaaca8f82b7fd0c237c444ab",\n    "services/execution/demo_api_restart_check.py": "b96ca788094995dafb7a69d9bbbd7f6a6dd16687d30bc9bd4271164c307ab239",\n    "services/execution/demo_event_clock.py": "924f3b88c9092355bb4b4ada3114944848208ab5b44ab73ee5b45d5fc1a29bfb",\n    "services/execution/demo_event_inbox.py": "6d9be8c64e144592a5c2343c1db6e4dfc1dee65aa65eeebc08bd52b5aa633281",\n    "services/execution/demo_execution_proof.py": "09c36ceab43021c5cf0d10da08af897124008be2e083deb560572640b3cbe0d4",\n    "services/execution/demo_flat_transport.py": "93f878c9869ad397a52546f0aa976bbe8071366eff36c7d0be6b1329df757401",\n    "services/execution/demo_initial_fill.py": "aa5bcaa7f8b9c6112893c89e8e8e90c74eb16704f1dc16becc6c22bdaec24ba5",\n    "services/execution/demo_managed_order_quota.py": None,\n    "services/execution/demo_managed_runtime.py": None,\n    "services/execution/demo_models.py": "175def10d7378661e82616b02ec87d6dbee4ffdeb4eb94437ce682206168dfb0",\n    "services/execution/demo_order_admission.py": None,\n    "services/execution/demo_preview.py": "58ba6376f61f8467d2b3c9e75923444105f5fe610f9bde4455a94b5fb5d6f267",\n    "services/execution/demo_query_contract.py": None,\n    "services/execution/demo_quota_startup_review.py": None,\n    "services/execution/demo_reconciliation.py": "7554e0fdcdab08dc222700bde8254ba7979c44c0ec4d82c1d2009ea0e0bb5040",\n    "services/execution/demo_recovery_budget.py": "2c7aa260fd708948b0ae7854257de03bc8b59c86da85180ea5230b07e8df772b",\n    "services/execution/demo_runtime_capacity.py": None,\n    "services/execution/demo_runtime_confirmation.py": None,\n    "services/execution/demo_runtime_cost_contract.py": None,\n    "services/execution/demo_runtime_guards.py": None,\n    "services/execution/demo_runtime_observation.py": None,\n    "services/execution/demo_runtime_storage.py": None,\n    "services/execution/demo_session_diagnostics.py": "78d2bbeadf90bda14a08f5c7dcd8cc8285f9ee55389b07ffb4a8742d052d520b",\n    "services/execution/demo_shared_weight_budget.py": None,\n    "services/execution/demo_stream.py": "770b0783287dcd607c35dad96b8882acd2bcb6a205928046b9831f9920a6a29f",\n    "services/execution/demo_tp_admission_candidate.py": "d8ab1b2ac699b38aaf75977ea97d49fa44ba60a52f518f997fd15f8a2489760a",\n    "services/execution/demo_tp_budget_integration.py": "ab8b13e714c3298cb659b34a6ead0948dd6bf6c943548f0a3657cbc031786399",\n    "services/execution/demo_tp_fenced_handoff_candidate.py": "9fcdf0c42b60dd7bae9f9fe2257a3d9e659581c3b711191b15c531b05084ee6c",\n    "services/execution/demo_tp_first_confirmation_candidate.py": "0b31f29c94fb3f2e56c68c855737eddc312f80871f500867530d078e1190248f",\n    "services/execution/demo_tp_staged_confirmation_candidate.py": "76544f006bdbcc17f54d19dae031e6eaebde831dca0537a15f73069f3d43335b",\n    "services/execution/demo_tp_trigger_check.py": "f2a0cbac4fc33142fa63befb5aa38e1eb7a6ac2bb10018c0ea04290f7e310a6a",\n    "services/execution/demo_tp_uncertain_cancel_candidate.py": "f188463cb0e2d17a160ed4f02fc9aac5b42d30938a44f2d03e8fa468bad48736",\n    "services/execution/demo_validation.py": "725b36a93212733bafed401d1ba059d176d0f43b7a8154288d30df03e3b6b3e4",\n    "services/execution/models.py": "5d362e33788a304e6f2f63f22868454b3804b103277a6fe4a1297c7e50e10a5d",\n    "services/execution/paper.py": "1a847e50fc78d48846fadc9953fad07aed5bbdf83f1e30986d39fef0e0d9fc13",\n    "services/execution/simulator.py": "fa3955b49de8b2a92ae67bea4b85d1f8d1e50581f967df2b96708bdfb3cdc2bd",\n    "services/market_data/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",\n    "services/market_data/integrity.py": "4669f0339610bf1fd183913f542fef7aae9a447ad72d5e57fe2b34e7724d3d8b",\n    "services/market_data/normalization.py": "ba06f00a0778cea08c39442fa9911d4140f1b0b1cf2f916ef7bc50c54cf6bb64",\n    "services/protection/paper.py": "b60ac5ae41db6e7f1bbd8cb294e5adec18685220720a597a18828d83a411655a",\n    "services/risk/paper.py": "7afe00460abae04dda795ab0dba8b8ad84877fa25686342c1fbce3f12ac80832",\n}\n\n\ndef verify_install_files(phase, root=Path(".")):\n    for name, target in TARGET_PINS.items():\n        path = root / name\n        if phase == "pre" and not path.exists():\n            require(BASELINE_PINS[name] is None, "INSTALL_DEPENDENCY_MISSING")\n            continue\n        require(path.is_file() and not path.is_symlink(), "INSTALL_SOURCE_CONFLICT")\n        actual = hashlib.sha256(path.read_bytes()).hexdigest()\n        allowed = {target} if phase == "post" else {target, BASELINE_PINS[name]}\n        require(actual in allowed, "INSTALL_SOURCE_CONFLICT")\n\n\ndef verify_default_refusal():\n    from packages.persistence.demo_order_quota import LocalQuotaStopped\n    from services.execution.demo_managed_runtime import assemble_managed_runtime\n\n    try:\n        assemble_managed_runtime(None, None, None, None, None)\n    except LocalQuotaStopped as error:\n        require(\n            str(error) == "RUNTIME_REAL_EXECUTION_DISABLED", "DEFAULT_EXECUTION_REFUSAL_CHANGED"\n        )\n    else:\n        raise Blocked("DEFAULT_EXECUTION_REFUSAL_CHANGED")\n\n\nSETUP_KEY = "MANAGED_RUNTIME:STORAGE_SETUP_20261111_V1"\n\nDONE_KEY = "MANAGED_RUNTIME:STORAGE_SETUP_COMPLETE_20261111_V1"\n\nORDER_KEY = "MANAGED_QUOTA:STORAGE_V1"\n\nSHARED_KEY = "MANAGED_RUNTIME:SHARED_STORAGE_V1"\n\nSETUP_KEYS = (SETUP_KEY, DONE_KEY, ORDER_KEY, SHARED_KEY)\n\nSTORAGE_PARENT = Path("/app/demo-export-state")\n\nSTORAGE_DIRECTORY = "demo-runtime-20261111"\n\nSTORAGE_SCOPE = "DEMO-WINDOWS-TP-MANAGED-WRITERS-V1"\n\nSOURCE_REFERENCE = "e03b6ba683732cd5872c30cf0d245a5b1fb6d325"\n\n\ndef setup_preserved(ledger):\n    original = ledger.model_copy(deep=True)\n    for key in SETUP_KEYS:\n        original.operations.pop(key, None)\n    return digest(original.model_dump(mode="json"))\n\n\ndef setup_context(store, lease=None, *, allow_stale_period=False):\n    with store.engine.connect() as conn, conn.begin():\n        conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")\n        conn.exec_driver_sql("SET LOCAL statement_timeout=\'3s\'")\n        row = (\n            conn.execute(select(accounts).where(accounts.c.account_id == ACCOUNT)).mappings().one()\n        )\n    if lease is None:\n        require(\n            row["owner"] is None\n            and (row["lease_until"] is None or row["lease_until"] <= store.now()),\n            "SETUP_ACTIVE_OWNER",\n        )\n    else:\n        require(\n            row["owner"] == str(lease.owner)\n            and row["fence"] == lease.fence\n            and row["lease_until"] is not None\n            and row["lease_until"] > store.now(),\n            "SETUP_CURRENT_LEASE_REQUIRED",\n        )\n    ledger = DemoLedger.model_validate(row["state"])\n    empty(ledger)\n    require(ledger.paused and ledger.risk_state.value == "NORMAL", "SETUP_PAUSED_NORMAL_REQUIRED")\n    require(\n        allow_stale_period\n        or period_keys(store.now(), ledger.policy.week_start_kst)\n        == (ledger.daily_key, ledger.weekly_key),\n        "SETUP_PERIOD_MISMATCH",\n    )\n    original = ledger.model_copy(deep=True)\n    for key in SETUP_KEYS:\n        original.operations.pop(key, None)\n    require(day_recorded(original), "SETUP_DAY_RECEIPT_REQUIRED")\n    return ledger\n\n\ndef _setup_root(parent):\n    import stat\n\n    require(parent.is_dir() and not parent.is_symlink(), "SETUP_PRIVATE_PARENT_REQUIRED")\n    info = parent.stat()\n    require(\n        info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,\n        "SETUP_PRIVATE_PARENT_REQUIRED",\n    )\n    root = parent / STORAGE_DIRECTORY\n    require(not root.is_symlink(), "SETUP_PRIVATE_ROOT_REQUIRED")\n    if root.exists():\n        info = root.stat()\n        require(\n            root.is_dir() and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,\n            "SETUP_PRIVATE_ROOT_REQUIRED",\n        )\n    return root\n\n\ndef _read_setup(ledger):\n    raw = ledger.operations.get(SETUP_KEY)\n    require(raw is not None, "SETUP_INTENT_REQUIRED")\n    try:\n        record = json.loads(raw)\n    except (ValueError, TypeError):\n        raise Blocked("SETUP_INTENT_INVALID") from None\n    require(\n        isinstance(record, dict)\n        and record.get("version") == 1\n        and type(record["version"]) is int\n        and record.get("account") == ACCOUNT\n        and record.get("source") == SOURCE_REFERENCE\n        and record.get("scope") == STORAGE_SCOPE\n        and record.get("directory") == STORAGE_DIRECTORY\n        and record.get("preserved_ledger_sha256") == setup_preserved(ledger),\n        "SETUP_INTENT_INVALID",\n    )\n    try:\n        UUID(record["shared_journal"])\n    except (ValueError, TypeError, KeyError):\n        raise Blocked("SETUP_INTENT_INVALID") from None\n    return record\n\n\ndef _storage_objects(anchor, root, record, clocks):\n    from services.execution.demo_runtime_storage import RuntimeStorage\n\n    return RuntimeStorage(\n        root / "order-quota",\n        root / "shared-weight",\n        STORAGE_SCOPE,\n        UUID(record["shared_journal"]),\n        anchor,\n    )\n\n\ndef _verify_prepared(ledger, anchor, root, clocks, *, progress=lambda stage: None):\n    from packages.persistence.demo_order_quota import DurableOrderQuota\n    from packages.persistence.demo_shared_checkpoint import AnchoredSharedWeightJournal\n    from services.execution.demo_managed_order_quota import validate_journal_bindings\n    from services.execution.demo_runtime_storage import runtime_storage_binding\n\n    progress("SETUP_INTENT_VALIDATION")\n    record = _read_setup(ledger)\n    require(record.get("stage") == "PREPARED", "SETUP_INCOMPLETE_NO_AUTOMATIC_RETRY")\n    require(\n        str(anchor.installation_id()) == record.get("anchor_installation"), "SETUP_ANCHOR_CHANGED"\n    )\n    try:\n        order_id = UUID(record["order_journal"])\n    except (ValueError, TypeError, KeyError):\n        raise Blocked("SETUP_INTENT_INVALID") from None\n    progress("ORDER_JOURNAL_VALIDATION")\n    order = DurableOrderQuota(\n        root / "order-quota",\n        ACCOUNT,\n        order_id,\n        read_only=True,\n        monotonic=clocks.monotonic,\n        wall_clock=clocks.wall_clock,\n    )\n    try:\n        validate_journal_bindings(ledger, order)\n        require(not order.flow_records() and not order._events(), "SETUP_ORDER_FLOWS_NOT_EMPTY")\n    finally:\n        order.close()\n    storage = _storage_objects(anchor, root, record, clocks)\n    require(\n        record.get("expected_runtime_binding") == runtime_storage_binding(storage),\n        "SETUP_INTENT_INVALID",\n    )\n    progress("SHARED_JOURNAL_VALIDATION")\n    shared_reader = AnchoredSharedWeightJournal(\n        storage.shared_directory,\n        STORAGE_SCOPE,\n        storage.shared_identity,\n        anchors=anchor,\n        read_only=True,\n        monotonic=clocks.monotonic,\n        wall_clock=clocks.wall_clock,\n        boot=clocks.boot,\n    )\n    with shared_reader._transaction() as (connection, meta, _):\n        require(\n            meta["total"] == 0\n            and connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0,\n            "SETUP_SHARED_REQUESTS_NOT_EMPTY",\n        )\n    pressure = shared_reader.view()\n    require(\n        pressure["pending_attempts"] == 0 and pressure["pressure"] == 0,\n        "SETUP_SHARED_REQUESTS_NOT_EMPTY",\n    )\n    return record, storage, pressure\n\n\ndef _verify_done(ledger, record):\n    raw = ledger.operations.get(DONE_KEY)\n    require(raw is not None, "SETUP_COMPLETE_RECEIPT_REQUIRED")\n    try:\n        receipt = json.loads(raw)\n    except (ValueError, TypeError):\n        raise Blocked("SETUP_COMPLETE_RECEIPT_INVALID") from None\n    require(\n        isinstance(receipt, dict)\n        and receipt.get("version") == 1\n        and type(receipt.get("version")) is int\n        and receipt.get("setup_intent_sha256") == digest(record)\n        and receipt.get("preserved_ledger_sha256") == setup_preserved(ledger)\n        and receipt.get("order_binding_sha256") == digest(ledger.operations.get(ORDER_KEY))\n        and receipt.get("runtime_binding_sha256") == digest(record["expected_runtime_binding"])\n        and ledger.operations.get(SHARED_KEY) == record["expected_runtime_binding"]\n        and receipt.get("execution_approved") is False,\n        "SETUP_COMPLETE_RECEIPT_INVALID",\n    )\n\n\ndef _setup_report(status, ledger=None, **values):\n    report = {\n        "runtime_storage_setup": status,\n        "execution_approved": False,\n        "orders_enabled": False,\n        "live_trading_enabled": False,\n        "http_attempts": 0,\n        "order_writes": 0,\n        "credentials_accessed": False,\n        "actual_ip_capacity_reserved": False,\n        "external_account_order_usage": "UNKNOWN",\n        "venue_request_cost_upper_bounds_proven": False,\n        "startup_hold_seconds": 70.001,\n    }\n    if ledger is not None:\n        report.update(\n            day_kst=ledger.daily_key,\n            week_kst=ledger.weekly_key,\n            paused=ledger.paused,\n            risk_state=ledger.risk_state.value,\n            daily_base_usdt=str(ledger.daily_base),\n            weekly_base_usdt=str(ledger.weekly_base),\n            high_water_usdt=str(ledger.high_water),\n        )\n    return report | values\n\n\ndef setup_status(\n    store,\n    anchor,\n    *,\n    parent=STORAGE_PARENT,\n    clocks=None,\n    allow_stale_period=False,\n    progress=lambda stage: None,\n):\n    from services.execution.demo_runtime_storage import RuntimeClocks\n\n    clocks = clocks or RuntimeClocks()\n    progress("LEDGER_AND_RECEIPTS_VALIDATION")\n    ledger = setup_context(store, allow_stale_period=allow_stale_period)\n    root = _setup_root(parent)\n    if SETUP_KEY not in ledger.operations:\n        require(\n            not any(k in ledger.operations for k in (ORDER_KEY, SHARED_KEY, DONE_KEY)),\n            "SETUP_UNRELATED_BINDINGS_PRESENT",\n        )\n        require(\n            not root.exists() and not root.is_symlink(), "SETUP_ORPHAN_DIRECTORY_NO_AUTOMATIC_RETRY"\n        )\n        return _setup_report(\n            "PREPARATION_REQUIRED", ledger, database_changed=False, filesystem_changed=False\n        )\n    record, storage, pressure = _verify_prepared(ledger, anchor, root, clocks, progress=progress)\n    if DONE_KEY in ledger.operations:\n        progress("SETUP_COMPLETION_VALIDATION")\n        _verify_done(ledger, record)\n        status = "CONFIGURED_KEEP_PAUSED"\n    else:\n        require(\n            SHARED_KEY not in ledger.operations\n            or ledger.operations[SHARED_KEY] == record["expected_runtime_binding"],\n            "SETUP_BINDING_CONFLICT",\n        )\n        status = "READY_TO_FINALIZE" if pressure["startup_ready"] else "WAIT_STARTUP_HOLD"\n    return _setup_report(\n        status,\n        ledger,\n        database_changed=False,\n        filesystem_changed=False,\n        storage_bindings_verified=True,\n        startup_ready=pressure["startup_ready"],\n        preserved_ledger_sha256=setup_preserved(ledger),\n    )\n\n\nPINNED_PROBE_SHA256 = "81ded98a1a36e7e7a46c25d3b5828e03cc41a16de44e0d0578f73219cb37c33d"\nPINNED_PROBE_SOURCE = \'"""Four GETs only: review DEMO request-weight and account order-quota evidence.\\n\\nDoes not install code, open a stream, access a DB, acquire leadership or authorize\\norders. The dedicated new USER_DATA query does not extend SDK route allowlists.\\n"""\\n\\n# SDK imports intentionally follow the stdlib-only source cohort check.\\n# ruff: noqa: E402\\n\\nimport hashlib\\nimport json\\nimport math\\nimport os\\nimport time\\nfrom collections.abc import Iterable, Mapping\\nfrom pathlib import Path\\nfrom typing import Any, Protocol\\nfrom urllib.error import HTTPError, URLError\\nfrom urllib.parse import parse_qs\\nfrom urllib.request import Request, build_opener\\n\\n# Check the installed cohort using stdlib only, before importing any SDK source.\\nINSTALLED_PINS: dict[str, str] = {\\n    "alembic.ini": "e7955a0ffb9c32a9c5067d7885ee9197328f17a4a12fb4a5bb556c2b85763aa2",\\n    "configs/demo-isolated.example.yaml": "d93e85c2e056ffbbc3dac5fab9f0e5bf28032fd16f41bbff1f3ae81fb00882ca",\\n    "integrations/binance_usdm/demo.py": "767d3a29b59882035bdfbf61d3271b39bfece0bff4b359e636db62ad9a5e84eb",\\n    "integrations/binance_usdm/demo_rate_limits.py": "d72af60f10a74baea689e11ef8f11b5003f2ab9f89e539290c45b93f2f3d4a4e",\\n    "integrations/binance_usdm/demo_read_routing.py": "a6e96b21135882a272cb5c97f86ddc14c3a5f4fa79dd115e40397252c679a66d",\\n    "integrations/binance_usdm/demo_snapshot.py": "f80f6e51505ab1095c25b6ee76af01c258cff911aabd02c201bfcddc636f2258",\\n    "integrations/binance_usdm/demo_tp_admission.py": "c6a66aff461f2aab59d2f2ae1f42a44c6388268d93893ecf4854c3af111ee0d8",\\n    "integrations/binance_usdm/demo_tp_weight_model.py": "611948a52ffad41ecc448549f530558cd4df2585e23273d23422c8a995c7cbc3",\\n    "integrations/binance_usdm/demo_tp_window_budget.py": "9433a307767a2e60a4cdbc31a85a8cb386066ff9799249239633ead1cf76120b",\\n    "integrations/binance_usdm/filters.py": "0279613f1a17a2bc7ffd50e30be2b6fd1aed8ba240219e8e893b754a067d95ec",\\n    "integrations/binance_usdm/orders.py": "2ab366265f7d35356ee9636c6aad4b594b518beeb636407515a57386b3e8441f",\\n    "integrations/binance_usdm/recovery_transport.py": "7495c0436437070644627d5ee175d7a63332dc5dc2134574285a7870e8bbba77",\\n    "integrations/binance_usdm/rest.py": "9a14edc40fa6365a50fe57c9ddeff4e866044e00287e7c5b680d8bb3dbd0ad52",\\n    "migrations/env.py": "4256345fc24dd52ba706aa9b712268b9df436d52bafa7238234272d754734086",\\n    "migrations/script.py.mako": "67042994cfc495007c508ccae15e44d21057d90222461630f9bc04d3657f5f6d",\\n    "migrations/versions/0001_public_market_data.py": "b62afcb18d6d6c42d710ca0cb0eb42001e174934f11678443dc5ac24d86932f7",\\n    "migrations/versions/0002_paper.py": "91ffad2a439b9f47e2636939a5aba091490942db7a87bb0d87124a531870c77d",\\n    "migrations/versions/0003_demo.py": "6b2a26ba34855a3dee6d3b2c67eb9b3ecac644b4e2e31ad6353e907a3c233762",\\n    "migrations/versions/0004_demo_urgent_alerts.py": "da9e4de16f1f9d4c15d70293d3b252084648fbbcb060f8ec7740b9a85f83e8ad",\\n    "packages/domain/config.py": "c2bd7a039e3ea007a1e34a6dbdd058cc626e2690cc8d55b0761cfd2085bf311d",\\n    "packages/domain/contracts.py": "71c0a95faad586d6e3f342d83b6b95a4772cb6e2c1808b44fc51fdf9f1332947",\\n    "packages/persistence/demo.py": "40f9a33b8eeda28c96f36de35656e4667a2d2461ce9de79dc4b19a687cd5cd5a",\\n    "pyproject.toml": "54760a9fa52fc4cc877d2302c7952688ddd13e12f1efc8e44a0974aae8914019",\\n    "scripts/check_demo_disconnect.py": "c85c013412053237eda7ed82a8948aa4b50f9da1951d173360a1ecfb46228a1e",\\n    "scripts/check_demo_session_ready.py": "18259376c0404f98bc0cb91afeb31e33789f581562e14939989816c2fa8754d9",\\n    "scripts/check_demo_tp_trigger.py": "a0bdd07027d5e3ca9b54a1468841fe2d40cf072c38c3c91c6c58292c2e67d96b",\\n    "scripts/demo_key_session.py": "85c698bc8bbad98bad4051c346a5e4e0a3a3bd22f8bbb90a362f835cadc633dd",\\n    "scripts/review_tp_minute_budget.py": "e8462dddf04d6d9107d01d7fbe8373df9a3a1e0cf6361d5f057021b2f67e9870",\\n    "services/execution/demo.py": "deb90dd8ddd9df627054a11c76f8dd8b09e364f2aaaca8f82b7fd0c237c444ab",\\n    "services/execution/demo_api_restart_check.py": "b96ca788094995dafb7a69d9bbbd7f6a6dd16687d30bc9bd4271164c307ab239",\\n    "services/execution/demo_empty_db_restart.py": "6adcc8284b43050bc4a115f2796c001d424ba7e0261654c099075fb3d950e39f",\\n    "services/execution/demo_empty_worker_recovery.py": "8e3cac8da89fb80ef9f3654ae8591ffea7be1db1c7dc60070da72b72851b2070",\\n    "services/execution/demo_event_clock.py": "924f3b88c9092355bb4b4ada3114944848208ab5b44ab73ee5b45d5fc1a29bfb",\\n    "services/execution/demo_event_inbox.py": "6d9be8c64e144592a5c2343c1db6e4dfc1dee65aa65eeebc08bd52b5aa633281",\\n    "services/execution/demo_flat_transport.py": "93f878c9869ad397a52546f0aa976bbe8071366eff36c7d0be6b1329df757401",\\n    "services/execution/demo_models.py": "175def10d7378661e82616b02ec87d6dbee4ffdeb4eb94437ce682206168dfb0",\\n    "services/execution/demo_preview.py": "58ba6376f61f8467d2b3c9e75923444105f5fe610f9bde4455a94b5fb5d6f267",\\n    "services/execution/demo_private_stability.py": "d3aee78f2da73c15c3eff5f89403fae666de5109ad3c2402585f2150df1a5123",\\n    "services/execution/demo_protected_worker_check.py": "70d9f0a6ec74223e8a7a82ec55c976288c10fb13c01c9b478f49f1a0fd408709",\\n    "services/execution/demo_recovery.py": "ca663f098d2f7c462d1f225f4ade91d743169175f125c574794dc58175c8c76e",\\n    "services/execution/demo_recovery_budget.py": "2c7aa260fd708948b0ae7854257de03bc8b59c86da85180ea5230b07e8df772b",\\n    "services/execution/demo_stream.py": "770b0783287dcd607c35dad96b8882acd2bcb6a205928046b9831f9920a6a29f",\\n    "services/execution/demo_tp_admission_candidate.py": "d8ab1b2ac699b38aaf75977ea97d49fa44ba60a52f518f997fd15f8a2489760a",\\n    "services/execution/demo_tp_budget_integration.py": "ab8b13e714c3298cb659b34a6ead0948dd6bf6c943548f0a3657cbc031786399",\\n    "services/execution/demo_tp_fenced_handoff_candidate.py": "9fcdf0c42b60dd7bae9f9fe2257a3d9e659581c3b711191b15c531b05084ee6c",\\n    "services/execution/demo_tp_first_confirmation_candidate.py": "0b31f29c94fb3f2e56c68c855737eddc312f80871f500867530d078e1190248f",\\n    "services/execution/demo_tp_staged_confirmation_candidate.py": "76544f006bdbcc17f54d19dae031e6eaebde831dca0537a15f73069f3d43335b",\\n    "services/execution/demo_tp_trigger_check.py": "f2a0cbac4fc33142fa63befb5aa38e1eb7a6ac2bb10018c0ea04290f7e310a6a",\\n    "services/execution/demo_tp_uncertain_cancel_candidate.py": "f188463cb0e2d17a160ed4f02fc9aac5b42d30938a44f2d03e8fa468bad48736",\\n    "services/execution/demo_validation.py": "725b36a93212733bafed401d1ba059d176d0f43b7a8154288d30df03e3b6b3e4",\\n    "services/execution/models.py": "5d362e33788a304e6f2f63f22868454b3804b103277a6fe4a1297c7e50e10a5d",\\n    "services/execution/paper.py": "1a847e50fc78d48846fadc9953fad07aed5bbdf83f1e30986d39fef0e0d9fc13",\\n    "services/execution/readiness_closed_history.py": "0c9cb90bc74402fdaed025f715a98a2e47169eee4d1f9990c75e8f5671e358da",\\n    "services/risk/paper.py": "7afe00460abae04dda795ab0dba8b8ad84877fa25686342c1fbce3f12ac80832",\\n    "uv.lock": "49bbe38dc3f559466c4666febe686926db6503413a45bd766088ed2454e01097",\\n}\\n\\n\\ndef _verify_source_bytes_before_import() -> None:\\n    if len(INSTALLED_PINS) != 55:\\n        raise ValueError("PIN_COHORT_INVALID")\\n    for name, digest in INSTALLED_PINS.items():\\n        path = Path(name)\\n        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:\\n            raise ValueError("INSTALLED_SOURCE_MISMATCH")\\n\\n\\nif __name__ == "__main__":\\n    try:\\n        _verify_source_bytes_before_import()\\n    except Exception:\\n        print(\\n            json.dumps(\\n                {\\n                    "tp_entry_admission_review": "BLOCKED_KEEP_PAUSED_NO_RETRY",\\n                    "stop_reason": "INSTALLED_SOURCE_MISMATCH",\\n                    "database_accessed": False,\\n                    "order_writes": 0,\\n                    "execution_approved": False,\\n                    "orders_enabled": False,\\n                    "live_trading_enabled": False,\\n                }\\n            )\\n        )\\n        raise SystemExit(2) from None\\n\\nfrom pydantic import SecretStr\\n\\nfrom integrations.binance_usdm.demo import (\\n    REST_HOST,\\n    DemoClient,\\n    DemoCredentials,\\n    DemoFailure,\\n    DemoRateLimited,\\n    DemoSettings,\\n    DemoUnknown,\\n    Transport,\\n)\\nfrom integrations.binance_usdm.demo_rate_limits import rate_headers\\nfrom integrations.binance_usdm.demo_tp_admission import published_limit\\nfrom integrations.binance_usdm.rest import NoRedirect, RESTResponse, prepare_signed_query\\nfrom packages.domain.config import UniqueKeyLoader\\nfrom scripts.demo_key_session import credentials\\nfrom services.execution.demo_order_admission import (\\n    AdmissionEvidenceInvalid,\\n    OrderSample,\\n    assess_order_admission,\\n    published_order_windows,\\n)\\n\\nORDER_USAGE_PATH = "/fapi/v1/rateLimit/order"\\nPROBE_PATHS = frozenset({"/fapi/v1/time", "/fapi/v1/exchangeInfo", ORDER_USAGE_PATH})\\nMAX_PROBE_REQUESTS = 4\\nMAX_PROBE_SECONDS = 10\\n\\n\\nclass EvidenceStop(DemoFailure):\\n    pass\\n\\n\\ndef evidence_require(condition: bool, code: str) -> None:\\n    if not condition:\\n        raise EvidenceStop(code)\\n\\n\\ndef verify_installed_sources() -> None:\\n    evidence_require(len(INSTALLED_PINS) == 55, "PIN_COHORT_INVALID")\\n    for name, digest in INSTALLED_PINS.items():\\n        path = Path(name)\\n        evidence_require(path.is_file(), "INSTALLED_SOURCE_MISMATCH")\\n        evidence_require(\\n            hashlib.sha256(path.read_bytes()).hexdigest() == digest, "INSTALLED_SOURCE_MISMATCH"\\n        )\\n\\n\\nclass AdmissionHeaders(Protocol):\\n    def items(self) -> Iterable[tuple[str, str]]: ...\\n\\n\\ndef admission_response_headers(raw: AdmissionHeaders) -> dict[str, str]:\\n    safe = rate_headers(dict(raw.items()))\\n    for counter in ("x-mbx-order-count-10s", "x-mbx-order-count-1m"):\\n        values = [value for name, value in raw.items() if name.lower() == counter]\\n        if values:\\n            evidence_require(\\n                len(values) == 1\\n                and isinstance(values[0], str)\\n                and values[0].isascii()\\n                and values[0].isdecimal()\\n                and len(values[0]) <= 9,\\n                "ORDER_COUNTER_HEADER_INVALID",\\n            )\\n            safe[counter] = values[0]\\n    return safe\\n\\n\\ndef admission_read_transport(\\n    method: str, path: str, query: SecretStr, api_key: SecretStr | None\\n) -> RESTResponse:\\n    # Unlike the SDK\\\'s generic request transport, this boundary has no mutation\\n    # branch. Neither caller-controlled URLs nor redirects are permitted.\\n    evidence_require(REST_HOST == "https://demo-fapi.binance.com", "DEMO_HOST_REQUIRED")\\n    evidence_require(method == "GET" and path in PROBE_PATHS, "READ_SCOPE_FORBIDDEN")\\n    params = parse_qs(query.get_secret_value(), keep_blank_values=True)\\n    if path == ORDER_USAGE_PATH:\\n        evidence_require(\\n            set(params) == {"timestamp", "recvWindow", "signature"}\\n            and all(len(v) == 1 for v in params.values())\\n            and params["timestamp"][0].isascii()\\n            and params["timestamp"][0].isdecimal()\\n            and params["recvWindow"] == ["5000"]\\n            and len(params["signature"][0]) == 64\\n            and all(c in "0123456789abcdef" for c in params["signature"][0])\\n            and isinstance(api_key, SecretStr)\\n            and bool(api_key.get_secret_value()),\\n            "SIGNED_READ_SCOPE_FORBIDDEN",\\n        )\\n    else:\\n        evidence_require(\\n            not query.get_secret_value() and api_key is None, "PUBLIC_READ_SCOPE_FORBIDDEN"\\n        )\\n    headers = {"Accept": "application/json"}\\n    if api_key is not None:\\n        headers["X-MBX-APIKEY"] = api_key.get_secret_value()\\n    target = REST_HOST + path + ("?" + query.get_secret_value() if params else "")\\n    request = Request(target, method="GET", headers=headers)\\n    try:\\n        with build_opener(NoRedirect()).open(request, timeout=5) as response:\\n            return RESTResponse(\\n                response.status,\\n                response.read(4_194_305),\\n                admission_response_headers(response.headers),\\n            )\\n    except HTTPError as error:\\n        try:\\n            return RESTResponse(\\n                error.code,\\n                error.read(4_194_305),\\n                rate_headers(dict(error.headers.items()))\\n                if error.code in {418, 429}\\n                else admission_response_headers(error.headers),\\n            )\\n        finally:\\n            error.close()\\n    except (URLError, OSError):\\n        raise DemoUnknown("Read-only admission transport unavailable") from None\\n\\n\\nclass AdmissionReadMonitor:\\n    def __init__(self, sender: Transport, *, monotonic: Any = time.monotonic):\\n        self.sender, self.clock = sender, monotonic\\n        self.started = self.last = monotonic()\\n        self.records: list[tuple[str, int]] = []\\n        self.weight_limit: int | None = None\\n        self.peak_weight = 0\\n        self.limited = False\\n        self.last_order_headers: dict[str, int] = {}\\n\\n    def check(self) -> None:\\n        now = self.clock()\\n        evidence_require(\\n            math.isfinite(now)\\n            and math.isfinite(self.started)\\n            and self.last <= now\\n            and 0 <= now - self.started < MAX_PROBE_SECONDS,\\n            "PROBE_CLOCK_OR_TIME_BUDGET",\\n        )\\n        self.last = now\\n        evidence_require(not self.limited, "RATE_LIMITED_NO_RETRY")\\n        if self.weight_limit is not None:\\n            evidence_require(\\n                self.peak_weight + 100 < self.weight_limit * 3 // 5,\\n                "READ_WEIGHT_HEADROOM_INSUFFICIENT",\\n            )\\n\\n    def enable(self, info: Any) -> None:\\n        self.weight_limit = published_limit(info)\\n        published_order_windows(info)\\n        self.check()\\n\\n    def __call__(\\n        self, method: str, path: str, query: SecretStr, key: SecretStr | None\\n    ) -> RESTResponse:\\n        evidence_require(method == "GET" and path in PROBE_PATHS, "READ_SCOPE_FORBIDDEN")\\n        evidence_require(len(self.records) < MAX_PROBE_REQUESTS, "PROBE_HTTP_BUDGET")\\n        if path == ORDER_USAGE_PATH:\\n            evidence_require(self.weight_limit is not None, "LIMIT_DISCOVERY_REQUIRED")\\n        self.check()\\n        self.records.append((path, 0))  # Count attempts even when a response is lost.\\n        response = self.sender(method, path, query, key)\\n        if response.status in {418, 429}:\\n            self.limited = True\\n            return response  # Let the original SDK set cooldown and raise its type.\\n        self.last_order_headers = {}\\n        for counter in ("x-mbx-order-count-10s", "x-mbx-order-count-1m"):\\n            values = [value for name, value in response.headers.items() if name.lower() == counter]\\n            if values:\\n                evidence_require(\\n                    len(values) == 1\\n                    and isinstance(values[0], str)\\n                    and values[0].isascii()\\n                    and values[0].isdecimal()\\n                    and len(values[0]) <= 9,\\n                    "ORDER_COUNTER_HEADER_INVALID",\\n                )\\n                self.last_order_headers[counter] = int(values[0])\\n        safe = rate_headers(response.headers)\\n        value = safe.get("x-mbx-used-weight-1m")\\n        evidence_require(value is not None, "WEIGHT_HEADER_UNAVAILABLE")\\n        self.peak_weight = max(self.peak_weight, int(value or "0"))\\n        self.check()\\n        return response\\n\\n\\nclass AdmissionReadClient(DemoClient):\\n    def __init__(self, settings: DemoSettings, key: DemoCredentials, **kwargs: Any):\\n        evidence_require(\\n            not settings.demo_trading_enabled\\n            and not settings.live_trading_enabled\\n            and key.environment == "DEMO",\\n            "READ_ONLY_CONTEXT_REQUIRED",\\n        )\\n        super().__init__(settings, key, **kwargs)\\n\\n    def public(self, path: str, params: Mapping[str, str] | None = None) -> Any:\\n        evidence_require(\\n            path in {"/fapi/v1/time", "/fapi/v1/exchangeInfo"} and not params,\\n            "PUBLIC_READ_SCOPE_FORBIDDEN",\\n        )\\n        return super().public(path, params)\\n\\n    def request(self, method: str, path: str, params: Mapping[str, str] | None = None) -> Any:\\n        raise EvidenceStop("GENERIC_SIGNED_REQUEST_FORBIDDEN")\\n\\n    def listen_key(self, *args: Any, **kwargs: Any) -> SecretStr | None:\\n        raise EvidenceStop("LISTEN_KEY_FORBIDDEN")\\n\\n    def query_order_usage(self) -> Any:\\n        evidence_require(\\n            not self.settings.demo_trading_enabled and not self.settings.live_trading_enabled,\\n            "READ_ONLY_CONTEXT_REQUIRED",\\n        )\\n        now = self.clock_ms()\\n        if now < self.cooldown_until:\\n            raise DemoRateLimited("Read-only admission cooldown active")\\n        evidence_require(\\n            self.synced_at is not None and 0 <= now - self.synced_at <= 5000,\\n            "ADMISSION_CLOCK_STALE",\\n        )\\n        signed = prepare_signed_query(\\n            {},\\n            api_key=self.credentials.api_key,\\n            secret=self.credentials.secret,\\n            timestamp_ms=now + self.offset,\\n        )\\n        return self._decode(\\n            self.send("GET", ORDER_USAGE_PATH, signed.query, signed.api_key), mutation=False\\n        )\\n\\n\\ndef review_admission(client: AdmissionReadClient, monitor: AdmissionReadMonitor) -> dict[str, Any]:\\n    client.sync_clock()\\n    info = client.public("/fapi/v1/exchangeInfo")\\n    monitor.enable(info)\\n    first_body = client.query_order_usage()\\n    first = OrderSample(monitor.clock(), first_body, monitor.last_order_headers.copy())\\n    second_body = client.query_order_usage()\\n    second = OrderSample(monitor.clock(), second_body, monitor.last_order_headers.copy())\\n    monitor.check()\\n    report = assess_order_admission(info, first, second, now=monitor.clock())\\n    report.update(\\n        {\\n            "tp_entry_admission_review": "READ_ONLY_EVIDENCE_COLLECTED_KEEP_PAUSED",\\n            "published_weight_limit_1m": monitor.weight_limit,\\n            "observed_max_used_weight_1m": monitor.peak_weight,\\n            "http_attempts": len(monitor.records),\\n            "signed_order_usage_queries": 2,\\n            "readiness_rechecked": False,\\n            "database_accessed": False,\\n            "database_changed": False,\\n            "leader_acquired": False,\\n            "order_writes": 0,\\n            "pause_modified": False,\\n            "runtime_installed": False,\\n            "orders_enabled": False,\\n            "live_trading_enabled": False,\\n            "ip_capacity_reserved": False,\\n            "production_activation_enabled": False,\\n            "remaining_approval_blockers": [\\n                "REQUEST_COST_CONTRACT_REVIEW_PENDING",\\n                "CROSS_PROCESS_PRESSURE_POLICY_REVIEW_PENDING",\\n                "PRODUCTION_DEFAULT_EXECUTION_DISABLED",\\n            ],\\n            "next_action": "REVIEW_EVIDENCE_NO_ORDER_OR_RUNTIME_SWITCH",\\n        }\\n    )\\n    return report\\n\\n\\ndef main() -> None:\\n    # Reject LIVE/mixed-key context before reading even a session credential.\\n    code = 2\\n    report: dict[str, Any] = {\\n        "tp_entry_admission_review": "BLOCKED_KEEP_PAUSED_NO_RETRY",\\n        "stop_reason": "CHECK_UNAVAILABLE",\\n        "database_accessed": False,\\n        "order_writes": 0,\\n        "execution_approved": False,\\n        "orders_enabled": False,\\n        "live_trading_enabled": False,\\n        "next_action": "KEEP_PAUSED_NO_TP_RETRY",\\n    }\\n    try:\\n        evidence_require(\\n            os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE"\\n            and os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"\\n            and not any(\\n                os.getenv(k)\\n                for k in (\\n                    "BINANCE_API_KEY",\\n                    "BINANCE_API_SECRET",\\n                    "BINANCE_LIVE_API_KEY",\\n                    "BINANCE_LIVE_API_SECRET",\\n                )\\n            ),\\n            "LIVE_OR_MIXED_CONTEXT_FORBIDDEN",\\n        )\\n        verify_installed_sources()\\n        import yaml\\n\\n        cfg = DemoSettings.model_validate(\\n            yaml.load(\\n                Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader\\n            )\\n        ).model_copy(update={"demo_trading_enabled": False})\\n        try:\\n            key = credentials(prompt=False)\\n        except Exception:\\n            raise EvidenceStop("DEMO_CREDENTIAL_SESSION_UNAVAILABLE") from None\\n        monitor = AdmissionReadMonitor(admission_read_transport)\\n        client = AdmissionReadClient(cfg, key, send=monitor)\\n        report = review_admission(client, monitor)\\n        code = 0 if not report["order_quota_blockers"] else 2\\n    except DemoRateLimited:\\n        report["stop_reason"] = "RATE_LIMITED_NO_RETRY"\\n    except (AdmissionEvidenceInvalid, EvidenceStop) as error:\\n        report["stop_reason"] = str(error) if str(error) in EVIDENCE_CODES else "CHECK_UNAVAILABLE"\\n    except DemoFailure:\\n        report["stop_reason"] = "DEMO_READ_QUERY_FAILED"\\n    except Exception:\\n        pass  # Never print network/SDK/credential bodies, URLs, queries or keys.\\n    print(json.dumps(report), flush=True)\\n    if code:\\n        raise SystemExit(code)\\n\\n\\nEVIDENCE_CODES = frozenset(\\n    {\\n        "ORDER_COUNTER_HEADER_INVALID",\\n        "ORDER_INTEGER_INVALID",\\n        "ORDER_WINDOW_INVALID",\\n        "ORDER_INTERVAL_UNSUPPORTED",\\n        "ORDER_LIMITS_UNAVAILABLE",\\n        "ORDER_WINDOW_DUPLICATE",\\n        "REQUIRED_ORDER_WINDOWS_MISSING",\\n        "ORDER_USAGE_UNAVAILABLE",\\n        "ORDER_SAMPLE_STALE_OR_CLOCK_INVALID",\\n        "ORDER_WINDOW_COVERAGE_MISMATCH",\\n        "PIN_COHORT_INVALID",\\n        "INSTALLED_SOURCE_MISMATCH",\\n        "DEMO_HOST_REQUIRED",\\n        "READ_SCOPE_FORBIDDEN",\\n        "SIGNED_READ_SCOPE_FORBIDDEN",\\n        "PUBLIC_READ_SCOPE_FORBIDDEN",\\n        "PROBE_CLOCK_OR_TIME_BUDGET",\\n        "RATE_LIMITED_NO_RETRY",\\n        "READ_WEIGHT_HEADROOM_INSUFFICIENT",\\n        "PROBE_HTTP_BUDGET",\\n        "LIMIT_DISCOVERY_REQUIRED",\\n        "WEIGHT_HEADER_UNAVAILABLE",\\n        "READ_ONLY_CONTEXT_REQUIRED",\\n        "GENERIC_SIGNED_REQUEST_FORBIDDEN",\\n        "LISTEN_KEY_FORBIDDEN",\\n        "ADMISSION_CLOCK_STALE",\\n        "LIVE_OR_MIXED_CONTEXT_FORBIDDEN",\\n        "DEMO_CREDENTIAL_SESSION_UNAVAILABLE",\\n    }\\n)\\n\\nif __name__ == "__main__":\\n    main()\\n\'\n\n\ndef readonly_token(engine):\n    with engine.connect() as conn, conn.begin():\n        conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")\n        conn.exec_driver_sql("SET LOCAL statement_timeout=\'3s\'")\n        row = (\n            conn.execute(select(accounts).where(accounts.c.account_id == ACCOUNT)).mappings().one()\n        )\n        history = (\n            conn.execute(\n                select(events).where(events.c.account_id == ACCOUNT).order_by(events.c.event_id)\n            )\n            .mappings()\n            .all()\n        )\n        messages = (\n            conn.execute(\n                select(outbox)\n                .join(events, outbox.c.event_id == events.c.event_id)\n                .where(events.c.account_id == ACCOUNT)\n                .order_by(outbox.c.event_id)\n            )\n            .mappings()\n            .all()\n        )\n        body = {\n            "account": dict(row),\n            "events": [dict(r) for r in history],\n            "outbox": [dict(r) for r in messages],\n        }\n        return hashlib.sha256(\n            json.dumps(body, sort_keys=True, default=lambda x: x.isoformat()).encode()\n        ).hexdigest()\n\n\ndef readonly_files(root):\n    import stat\n\n    result = {}\n    total = 0\n    for file in sorted(root.rglob("*")):\n        require(not file.is_symlink(), "REVIEW_STORAGE_SYMLINK")\n        if file.is_dir():\n            continue\n        info = file.stat()\n        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "REVIEW_STORAGE_FILE_INVALID")\n        total += info.st_size\n        require(\n            len(result) < 16 and info.st_size <= 16 * 1024 * 1024 and total <= 32 * 1024 * 1024,\n            "REVIEW_STORAGE_SIZE_LIMIT",\n        )\n        result[str(file.relative_to(root))] = hashlib.sha256(file.read_bytes()).hexdigest()\n    return result\n\n\ndef collect_exchange_evidence():\n    import sys\n    import types\n\n    require(\n        hashlib.sha256(PINNED_PROBE_SOURCE.encode()).hexdigest() == PINNED_PROBE_SHA256,\n        "REVIEW_PROBE_SOURCE_CHANGED",\n    )\n    name = "_pinned_runtime_admission_read_probe"\n    module = types.ModuleType(name)\n    sys.modules[name] = module\n    monitor = None\n    try:\n        exec(compile(PINNED_PROBE_SOURCE, "<pinned_admission_probe>", "exec"), module.__dict__)\n        import yaml\n\n        module.evidence_require(\n            not any(\n                os.getenv(k)\n                for k in (\n                    "BINANCE_API_KEY",\n                    "BINANCE_API_SECRET",\n                    "BINANCE_LIVE_API_KEY",\n                    "BINANCE_LIVE_API_SECRET",\n                )\n            ),\n            "LIVE_OR_MIXED_CONTEXT_FORBIDDEN",\n        )\n        module.verify_installed_sources()\n        cfg = module.DemoSettings.model_validate(\n            yaml.load(\n                Path("configs/demo-isolated.example.yaml").read_text(),\n                Loader=module.UniqueKeyLoader,\n            )\n        ).model_copy(update={"demo_trading_enabled": False})\n        try:\n            key = module.credentials(prompt=False)\n        except Exception:\n            return {\n                "collection_status": "DEMO_SESSION_UNAVAILABLE",\n                "stop_reason": "DEMO_CREDENTIAL_SESSION_UNAVAILABLE",\n                "http_attempts": 0,\n                "usage_counts_verified": False,\n            }\n        monitor = module.AdmissionReadMonitor(module.admission_read_transport)\n        client = module.AdmissionReadClient(cfg, key, send=monitor)\n        raw = module.review_admission(client, monitor)\n        fields = (\n            "order_quota_evidence",\n            "sample_age_ms",\n            "required_modeled_order_slots",\n            "order_plan",\n            "windows",\n            "order_quota_blockers",\n            "algo_trigger_counter_upper_bound_proven",\n            "published_weight_limit_1m",\n            "observed_max_used_weight_1m",\n            "signed_order_usage_queries",\n        )\n        report = {k: raw[k] for k in fields if k in raw}\n        report.update(\n            collection_status="READ_ONLY_EVIDENCE_COLLECTED",\n            http_attempts=len(monitor.records),\n            usage_counts_verified=not bool(raw["order_quota_blockers"]),\n            installed_controls_rechecked=True,\n            readiness_rechecked=False,\n            exposure_checked=False,\n            repeat_same_limits_only_probe_recommended=False,\n            execution_approved=False,\n        )\n        return report\n    except Exception as error:\n        reason = "DEMO_EVIDENCE_CHECK_FAILED"\n        if isinstance(error, getattr(module, "DemoRateLimited", ())):\n            reason = "RATE_LIMITED_NO_RETRY"\n        elif isinstance(\n            error,\n            tuple(\n                kind\n                for name in ("EvidenceStop", "AdmissionEvidenceInvalid")\n                if isinstance((kind := getattr(module, name, None)), type)\n            ),\n        ) and str(error) in getattr(module, "EVIDENCE_CODES", frozenset()):\n            reason = str(error)\n        return {\n            "collection_status": "BLOCKED_NO_RETRY",\n            "stop_reason": reason,\n            "http_attempts": len(monitor.records) if monitor is not None else 0,\n            "usage_counts_verified": False,\n        }\n    finally:\n        sys.modules.pop(name, None)\n\n\ndef review_controls(\n    store,\n    anchor,\n    *,\n    parent=STORAGE_PARENT,\n    clocks=None,\n    collect=False,\n    collector=collect_exchange_evidence,\n    progress=lambda stage: None,\n):\n    from integrations.binance_usdm.demo_tp_admission import TPModelProfile\n    from integrations.binance_usdm.demo_tp_weight_model import MODELED_TP_ROUTE_COSTS\n    from services.execution.demo_runtime_capacity import capacity_plan\n    from services.execution.demo_runtime_cost_contract import cost_contract_report\n    from services.execution.demo_runtime_storage import RuntimeClocks\n\n    clocks = clocks or RuntimeClocks()\n    progress("DATABASE_SNAPSHOT_BEFORE")\n    before = readonly_token(store.engine)\n    progress("STORAGE_SNAPSHOT_BEFORE")\n    root = _setup_root(parent)\n    files = readonly_files(root)\n    status = setup_status(\n        store, anchor, parent=parent, clocks=clocks, allow_stale_period=True, progress=progress\n    )\n    require(\n        status["runtime_storage_setup"] == "CONFIGURED_KEEP_PAUSED",\n        "REVIEW_STORAGE_CONFIGURATION_REQUIRED",\n    )\n    ledger = setup_context(store, allow_stale_period=True)\n    current_periods = period_keys(store.now(), ledger.policy.week_start_kst)\n    periods_match = current_periods == (ledger.daily_key, ledger.weekly_key)\n    progress("DEFAULT_EXECUTION_GUARD")\n    verify_default_refusal()\n    progress("DOCUMENTED_COST_CONTRACT")\n    contract = cost_contract_report(MODELED_TP_ROUTE_COSTS)\n    progress("MODELED_CAPACITY")\n    plan = capacity_plan(TPModelProfile(historical_orders=len(ledger.orders)))\n    progress("OPTIONAL_EXCHANGE_EVIDENCE")\n    evidence = (\n        collector()\n        if collect and periods_match\n        else {\n            "collection_status": (\n                "NOT_COLLECTED_PERIOD_MISMATCH"\n                if collect and not periods_match\n                else "NOT_REQUESTED"\n            ),\n            "http_attempts": 0,\n            "usage_counts_verified": False,\n        }\n    )\n    progress("DATABASE_SNAPSHOT_AFTER")\n    require(readonly_token(store.engine) == before, "REVIEW_DATABASE_CHANGED_DURING_READ")\n    progress("STORAGE_SNAPSHOT_AFTER")\n    require(readonly_files(root) == files, "REVIEW_STORAGE_CHANGED_DURING_READ")\n    require(\n        period_keys(store.now(), ledger.policy.week_start_kst) == current_periods,\n        "REVIEW_PERIOD_CHANGED_DURING_READ",\n    )\n    blockers = [] if periods_match else ["PERIOD_KEYS_MISMATCH"]\n    if evidence.get("usage_counts_verified") is not True:\n        blockers.append("ACCOUNT_ORDER_USAGE_NOT_VERIFIED")\n    if not plan["proof_fits_empty_window"] or not plan["entry_fits_empty_window"]:\n        blockers.append("MODELED_BATCH_DOES_NOT_FIT")\n    blockers += [\n        "VENUE_REQUEST_COST_UPPER_BOUNDS_UNPROVEN",\n        "SHARED_IP_CAPACITY_NOT_RESERVED",\n        "PRODUCTION_DEFAULT_EXECUTION_DISABLED",\n        "FRESH_EXCHANGE_EXPOSURE_READINESS_NOT_CHECKED",\n    ]\n    return {\n        "runtime_readiness_review": (\n            "LOCAL_CONTROLS_VERIFIED_KEEP_PAUSED"\n            if periods_match\n            else "LOCAL_CONTROLS_VERIFIED_PERIOD_MISMATCH_KEEP_PAUSED"\n        ),\n        "review_scope": "POST_INSTALL_LOCAL_CONTROLS_ONLY",\n        "trading_readiness_verified": False,\n        "period_keys_match": periods_match,\n        "periods": {\n            "stored_day_kst": ledger.daily_key,\n            "current_day_kst": current_periods[0],\n            "stored_week_kst": ledger.weekly_key,\n            "current_week_kst": current_periods[1],\n        },\n        "period_baselines_changed": False,\n        "installed_controls_verified": True,\n        "storage_configuration_verified": True,\n        "financial_receipts_verified": True,\n        "preserved_ledger_sha256": setup_preserved(ledger),\n        "database_changed_by_review": False,\n        "journal_changed_by_review": False,\n        "leader_acquired": False,\n        "session_started": False,\n        "day_kst": ledger.daily_key,\n        "week_kst": ledger.weekly_key,\n        "risk_state": ledger.risk_state.value,\n        "paused": ledger.paused,\n        "documented_cost_contract": contract,\n        "modeled_window_capacity": plan,\n        "exchange_order_evidence": evidence,\n        "http_attempts": evidence.get("http_attempts", 0),\n        "external_account_usage_verified": evidence.get("usage_counts_verified") is True,\n        "execution_blockers": blockers,\n        "execution_approved": False,\n        "orders_enabled": False,\n        "live_trading_enabled": False,\n        "order_writes": 0,\n        "runtime_installed_by_this_review": False,\n        "next_action": "REVIEW_REMAINING_EXECUTION_GATES_NO_REINSTALL_OR_ORDER",\n    }\n\n\nSAFE_FAILURE_CODES = frozenset(\n    [\n        "DAY_BOUNDARY_EVIDENCE_ONLY",\n        "DAY_EVIDENCE_BLOCKED",\n        "DAY_OPERATION_CONFLICT",\n        "DAY_PREVIEW_CHANGED",\n        "DEFAULT_EXECUTION_REFUSAL_CHANGED",\n        "DEFAULT_REAL_EXECUTION_DISABLED",\n        "EMPTY_ACCOUNT_OBSERVATION_WORKER_SIGKILL",\n        "EMPTY_ACCOUNT_SINGLE_WSS_SESSION",\n        "EMPTY_DB_RESTART_HISTORY_PRESERVED_VERIFIED",\n        "EMPTY_IDLE_DB_SERVER_RESTART",\n        "EMPTY_NORMAL_PAUSED_REQUIRED",\n        "EMPTY_PRIVATE_SESSION_STABILITY_RENEWAL_VERIFIED",\n        "EMPTY_PRIVATE_STABILITY_STARTED",\n        "EMPTY_WORKER_CHECKED_RELEASE_PENDING",\n        "EMPTY_WORKER_READY",\n        "EMPTY_WORKER_SIGKILL_FENCED_RECOVERY_VERIFIED",\n        "INSTALL_DEPENDENCY_MISSING",\n        "INSTALL_SOURCE_CONFLICT",\n        "PERIOD_BASELINE_MISSING",\n        "PERIOD_KEYS_MISMATCH",\n        "PERIOD_OPERATION_CONFLICT",\n        "PERIOD_OR_ANCHOR_WINDOW_UNSUPPORTED",\n        "PERIOD_PREVIEW_CHANGED",\n        "PERIOD_REVIEW_BLOCKED",\n        "PRIOR_PERIOD_RECEIPT_REQUIRED",\n        "QUOTA_ACCOUNT_INVALID",\n        "QUOTA_BOOTSTRAP_REQUIRES_PAUSED_FLAT",\n        "QUOTA_BRIDGE_DEGRADED",\n        "QUOTA_CLOCK_UNTRUSTED",\n        "QUOTA_COMMITTED_FLOW_REQUIRED",\n        "QUOTA_COMMITTED_INTENT_REQUIRED",\n        "QUOTA_CONTEXT_MISMATCH",\n        "QUOTA_EFFECT_ROLE_MISMATCH",\n        "QUOTA_EVENT_ALREADY_CONSUMED",\n        "QUOTA_EVENT_KIND_INVALID",\n        "QUOTA_EXIT_INTENT_REQUIRED",\n        "QUOTA_FENCED_EFFECT_REQUIRED",\n        "QUOTA_FILE_IDENTITY_INVALID",\n        "QUOTA_FILE_REPLACED",\n        "QUOTA_FLOW_ALREADY_EXISTS",\n        "QUOTA_FLOW_INVALID",\n        "QUOTA_FLOW_NOT_ACTIVE",\n        "QUOTA_FULL_PROOF_CHANGED",\n        "QUOTA_JOURNAL_CHAIN_INVALID",\n        "QUOTA_JOURNAL_CORRUPT",\n        "QUOTA_JOURNAL_MISSING",\n        "QUOTA_JOURNAL_PAYLOAD_INVALID",\n        "QUOTA_JOURNAL_SIZE_LIMIT",\n        "QUOTA_JOURNAL_UNAVAILABLE",\n        "QUOTA_LOCAL_CAPACITY_INSUFFICIENT",\n        "QUOTA_MANAGED_WRITER_BUSY",\n        "QUOTA_MONOTONIC_INVALID",\n        "QUOTA_ORDER_OWNERSHIP_INVALID",\n        "QUOTA_PG_JOURNAL_FLOW_MISMATCH",\n        "QUOTA_PG_JOURNAL_SEAL_MISMATCH",\n        "QUOTA_PRIOR_FLOW_UNRESOLVED",\n        "QUOTA_PRIVATE_DIRECTORY_REQUIRED",\n        "QUOTA_PROCESS_ATTEMPT_DUPLICATE",\n        "QUOTA_PROCESS_OWNERSHIP_INVALID",\n        "QUOTA_PROTECTION_INTENT_REQUIRED",\n        "QUOTA_PUBLISHED_LIMIT_TOO_LOW",\n        "QUOTA_READ_ONLY",\n        "QUOTA_READ_ONLY_CANNOT_CREATE",\n        "QUOTA_RECOVERY_EXIT_ONLY",\n        "QUOTA_RESTART_HOLD_ACTIVE",\n        "QUOTA_ROLE_SCOPE_INVALID",\n        "QUOTA_SESSION_INVALID",\n        "QUOTA_STORAGE_ALREADY_BOUND",\n        "QUOTA_STORAGE_BINDING_MISMATCH",\n        "QUOTA_UTC_CLOCK_INVALID",\n        "REVIEW_CLEANUP_FAILED",\n        "REVIEW_DATABASE_CHANGED_DURING_READ",\n        "REVIEW_LIVE_CONTEXT_FORBIDDEN",\n        "REVIEW_LOCAL_BLOCKERS_NO_RETRY_OR_RESUME",\n        "REVIEW_ONLY_NO_DATABASE_CHANGE",\n        "REVIEW_PERIOD_CHANGED_DURING_READ",\n        "REVIEW_PROBE_SOURCE_CHANGED",\n        "REVIEW_REMAINING_EXECUTION_GATES_NO_REINSTALL_OR_ORDER",\n        "REVIEW_STORAGE_CHANGED_DURING_READ",\n        "REVIEW_STORAGE_CONFIGURATION_REQUIRED",\n        "REVIEW_STORAGE_FILE_INVALID",\n        "REVIEW_STORAGE_SIZE_LIMIT",\n        "REVIEW_STORAGE_SYMLINK",\n        "REVIEW_TOKEN_CHANGED",\n        "REVIEW_UNAVAILABLE",\n        "RUNTIME_ASSEMBLY_CLEANUP_FAILED",\n        "RUNTIME_ASSEMBLY_FAILED",\n        "RUNTIME_BIND_DEMO_ACCOUNT_REQUIRED",\n        "RUNTIME_BIND_LIVE_CONTEXT_FORBIDDEN",\n        "RUNTIME_BIND_REQUIRES_PAUSED_FLAT",\n        "RUNTIME_CLIENT_SETTINGS_CHANGED",\n        "RUNTIME_CLIENT_WIRING_CHANGED",\n        "RUNTIME_CLOCK_REFRESH_FAILED",\n        "RUNTIME_CLOSED",\n        "RUNTIME_CONFIRMATION_CONTEXT_REQUIRED",\n        "RUNTIME_CONFIRMATION_ORDER_LOCK_REQUIRED",\n        "RUNTIME_CONFIRMATION_RUNNING",\n        "RUNTIME_CONFIRMATION_WIRING_CHANGED",\n        "RUNTIME_COST_CONTRACT_MODEL_MISMATCH",\n        "RUNTIME_COST_CONTRACT_QUERY_POLICY_MISMATCH",\n        "RUNTIME_CURRENT_LEASE_REQUIRED",\n        "RUNTIME_DEDICATED_ANCHOR_POOL_REQUIRED",\n        "RUNTIME_ENTRY_BATCH_CANNOT_FIT",\n        "RUNTIME_ISOLATED_DISABLED_CLIENT_REQUIRED",\n        "RUNTIME_LEDGER_SCOPE_MISMATCH",\n        "RUNTIME_MODEL_BINDING_CHANGED",\n        "RUNTIME_ORDER_LOCK_REQUIRED",\n        "RUNTIME_ORDER_STORAGE_NOT_BOUND",\n        "RUNTIME_PAUSED_FLAT_REQUIRED",\n        "RUNTIME_PENDING_ORDER_FLOW",\n        "RUNTIME_PENDING_SHARED_REQUESTS",\n        "RUNTIME_PERIOD_KEYS_MISMATCH",\n        "RUNTIME_PROOF_BATCH_CANNOT_FIT",\n        "RUNTIME_QUERY_CONTRACT_REJECTED",\n        "RUNTIME_REAL_EXECUTION_DISABLED",\n        "RUNTIME_SHARED_ANCHOR_REQUIRED",\n        "RUNTIME_SHARED_PROVIDER_CHANGED",\n        "RUNTIME_SHARED_STARTUP_HOLD",\n        "RUNTIME_SINGLE_USE_REQUIRED",\n        "RUNTIME_START_BUDGET_CHANGED",\n        "RUNTIME_START_CLIENT_CHANGED",\n        "RUNTIME_START_CONTEXT_CHANGED",\n        "RUNTIME_START_EXPOSURE_CHANGED",\n        "RUNTIME_START_HISTORY_CHANGED",\n        "RUNTIME_START_LEDGER_SCOPE_CHANGED",\n        "RUNTIME_START_RISK_CHANGED",\n        "RUNTIME_START_WIRING_CHANGED",\n        "RUNTIME_STILL_RUNNING",\n        "RUNTIME_STORAGE_BINDING_INVALID",\n        "RUNTIME_STORAGE_BINDING_MISMATCH",\n        "RUNTIME_SYNTHETIC_CONTEXT_REQUIRED",\n        "RUNTIME_TRANSPORT_BINDING_CHANGED",\n        "SETUP_ACTIVE_OWNER",\n        "SETUP_ANCHOR_CHANGED",\n        "SETUP_BINDING_CONFLICT",\n        "SETUP_COMPLETE_RECEIPT_INVALID",\n        "SETUP_COMPLETE_RECEIPT_REQUIRED",\n        "SETUP_CURRENT_LEASE_REQUIRED",\n        "SETUP_DAY_RECEIPT_REQUIRED",\n        "SETUP_INCOMPLETE_NO_AUTOMATIC_RETRY",\n        "SETUP_INTENT_INVALID",\n        "SETUP_INTENT_REQUIRED",\n        "SETUP_ORDER_FLOWS_NOT_EMPTY",\n        "SETUP_ORPHAN_DIRECTORY_NO_AUTOMATIC_RETRY",\n        "SETUP_PAUSED_NORMAL_REQUIRED",\n        "SETUP_PERIOD_MISMATCH",\n        "SETUP_PRIVATE_PARENT_REQUIRED",\n        "SETUP_PRIVATE_ROOT_REQUIRED",\n        "SETUP_SHARED_REQUESTS_NOT_EMPTY",\n        "SETUP_UNRELATED_BINDINGS_PRESENT",\n        "SHARED_ATTEMPT_NOT_PENDING",\n        "SHARED_CHECKPOINT_ALREADY_BOUND",\n        "SHARED_CHECKPOINT_BINDING_MISMATCH",\n        "SHARED_CHECKPOINT_HISTORY_INVALID",\n        "SHARED_CHECKPOINT_INSTALLATION_CHANGED",\n        "SHARED_CHECKPOINT_INSTALLATION_INVALID",\n        "SHARED_CHECKPOINT_MISMATCH",\n        "SHARED_CHECKPOINT_MISSING",\n        "SHARED_CHECKPOINT_POSTGRESQL_REQUIRED",\n        "SHARED_CHECKPOINT_WRITER_REQUIRED",\n        "SHARED_CLOCK_INVALID",\n        "SHARED_CLOCK_OR_BOOT_CHANGED",\n        "SHARED_FILE_IDENTITY_INVALID",\n        "SHARED_FILE_REPLACED",\n        "SHARED_IP_CAPACITY_NOT_RESERVED",\n        "SHARED_MANAGED_WEIGHT_V1",\n        "SHARED_PRESSURE_ADMISSION_BLOCKED",\n        "SHARED_PRESSURE_STOPPED",\n        "SHARED_PRIVATE_DIRECTORY_REQUIRED",\n        "SHARED_READ_ONLY",\n        "SHARED_READ_ONLY_CANNOT_CREATE",\n        "SHARED_REQUEST_SCOPE_INVALID",\n        "SHARED_SCOPE_INVALID",\n        "SHARED_STARTUP_HOLD",\n        "SHARED_STORAGE_BINDING_MISMATCH",\n        "SHARED_STORAGE_INVALID",\n        "SHARED_STORAGE_SIZE_LIMIT",\n        "SHARED_TRANSPORT_CONTEXT_REQUIRED",\n        "SHARED_WEIGHT_CAPACITY",\n        "SHARED_WEIGHT_POLICY_LIMIT_MISMATCH",\n        "SHARED_WRITER_BUSY",\n    ]\n)\n\n\ndef safe_failure(error):\n    from pydantic import ValidationError\n    from sqlalchemy.exc import NoResultFound, SQLAlchemyError\n\n    from integrations.binance_usdm.demo import DemoFailure\n\n    if isinstance(error, (Blocked, DemoFailure)) and str(error) in SAFE_FAILURE_CODES:\n        return str(error), "CONTROL_REFUSAL"\n    if isinstance(error, NoResultFound):\n        return "REVIEW_ACCOUNT_ROW_UNAVAILABLE", "DATABASE_STATE"\n    if isinstance(error, SQLAlchemyError):\n        return "REVIEW_DATABASE_READ_FAILED", "DATABASE_READ"\n    if isinstance(error, PermissionError):\n        return "REVIEW_FILESYSTEM_PERMISSION_DENIED", "FILESYSTEM_READ"\n    if isinstance(error, FileNotFoundError):\n        return "REVIEW_REQUIRED_FILE_MISSING", "FILESYSTEM_READ"\n    if isinstance(error, OSError):\n        return "REVIEW_FILESYSTEM_READ_FAILED", "FILESYSTEM_READ"\n    if isinstance(error, ValidationError):\n        return "REVIEW_STORED_STATE_INVALID", "STORED_STATE_VALIDATION"\n    if isinstance(error, (ValueError, TypeError, AttributeError, KeyError)):\n        return "REVIEW_STATE_OR_FORMAT_UNAVAILABLE", "STATE_OR_FORMAT"\n    return "REVIEW_UNEXPECTED_FAILURE", "UNEXPECTED_FAILURE"\n\n\ndef main():\n    import argparse\n\n    parser = argparse.ArgumentParser(description=__doc__)\n    parser.add_argument("--collect-exchange-evidence", action="store_true")\n    args = parser.parse_args()\n    engines = []\n    stage = ["CONTEXT_VALIDATION"]\n\n    def progress(value):\n        stage[0] = value\n\n    code = 2\n    report = {\n        "runtime_readiness_review": "BLOCKED_KEEP_PAUSED",\n        "stop_reason": "REVIEW_UNAVAILABLE",\n        "order_writes": 0,\n        "execution_approved": False,\n        "orders_enabled": False,\n        "live_trading_enabled": False,\n    }\n    try:\n        require(\n            os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"\n            and os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE",\n            "REVIEW_LIVE_CONTEXT_FORBIDDEN",\n        )\n        progress("SOURCE_COHORT_VALIDATION")\n        require(source_cohort_valid(INSTALLED_PINS), "INSTALLED_SOURCE_HASH_MISMATCH")\n        verify_install_files("post")\n        from packages.persistence.demo_shared_checkpoint import SharedWeightAnchorStore\n\n        progress("DATABASE_ENGINE_CREATION")\n        for _ in range(2):\n            engines.append(\n                create_engine(\n                    database_url().get_secret_value(), connect_args={"connect_timeout": 2}\n                )\n            )\n        report = review_controls(\n            DemoStore(engines[0]),\n            SharedWeightAnchorStore(engines[1]),\n            collect=args.collect_exchange_evidence,\n            progress=progress,\n        )\n        code = 0\n    except Exception as error:\n        reason, category = safe_failure(error)\n        report.update(\n            stop_reason=reason,\n            stage=stage[0],\n            failure_category=category,\n            automatic_repair_attempted=False,\n            next_action="REVIEW_FAILURE_CODE_NO_REINSTALL_OR_ORDER_RETRY",\n        )\n    finally:\n        failed = False\n        for engine in reversed(engines):\n            try:\n                engine.dispose()\n            except Exception:\n                failed = True\n        if failed:\n            report = {\n                "runtime_readiness_review": "BLOCKED_KEEP_PAUSED",\n                "stop_reason": "REVIEW_CLEANUP_FAILED",\n                "order_writes": 0,\n                "execution_approved": False,\n                "orders_enabled": False,\n                "live_trading_enabled": False,\n            }\n            code = 2\n    print(json.dumps(report), flush=True)\n    raise SystemExit(code)\n\n\nif __name__ == "__main__":\n    main()\n'


def load_storage_review():
    import sys
    import types

    require(
        hashlib.sha256(REVIEW_SOURCE.encode()).hexdigest() == REVIEW_SHA256,
        "STORAGE_REVIEW_SOURCE_CHANGED",
    )
    module = types.ModuleType("_pinned_storage_period_review")
    sys.modules[module.__name__] = module
    exec(compile(REVIEW_SOURCE, "<pinned_storage_period_review>", "exec"), module.__dict__)
    return module


r = load_storage_review()


def verify_previous_day_and_setup(ledger):
    # Validate the exact November 11 financial receipt chain on a copy, excluding
    # only the four known storage bindings from the older receipt's hash domain.
    prior = ledger.model_copy(deep=True)
    for key in r.SETUP_KEYS:
        prior.operations.pop(key, None)
    require(r.day_recorded(prior), "PRIOR_DAY_RECEIPT_INVALID")
    record = r._read_setup(ledger)
    r._verify_done(ledger, record)
    return True


def checkpoint_token(engine):
    from sqlalchemy import text

    from packages.persistence.demo_shared_checkpoint import bindings, checkpoints, installations

    with engine.connect() as c, c.begin():
        c.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        c.execute(text("SET LOCAL statement_timeout='2s'"))
        rows = [
            list(c.execute(select(t).order_by(*t.primary_key.columns)).mappings())
            for t in (installations, bindings, checkpoints)
        ]
        return digest([[dict(row) for row in group] for group in rows])


def storage_preview(
    store,
    anchor,
    client,
    monitor,
    *,
    attest=False,
    attested_day=None,
    parent=r.STORAGE_PARENT,
    clocks=None,
    progress=lambda stage: None,
):
    require(attest is True, "OPERATOR_ATTESTATION_REQUIRED")
    require(attested_day == CURRENT_ATTESTED_DAY, "CURRENT_DAY_ATTESTATION_REQUIRED")
    progress("LOCAL_STORAGE_AND_RECEIPTS")
    financial_before = r.readonly_token(store.engine)
    root = r._setup_root(parent)
    files_before = r.readonly_files(root)
    anchor_before = checkpoint_token(anchor.engine)
    state = r.setup_status(store, anchor, parent=parent, clocks=clocks, allow_stale_period=True)
    require(
        state["runtime_storage_setup"] == "CONFIGURED_KEEP_PAUSED"
        and state["startup_ready"] is True,
        "STORAGE_NOT_CONFIGURED_OR_STARTUP_HOLD",
    )
    progress("SIGNED_CURRENT_STATE_AND_HISTORY")
    result = preview(store, client, monitor, attest=attest, attested_day=attested_day)
    progress("READ_ONLY_STORAGE_POSTCHECK")
    require(financial_before == r.readonly_token(store.engine), "LOCAL_STATE_CHANGED")
    require(files_before == r.readonly_files(root), "STORAGE_CHANGED_DURING_PREVIEW")
    require(anchor_before == checkpoint_token(anchor.engine), "CHECKPOINT_CHANGED_DURING_PREVIEW")
    final = r.setup_status(store, anchor, parent=parent, clocks=clocks, allow_stale_period=True)
    require(
        final["runtime_storage_setup"] == "CONFIGURED_KEEP_PAUSED"
        and final["startup_ready"] is True,
        "STORAGE_NOT_CONFIGURED_OR_STARTUP_HOLD",
    )
    result.update(
        prior_attested_day_operation_id=r.DAY_KEY,
        prior_attested_day_receipt_verified=True,
        storage_configuration_verified=True,
        storage_journals_preserved=True,
        shared_checkpoint_history_preserved=True,
        storage_receipts_preserved=True,
        clock_recovery_files_preserved=True,
        storage_changed_by_preview=False,
        checkpoint_changed_by_preview=False,
        execution_approved=False,
        next_action="REVIEW_DAY12_VALUES_AND_STORAGE_RECEIPT_CHAIN_NO_APPLY_OR_RESUME",
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--attest-no-activity-since-manual-close", action="store_true", required=True
    )
    parser.add_argument("--attested-day-kst", required=True, choices=[CURRENT_ATTESTED_DAY])
    args = parser.parse_args()
    engines = []
    route = None
    code = 2
    stage = ["CONTEXT_VALIDATION"]

    def progress(value):
        stage[0] = value

    report = blocked("CHECK_UNAVAILABLE")
    try:
        require(
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"
            and os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE",
            "LIVE_CONTEXT_FORBIDDEN",
        )
        progress("SOURCE_VALIDATION")
        verify_sources()
        r.verify_install_files("post")
        from packages.persistence.demo_shared_checkpoint import SharedWeightAnchorStore

        cfg = DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
            )
        ).model_copy(update={"demo_trading_enabled": False})
        for _ in range(2):
            engines.append(
                create_engine(
                    database_url().get_secret_value(), connect_args={"connect_timeout": 2}
                )
            )
        store = DemoStore(engines[0])
        anchor = SharedWeightAnchorStore(engines[1])
        progress("LOCAL_STORAGE_PRECHECK")
        ledger = local(store)[-1]
        require(
            period_keys(store.now(), ledger.policy.week_start_kst)[0] == CURRENT_ATTESTED_DAY,
            "CURRENT_DAY_ATTESTATION_REQUIRED",
        )
        r.setup_status(store, anchor, allow_stale_period=True)
        progress("DEMO_SESSION_READ")
        try:
            key = credentials(prompt=False)
        except Exception:
            raise Blocked("DEMO_CREDENTIAL_SESSION_UNAVAILABLE") from None
        route = TPReadRouting(transport)
        monitor = Monitor(route)
        client = DayPreviewClient(cfg, key, send=monitor)
        monitor.server_ms = lambda: client.clock_ms() + client.offset
        report = storage_preview(
            store,
            anchor,
            client,
            monitor,
            attest=args.attest_no_activity_since_manual_close,
            attested_day=args.attested_day_kst,
            progress=progress,
        )
        code = 0
    except Exception as error:
        reason = safe_reason(error)
        if reason == "CHECK_UNAVAILABLE":
            reason = r.safe_failure(error)[0]
        report = blocked(reason)
        report["stage"] = stage[0]
    finally:
        failed = False
        if route is not None:
            try:
                route.close()
            except Exception:
                failed = True
        for engine in reversed(engines):
            try:
                engine.dispose()
            except Exception:
                failed = True
        if failed:
            report = blocked("RESOURCE_CLEANUP_UNVERIFIED")
            code = 2
    print(json.dumps(report), flush=True)
    raise SystemExit(code)


STOP_CODES = STOP_CODES | frozenset(
    {
        "STORAGE_REVIEW_SOURCE_CHANGED",
        "STORAGE_NOT_CONFIGURED_OR_STARTUP_HOLD",
        "STORAGE_CHANGED_DURING_PREVIEW",
        "CHECKPOINT_CHANGED_DURING_PREVIEW",
    }
)

if __name__ == "__main__":
    main()
