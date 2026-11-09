"""Paused, flat read-only pre/post installation verification; never resume or change DB."""

import argparse
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta
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
from integrations.binance_usdm.demo_snapshot import operational_snapshot, signed_number
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import DemoStore, accounts
from scripts.demo_key_session import credentials
from services.execution.demo_empty_db_restart import empty
from services.execution.demo_models import DemoLedger
from services.execution.demo_recovery import local_period_status
from services.execution.models import PaperOrder

ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"
INCIDENT = "041d1f35-870e-46cb-aad2-ed6059bc1095"
RECEIPT = str(uuid5(UUID(INCIDENT), "signed-operator-manual-close-v1"))
MAX_SECONDS = 75.0
MAX_HTTP_ATTEMPTS = 64
MAX_CLOCK_ATTEMPTS = 6
OLD_PINS = {
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
    "scripts/check_demo_session_ready.py": "18259376c0404f98bc0cb91afeb31e33789f581562e14939989816c2fa8754d9",
    "services/execution/readiness_closed_history.py": "0c9cb90bc74402fdaed025f715a98a2e47169eee4d1f9990c75e8f5671e358da",
    "services/execution/demo_preview.py": "58ba6376f61f8467d2b3c9e75923444105f5fe610f9bde4455a94b5fb5d6f267",
    "services/execution/demo_api_restart_check.py": "b96ca788094995dafb7a69d9bbbd7f6a6dd16687d30bc9bd4271164c307ab239",
    "services/execution/demo_empty_worker_recovery.py": "8e3cac8da89fb80ef9f3654ae8591ffea7be1db1c7dc60070da72b72851b2070",
    "services/execution/demo_private_stability.py": "d3aee78f2da73c15c3eff5f89403fae666de5109ad3c2402585f2150df1a5123",
    "scripts/check_demo_disconnect.py": "c85c013412053237eda7ed82a8948aa4b50f9da1951d173360a1ecfb46228a1e",
    "alembic.ini": "e7955a0ffb9c32a9c5067d7885ee9197328f17a4a12fb4a5bb556c2b85763aa2",
    "pyproject.toml": "54760a9fa52fc4cc877d2302c7952688ddd13e12f1efc8e44a0974aae8914019",
    "uv.lock": "49bbe38dc3f559466c4666febe686926db6503413a45bd766088ed2454e01097",
    "migrations/env.py": "4256345fc24dd52ba706aa9b712268b9df436d52bafa7238234272d754734086",
    "migrations/script.py.mako": "67042994cfc495007c508ccae15e44d21057d90222461630f9bc04d3657f5f6d",
    "migrations/versions/0001_public_market_data.py": "b62afcb18d6d6c42d710ca0cb0eb42001e174934f11678443dc5ac24d86932f7",
    "migrations/versions/0002_paper.py": "91ffad2a439b9f47e2636939a5aba091490942db7a87bb0d87124a531870c77d",
    "migrations/versions/0003_demo.py": "6b2a26ba34855a3dee6d3b2c67eb9b3ecac644b4e2e31ad6353e907a3c233762",
    "migrations/versions/0004_demo_urgent_alerts.py": "da9e4de16f1f9d4c15d70293d3b252084648fbbcb060f8ec7740b9a85f83e8ad",
}
NEW_PINS = {
    "configs/demo-isolated.example.yaml": "d93e85c2e056ffbbc3dac5fab9f0e5bf28032fd16f41bbff1f3ae81fb00882ca",
    "integrations/binance_usdm/demo.py": "767d3a29b59882035bdfbf61d3271b39bfece0bff4b359e636db62ad9a5e84eb",
    "integrations/binance_usdm/demo_rate_limits.py": "d72af60f10a74baea689e11ef8f11b5003f2ab9f89e539290c45b93f2f3d4a4e",
    "integrations/binance_usdm/demo_read_routing.py": "a6e96b21135882a272cb5c97f86ddc14c3a5f4fa79dd115e40397252c679a66d",
    "integrations/binance_usdm/demo_snapshot.py": "f80f6e51505ab1095c25b6ee76af01c258cff911aabd02c201bfcddc636f2258",
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
    "scripts/check_demo_session_ready.py": "18259376c0404f98bc0cb91afeb31e33789f581562e14939989816c2fa8754d9",
    "services/execution/readiness_closed_history.py": "0c9cb90bc74402fdaed025f715a98a2e47169eee4d1f9990c75e8f5671e358da",
    "services/execution/demo_preview.py": "58ba6376f61f8467d2b3c9e75923444105f5fe610f9bde4455a94b5fb5d6f267",
    "services/execution/demo_api_restart_check.py": "b96ca788094995dafb7a69d9bbbd7f6a6dd16687d30bc9bd4271164c307ab239",
    "services/execution/demo_empty_worker_recovery.py": "8e3cac8da89fb80ef9f3654ae8591ffea7be1db1c7dc60070da72b72851b2070",
    "services/execution/demo_private_stability.py": "d3aee78f2da73c15c3eff5f89403fae666de5109ad3c2402585f2150df1a5123",
    "scripts/check_demo_disconnect.py": "c85c013412053237eda7ed82a8948aa4b50f9da1951d173360a1ecfb46228a1e",
    "alembic.ini": "e7955a0ffb9c32a9c5067d7885ee9197328f17a4a12fb4a5bb556c2b85763aa2",
    "pyproject.toml": "54760a9fa52fc4cc877d2302c7952688ddd13e12f1efc8e44a0974aae8914019",
    "uv.lock": "49bbe38dc3f559466c4666febe686926db6503413a45bd766088ed2454e01097",
    "migrations/env.py": "4256345fc24dd52ba706aa9b712268b9df436d52bafa7238234272d754734086",
    "migrations/script.py.mako": "67042994cfc495007c508ccae15e44d21057d90222461630f9bc04d3657f5f6d",
    "migrations/versions/0001_public_market_data.py": "b62afcb18d6d6c42d710ca0cb0eb42001e174934f11678443dc5ac24d86932f7",
    "migrations/versions/0002_paper.py": "91ffad2a439b9f47e2636939a5aba091490942db7a87bb0d87124a531870c77d",
    "migrations/versions/0003_demo.py": "6b2a26ba34855a3dee6d3b2c67eb9b3ecac644b4e2e31ad6353e907a3c233762",
    "migrations/versions/0004_demo_urgent_alerts.py": "da9e4de16f1f9d4c15d70293d3b252084648fbbcb060f8ec7740b9a85f83e8ad",
}
READ_PATHS = frozenset(
    {
        "/fapi/v1/positionSide/dual",
        "/fapi/v1/order",
        "/fapi/v1/exchangeInfo",
        "/fapi/v1/time",
        "/fapi/v1/multiAssetsMargin",
        "/fapi/v1/accountConfig",
        "/fapi/v3/account",
        "/fapi/v1/symbolConfig",
        "/fapi/v3/positionRisk",
        "/fapi/v1/openOrders",
        "/fapi/v1/algoOrder",
        "/fapi/v1/income",
        "/fapi/v1/openAlgoOrders",
    }
)


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


