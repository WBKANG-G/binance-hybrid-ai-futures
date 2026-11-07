"""Read-only account-mode getter comparison. No DB, orders or runtime switch."""

import hashlib
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

import yaml
from pydantic import SecretStr

from integrations.binance_usdm.demo import (
    REST_HOST,
    DemoClient,
    DemoFailure,
    DemoRateLimited,
    DemoSettings,
    DemoUnknown,
)
from integrations.binance_usdm.demo_rate_limits import rate_headers
from integrations.binance_usdm.rest import NoRedirect, RESTResponse, prepare_signed_query
from packages.domain.config import UniqueKeyLoader
from scripts.demo_key_session import credentials

NEW = "/fapi/v1/accountConfig"
OLD_MODE = "/fapi/v1/positionSide/dual"
OLD_ASSETS = "/fapi/v1/multiAssetsMargin"
READ_PATHS = frozenset({"/fapi/v1/time", "/fapi/v1/exchangeInfo", OLD_MODE, OLD_ASSETS, NEW})
MAX_SECONDS = 30.0
MAX_HTTP_ATTEMPTS = 16
MAX_CLOCK_ATTEMPTS = 3

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
    "scripts/check_demo_session_ready.py": "18259376c0404f98bc0cb91afeb31e33789f581562e14939989816c2fa8754d9",
    "services/execution/readiness_closed_history.py": "0c9cb90bc74402fdaed025f715a98a2e47169eee4d1f9990c75e8f5671e358da",
}