def receipt_bound(ledger, now):
    """Bind the trusted adoption receipt to all unchanged original order fields."""
    current = ledger.orders[INCIDENT]
    raw = json.loads(ledger.operations[RECEIPT])
    original = PaperOrder.model_validate(raw.get("original_order"))
    allowed = {"open_quantity", "actual_risk", "protected", "position_state"}
    before, after = original.model_dump(mode="json"), current.model_dump(mode="json")
    require(
        all(before[k] == after[k] for k in before.keys() - allowed),
        "RECEIPT_ORDER_BINDING_MISMATCH",
    )
    require(
        original.state.value == "FILLED"
        and original.position_state.value == "OPEN_UNPROTECTED"
        and original.open_quantity == original.quantity == original.filled == Decimal("0.0007")
        and not original.remaining
        and not original.protected
        and original.exit_id is None
        and original.exit_state == "NONE",
        "RECEIPT_ORIGINAL_STATE_UNSUPPORTED",
    )
    require(
        current.algo_id
        and current.tp_algo_id
        and current.algo_id != current.tp_algo_id
        and current.signal.symbol == "BTCUSDT"
        and current.signal.direction == "LONG"
        and current.signal.purpose == "DEMO_VALIDATION",
        "RECEIPT_SCOPE_MISMATCH",
    )
    require(
        raw.get("manual_close_client_id", "").startswith("web_")
        and raw["manual_close_client_id"] not in ledger.requests
        and all(
            isinstance(raw.get(k), str) and re.fullmatch("[0-9a-f]{64}", raw[k])
            for k in ("exchange_fingerprint", "prior_ledger_sha256")
        ),
        "RECEIPT_PROVENANCE_MISMATCH",
    )
    stamp = datetime.fromisoformat(raw["acknowledged_at_utc"])
    require(
        stamp.tzinfo is not None
        and stamp.utcoffset() == timedelta(0)
        and ledger.value.as_of <= stamp <= now,
        "RECEIPT_TIME_UNSUPPORTED",
    )
    return current


class ReadClient(DemoClient):
    def bind(self, ledger):
        self.start_ms = ledger.income_since_ms
        self.order_ids = {o.client_id for o in ledger.orders.values()} | {
            o.exit_id for o in ledger.orders.values() if o.exit_id
        }
        self.algo_ids = {x for o in ledger.orders.values() for x in (o.algo_id, o.tp_algo_id) if x}
        self.executions = set()

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
                p.get("symbol") == "BTCUSDT"
                and (
                    set(p) == {"symbol", "origClientOrderId"}
                    and p["origClientOrderId"] in self.order_ids
                    or set(p) == {"symbol", "orderId"}
                    and p["orderId"] in self.executions
                ),
                "ORDER_SCOPE_FORBIDDEN",
            )
        elif path == "/fapi/v1/algoOrder":
            require(
                set(p) == {"clientAlgoId"} and p["clientAlgoId"] in self.algo_ids,
                "ALGO_SCOPE_FORBIDDEN",
            )
        elif path == "/fapi/v1/income":
            require(
                set(p) == {"startTime", "endTime", "limit", "page"}
                and p["startTime"] == str(self.start_ms)
                and p["limit"] == "1000"
                and p["page"] in {str(i) for i in range(1, 11)},
                "INCOME_SCOPE_FORBIDDEN",
            )
            end = int(p["endTime"])
            require(
                self.start_ms <= end <= self.clock_ms() + self.offset + 1000,
                "INCOME_WINDOW_FORBIDDEN",
            )
        else:
            require(not p, "UNEXPECTED_QUERY_PARAMETERS")
        result = super().request(method, path, p)
        if (
            path == "/fapi/v1/algoOrder"
            and isinstance(result, dict)
            and result.get("algoStatus") == "FINISHED"
        ):
            execution = str(result.get("actualOrderId", ""))
            if execution.isascii() and execution.isdigit() and int(execution) > 0:
                self.executions.add(execution)
        return result

    def listen_key(self, *args, **kwargs):
        raise Blocked("LISTEN_KEY_FORBIDDEN")


def require_empty(remote):
    require(
        not any(remote.positions.values())
        and not remote.margin_used
        and not any(r["status"] in {"NEW", "PARTIALLY_FILLED"} for r in remote.orders.values())
        and not any(r["algoStatus"] == "NEW" for r in remote.algos.values()),
        "EXCHANGE_NOT_EMPTY",
    )


def same_fields(a, b, *, ignore):
    return all(getattr(a, k) == getattr(b, k) for k in type(a).model_fields if k not in ignore)