class Blocked(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise Blocked(code)


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


def scoped_transport(
    method: str, path: str, query: SecretStr, api_key: SecretStr | None
) -> RESTResponse:
    # Path/host validation also lives at the lowest network boundary.
    if method != "GET" or path not in READ_PATHS or REST_HOST != "https://demo-fapi.binance.com":
        raise ValueError("Read-only DEMO configuration route forbidden")
    url = REST_HOST + path
    payload = query.get_secret_value()
    headers = {"Accept": "application/json"}
    if api_key is not None:
        headers["X-MBX-APIKEY"] = api_key.get_secret_value()
    body = None
    if method in {"POST", "PUT"}:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        body = payload.encode()
    elif payload:
        url += "?" + payload
    request = Request(url, data=body, method=method, headers=headers)
    try:
        with build_opener(NoRedirect()).open(request, timeout=5) as response:
            return RESTResponse(
                response.status,
                response.read(4_194_305),
                rate_headers(getattr(response, "headers", None)),
            )
    except HTTPError as error:
        try:
            return RESTResponse(
                error.code,
                error.read(4_194_305),
                rate_headers(dict(error.headers.items()) if error.headers else None),
            )
        finally:
            error.close()
    except (URLError, OSError):
        raise DemoUnknown("DEMO network request failed; reconcile any pending mutation") from None


class ConfigClient(DemoClient):
    def public(self, path, params=None):
        require(
            path in {"/fapi/v1/time", "/fapi/v1/exchangeInfo"} and not params,
            "PUBLIC_SCOPE_FORBIDDEN",
        )
        return super().public(path, params)

    def request(self, method, path, params=None):
        require(
            method == "GET" and path in {OLD_MODE, OLD_ASSETS, NEW} and not params,
            "MUTATION_OR_SCOPE_FORBIDDEN",
        )
        if path != NEW:
            return super().request(method, path, params)
        # This reviewed one-route extension does not modify SDK allowlists.
        # Use the existing signer, clock bounds, cooldown and response decoder.
        now = self.clock_ms()
        require(
            self.synced_at is not None
            and 0 <= now - self.synced_at <= 60000
            and now >= self.cooldown_until,
            "CLOCK_STALE_OR_COOLDOWN",
        )
        signed = prepare_signed_query(
            {},
            api_key=self.credentials.api_key,
            secret=self.credentials.secret,
            timestamp_ms=now + self.offset,
        )
        try:
            response = self.send("GET", NEW, signed.query, signed.api_key)
        except DemoFailure:
            raise
        except Exception:
            raise DemoUnknown("Configuration read unavailable; no retry") from None
        return self._decode(response, mutation=False)

    def listen_key(self, *args, **kwargs):
        raise Blocked("LISTEN_KEY_FORBIDDEN")


def fields(raw, keys):
    require(
        isinstance(raw, dict) and all(type(raw.get(k)) is bool for k in keys),
        "MODE_RESPONSE_UNVERIFIED",
    )
    return {k: raw[k] for k in keys}


def compare(client, monitor):
    require(
        not client.settings.demo_trading_enabled
        and client.settings.margin_mode == "ISOLATED"
        and client.settings.symbols == ("BTCUSDT",),
        "READ_ONLY_CONTEXT_REQUIRED",
    )
    client.sync_clock()
    limit = published_limit(client.public("/fapi/v1/exchangeInfo"))
    monitor.enable(limit)

    def old():
        a = fields(client.request("GET", OLD_MODE), ("dualSidePosition",))
        b = fields(client.request("GET", OLD_ASSETS), ("multiAssetsMargin",))
        return a | b

    first = old()
    a = fields(client.request("GET", NEW), ("dualSidePosition", "multiAssetsMargin"))
    b = fields(client.request("GET", NEW), ("dualSidePosition", "multiAssetsMargin"))
    last = old()
    require(first == a == b == last, "ACCOUNT_MODES_CHANGED_OR_DISAGREED")
    require(not a["dualSidePosition"] and not a["multiAssetsMargin"], "ACCOUNT_MODE_UNSUPPORTED")
    monitor.check()
    observations = [
        {
            "route": "ACCOUNT_CONFIG"
            if r["path"] == NEW
            else "POSITION_MODE"
            if r["path"] == OLD_MODE
            else "ASSET_MODE",
            "observed_counter_delta": r["counter_delta"],
            "counter_reset_or_window_change": r["counter_reset_or_window_change"],
        }
        for r in monitor.records
        if r["signed"]
    ]
    return {
        "account_config_review": "TWO_MODE_FIELDS_MATCH_READ_ONLY_NO_RUNTIME_SWITCH",
        "environment": "DEMO",
        "sequence": "OLD_PAIR_NEW_NEW_OLD_PAIR",
        "dualSidePosition": a["dualSidePosition"],
        "multiAssetsMargin": a["multiAssetsMargin"],
        "signed_queries": len(observations),
        "http_attempts": len(monitor.records),
        "published_weight_limit_1m": limit,
        "observations": observations,
        "counter_deltas_include_other_ip_activity": True,
        "endpoint_weight_upper_bound_proven": False,
        "official_sdk_weight_model": {"old_pair": 60, "new_config": 5},
        "database_accessed": False,
        "database_changed": False,
        "keys_persisted_by_probe": False,
        "order_writes": 0,
        "positions_or_orders_checked": False,
        "runtime_installed": False,
        "runtime_switch_approved": False,
        "active_tp_budget_admission_proven": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
        "next_action": "REVIEW_ONLY_NO_TP_OR_INSTALL",
    }


def blocked(reason):
    return {
        "account_config_review": "BLOCKED_NO_RETRY_NO_RUNTIME_SWITCH",
        "stop_reason": reason,
        "database_accessed": False,
        "database_changed": False,
        "order_writes": 0,
        "runtime_installed": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }


def safe_reason(error):
    if isinstance(error, DemoRateLimited):
        return "RATE_LIMITED_NO_RETRY"
    if isinstance(error, (Blocked, ProbeStop)) and str(error) in STOP_CODES:
        return str(error)
    if isinstance(error, DemoFailure):
        return "SIGNED_CONFIG_QUERY_UNVERIFIED"
    return "CHECK_UNAVAILABLE"


def main():
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
        try:
            key = credentials(prompt=False)
        except Exception:
            raise Blocked("DEMO_CREDENTIAL_SESSION_UNAVAILABLE") from None
        monitor = Monitor(scoped_transport)
        client = ConfigClient(cfg, key, send=monitor)
        monitor.server_ms = lambda: client.clock_ms() + client.offset
        report = compare(client, monitor)
        code = 0
    except Exception as error:
        report = blocked(safe_reason(error))
    print(json.dumps(report), flush=True)
    raise SystemExit(code)


STOP_CODES = frozenset(
    [
        "ACCOUNT_MODES_CHANGED_OR_DISAGREED",
        "ACCOUNT_MODE_UNSUPPORTED",
        "CLOCK_BUDGET_REACHED",
        "CLOCK_STALE_OR_COOLDOWN",
        "DEMO_CREDENTIAL_SESSION_UNAVAILABLE",
        "HTTP_BUDGET_REACHED",
        "INSTALLED_SOURCE_HASH_MISMATCH",
        "LIMIT_DISCOVERY_REQUIRED",
        "LISTEN_KEY_FORBIDDEN",
        "LIVE_CONTEXT_FORBIDDEN",
        "MODE_RESPONSE_UNVERIFIED",
        "MUTATION_OR_SCOPE_FORBIDDEN",
        "NON_READ_OR_UNKNOWN_ROUTE_FORBIDDEN",
        "PUBLIC_SCOPE_FORBIDDEN",
        "PUBLISHED_LIMIT_AMBIGUOUS_OR_INVALID",
        "PUBLISHED_LIMIT_INVALID",
        "PUBLISHED_LIMIT_UNAVAILABLE",
        "RATE_LIMIT_ALREADY_OBSERVED",
        "READ_ONLY_CONTEXT_REQUIRED",
        "TIME_BUDGET_REACHED",
        "WEIGHT_HEADER_UNAVAILABLE",
        "WEIGHT_HEADROOM_THRESHOLD",
    ]
)
if __name__ == "__main__":
    main()