def baseline_terminal(ledger, remote):
    o = ledger.orders[INCIDENT]
    entry = remote.orders.get(o.client_id, {})
    require(
        entry.get("status") == "FILLED"
        and entry.get("symbol") == "BTCUSDT"
        and entry.get("clientOrderId") == o.client_id
        and entry.get("side") == "BUY"
        and entry.get("type") == "LIMIT"
        and entry.get("positionSide") == "BOTH"
        and entry.get("reduceOnly") == "false"
        and signed_number(entry.get("origQty")) == o.quantity
        and signed_number(entry.get("price")) == o.signal.suggested_limit_price
        and signed_number(entry.get("avgPrice")) == o.average_price
        and entry.get("orderId") == str(o.server_order_id)
        and signed_number(entry.get("executedQty")) == o.filled,
        "ENTRY_TERMINAL_UNVERIFIED",
    )
    for identifier, kind, trigger in [
        (o.algo_id, "STOP_MARKET", o.signal.initial_stop_price),
        (o.tp_algo_id, "TAKE_PROFIT_MARKET", o.signal.target_price),
    ]:
        r = remote.algos.get(identifier, {})
        require(
            r.get("algoStatus") in {"CANCELED", "EXPIRED"}
            and r.get("clientAlgoId") == identifier
            and r.get("orderType") == kind
            and r.get("symbol") == "BTCUSDT"
            and r.get("side") == "SELL"
            and r.get("positionSide") == "BOTH"
            and r.get("closePosition") == "true"
            and r.get("workingType") == "MARK_PRICE"
            and r.get("priceProtect") == "false"
            and r.get("algoType") == "CONDITIONAL"
            and signed_number(r.get("triggerPrice")) == trigger,
            "ORIGINAL_PROTECTION_NOT_TERMINAL",
        )


def verify_sources(phase, observations=None):
    require(phase in {"pre", "post"}, "INVALID_PHASE")
    expected = OLD_PINS if phase == "pre" else NEW_PINS
    actual = (
        observations
        if observations is not None
        else {
            n: hashlib.sha256(Path(n).read_bytes()).hexdigest() if Path(n).is_file() else None
            for n in expected
        }
    )
    require(actual == expected, "INSTALLED_SOURCE_HASH_MISMATCH")


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            default=lambda x: x.model_dump(mode="json") if isinstance(x, DemoLedger) else str(x),
        ).encode()
    ).hexdigest()


def valid_post_binding(account_digest, remote_digest, pre_reads):
    return (
        all(
            isinstance(x, str) and re.fullmatch("[0-9a-f]{64}", x)
            for x in (account_digest, remote_digest)
        )
        and type(pre_reads) is int
        and 2 < pre_reads <= MAX_HTTP_ATTEMPTS
        and pre_reads % 2 == 0
    )


def compare(
    store, client, monitor, phase, expected_account=None, expected_remote=None, pre_reads=None
):
    require(phase in {"pre", "post"}, "INVALID_PHASE")
    binding_supplied = any(x is not None for x in (expected_account, expected_remote, pre_reads))
    if phase == "post" or binding_supplied:
        require(
            valid_post_binding(expected_account, expected_remote, pre_reads),
            "PRECHECK_BINDING_REQUIRED",
        )
    before = local(store)
    ledger = before[-1]
    account_digest = digest(before)
    if phase == "post" or binding_supplied:
        require(account_digest == expected_account, "PRE_POST_ACCOUNT_CHANGED")
    require(
        not client.settings.demo_trading_enabled
        and client.settings.policy == ledger.policy
        and client.settings.symbols == ledger.symbols == ("BTCUSDT",)
        and client.settings.margin_mode == ledger.margin_mode == "ISOLATED",
        "READ_ONLY_CONTEXT_REQUIRED",
    )
    periods = local_period_status(store, ledger)
    # A date mismatch is reported, never repaired and never treated as trading readiness.
    # This gate authorizes only verification of a paused, exchange-flat code installation.
    receipt_bound(ledger, store.now())
    client.bind(ledger)
    try:
        client.sync_clock()
        monitor.enable(published_limit(client.public("/fapi/v1/exchangeInfo")))
        pair = []
        start = len(monitor.records)
        for _ in range(2):
            remote = operational_snapshot(client, ledger, store.now())
            require_empty(remote)
            baseline_terminal(ledger, remote)
            require(
                remote.transfers == ledger.income_seen
                and remote.leverage.get("BTCUSDT") == 2
                and remote.margin_types.get("BTCUSDT") == "ISOLATED"
                and remote.equity > 0,
                "TRANSFER_OR_SETTINGS_CHANGED",
            )
            pair.append(remote)
        require(same_fields(pair[0], pair[1], ignore={"as_of"}), "SNAPSHOTS_CHANGED")
        records = monitor.records[start:]
        signed_reads = sum(r["signed"] for r in records)
        old_modes = sum(
            r["path"] in {"/fapi/v1/positionSide/dual", "/fapi/v1/multiAssetsMargin"}
            for r in records
        )
        new_modes = sum(r["path"] == "/fapi/v1/accountConfig" for r in records)
        require(
            (old_modes, new_modes) == ((4, 0) if phase == "pre" else (0, 2)),
            "INSTALLED_MODE_ROUTE_UNVERIFIED",
        )
        remote_digest = digest(pair[-1].model_dump(mode="json", exclude={"as_of"}))
        if phase == "post" or binding_supplied:
            require(remote_digest == expected_remote, "PRE_POST_EXCHANGE_CHANGED")
            require(
                signed_reads == pre_reads - (2 if phase == "post" else 0),
                "EXPECTED_TWO_READ_REDUCTION_UNVERIFIED",
            )
        monitor.check()
        require(local_period_status(store, ledger) == periods, "PERIOD_CHANGED_DURING_CHECK")
        return {
            "account_config_install": "PRECHECKED_PAUSED_FLAT_INSTALL_ONLY"
            if phase == "pre"
            else "POSTCHECKED_PAUSED_FLAT_INSTALL_ONLY",
            "phase": phase,
            "account_digest": account_digest,
            "exchange_digest": remote_digest,
            "pair_signed_reads": signed_reads,
            "old_mode_reads": old_modes,
            "account_config_reads": new_modes,
            "pair_signed_reads_removed": 2 if phase == "post" else 0,
            "financial_history_and_transfer_fields_preserved": phase == "post",
            "manual_close_receipt_preserved": True,
            "failed_trial_preserved": True,
            "remote_empty_verified": True,
            "database_changed": False,
            "leader_acquired": False,
            "risk_state": ledger.risk_state.value,
            "paused": ledger.paused,
            "period_keys_match": periods["period_keys_match"],
            "periods": periods["periods"],
            "period_baselines_changed": False,
            "trading_readiness_checked": False,
            "active_tp_budget_admission_proven": False,
            "order_writes": 0,
            "orders_enabled": False,
            "live_trading_enabled": False,
            "http_attempts": len(monitor.records),
            "installation_postcheck_passed": phase == "post",
            "next_action": "KEEP_PAUSED_NO_TP_RETRY",
        }
    finally:
        require(local(store) == before, "LOCAL_STATE_CHANGED")


def blocked(reason):
    return {
        "account_config_install": "BLOCKED_KEEP_PAUSED_NO_RETRY",
        "stop_reason": reason,
        "database_changed_by_guard": False,
        "order_writes": 0,
        "orders_enabled": False,
        "live_trading_enabled": False,
        "installation_verified": False,
        "next_action": "KEEP_PAUSED_NO_TP_RETRY",
    }


def safe_reason(error):
    if isinstance(error, DemoRateLimited):
        return "RATE_LIMITED_NO_RETRY"
    if isinstance(error, (Blocked, ProbeStop)) and str(error) in STOP_CODES:
        return str(error)
    return "CHECK_UNAVAILABLE"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("pre", "post"), required=True)
    parser.add_argument("--expected-account")
    parser.add_argument("--expected-remote")
    parser.add_argument("--pre-reads", type=int)
    args = parser.parse_args()
    binding_supplied = any(
        x is not None for x in (args.expected_account, args.expected_remote, args.pre_reads)
    )
    if (args.phase == "post" or binding_supplied) and not valid_post_binding(
        args.expected_account, args.expected_remote, args.pre_reads
    ):
        print(json.dumps(blocked("PRECHECK_BINDING_REQUIRED")), flush=True)
        raise SystemExit(2)
    engine = route = None
    code = 2
    report = blocked("CHECK_UNAVAILABLE")
    try:
        require(
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"
            and os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE",
            "LIVE_CONTEXT_FORBIDDEN",
        )
        verify_sources(args.phase)
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
        client = ReadClient(cfg, key, send=monitor)
        monitor.server_ms = lambda: client.clock_ms() + client.offset
        report = compare(
            store,
            client,
            monitor,
            args.phase,
            args.expected_account,
            args.expected_remote,
            args.pre_reads,
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
        "ACTIVE_OWNER_BLOCKED",
        "ALGO_SCOPE_FORBIDDEN",
        "CHECK_UNAVAILABLE",
        "CLOCK_BUDGET_REACHED",
        "DEMO_CREDENTIAL_SESSION_UNAVAILABLE",
        "ENTRY_TERMINAL_UNVERIFIED",
        "EXCHANGE_NOT_EMPTY",
        "EXPECTED_TWO_READ_REDUCTION_UNVERIFIED",
        "FAILED_TRIAL_MARKER_CHANGED",
        "HTTP_BUDGET_REACHED",
        "INCOME_SCOPE_FORBIDDEN",
        "INCOME_WINDOW_FORBIDDEN",
        "INITIALIZED_LEDGER_REQUIRED",
        "INSTALLED_MODE_ROUTE_UNVERIFIED",
        "INSTALLED_SOURCE_HASH_MISMATCH",
        "INVALID_PHASE",
        "LIMIT_DISCOVERY_REQUIRED",
        "LISTEN_KEY_FORBIDDEN",
        "LIVE_CONTEXT_FORBIDDEN",
        "LOCAL_STATE_CHANGED",
        "MANUAL_CLOSE_LOCAL_SCOPE_MISMATCH",
        "MANUAL_CLOSE_RECEIPT_MISMATCH",
        "MUTATION_OR_ROUTE_FORBIDDEN",
        "NON_READ_OR_UNKNOWN_ROUTE_FORBIDDEN",
        "ORDER_SCOPE_FORBIDDEN",
        "ORIGINAL_PROTECTION_NOT_TERMINAL",
        "PERIOD_CHANGED_DURING_CHECK",
        "PRECHECK_BINDING_REQUIRED",
        "PRE_POST_ACCOUNT_CHANGED",
        "PRE_POST_EXCHANGE_CHANGED",
        "PUBLIC_SCOPE_FORBIDDEN",
        "PUBLISHED_LIMIT_AMBIGUOUS_OR_INVALID",
        "PUBLISHED_LIMIT_INVALID",
        "PUBLISHED_LIMIT_UNAVAILABLE",
        "RATE_LIMIT_ALREADY_OBSERVED",
        "READ_ONLY_CONTEXT_REQUIRED",
        "RECEIPT_ORDER_BINDING_MISMATCH",
        "RECEIPT_ORIGINAL_STATE_UNSUPPORTED",
        "RECEIPT_PROVENANCE_MISMATCH",
        "RECEIPT_SCOPE_MISMATCH",
        "RECEIPT_TIME_UNSUPPORTED",
        "RESOURCE_CLEANUP_UNVERIFIED",
        "SNAPSHOTS_CHANGED",
        "TIME_BUDGET_REACHED",
        "TRANSFER_OR_SETTINGS_CHANGED",
        "UNEXPECTED_QUERY_PARAMETERS",
        "WEIGHT_HEADER_UNAVAILABLE",
        "WEIGHT_HEADROOM_THRESHOLD",
    ]
)
if __name__ == "__main__":
    main()
