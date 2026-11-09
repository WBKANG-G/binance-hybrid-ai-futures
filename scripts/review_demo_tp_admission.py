"""Four GETs only: review DEMO request-weight and account order-quota evidence.

Does not install code, open a stream, access a DB, acquire leadership or authorize
orders. The dedicated new USER_DATA query does not extend SDK route allowlists.
"""

# SDK imports intentionally follow the stdlib-only source cohort check.
# ruff: noqa: E402

import hashlib
import json
import math
import os
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs
from urllib.request import Request, build_opener

# Check the installed cohort using stdlib only, before importing any SDK source.
INSTALLED_PINS: dict[str, str] = {
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


def _verify_source_bytes_before_import() -> None:
    if len(INSTALLED_PINS) != 55:
        raise ValueError("PIN_COHORT_INVALID")
    for name, digest in INSTALLED_PINS.items():
        path = Path(name)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("INSTALLED_SOURCE_MISMATCH")


if __name__ == "__main__":
    try:
        _verify_source_bytes_before_import()
    except Exception:
        print(
            json.dumps(
                {
                    "tp_entry_admission_review": "BLOCKED_KEEP_PAUSED_NO_RETRY",
                    "stop_reason": "INSTALLED_SOURCE_MISMATCH",
                    "database_accessed": False,
                    "order_writes": 0,
                    "execution_approved": False,
                    "orders_enabled": False,
                    "live_trading_enabled": False,
                }
            )
        )
        raise SystemExit(2) from None

from pydantic import SecretStr

from integrations.binance_usdm.demo import (
    REST_HOST,
    DemoClient,
    DemoCredentials,
    DemoFailure,
    DemoRateLimited,
    DemoSettings,
    DemoUnknown,
    Transport,
)
from integrations.binance_usdm.demo_rate_limits import rate_headers
from integrations.binance_usdm.demo_tp_admission import published_limit
from integrations.binance_usdm.rest import NoRedirect, RESTResponse, prepare_signed_query
from packages.domain.config import UniqueKeyLoader
from scripts.demo_key_session import credentials
from services.execution.demo_order_admission import (
    AdmissionEvidenceInvalid,
    OrderSample,
    assess_order_admission,
    published_order_windows,
)

ORDER_USAGE_PATH = "/fapi/v1/rateLimit/order"
PROBE_PATHS = frozenset({"/fapi/v1/time", "/fapi/v1/exchangeInfo", ORDER_USAGE_PATH})
MAX_PROBE_REQUESTS = 4
MAX_PROBE_SECONDS = 10


class EvidenceStop(DemoFailure):
    pass


def evidence_require(condition: bool, code: str) -> None:
    if not condition:
        raise EvidenceStop(code)


def verify_installed_sources() -> None:
    evidence_require(len(INSTALLED_PINS) == 55, "PIN_COHORT_INVALID")
    for name, digest in INSTALLED_PINS.items():
        path = Path(name)
        evidence_require(path.is_file(), "INSTALLED_SOURCE_MISMATCH")
        evidence_require(
            hashlib.sha256(path.read_bytes()).hexdigest() == digest, "INSTALLED_SOURCE_MISMATCH"
        )


class AdmissionHeaders(Protocol):
    def items(self) -> Iterable[tuple[str, str]]: ...


def admission_response_headers(raw: AdmissionHeaders) -> dict[str, str]:
    safe = rate_headers(dict(raw.items()))
    for counter in ("x-mbx-order-count-10s", "x-mbx-order-count-1m"):
        values = [value for name, value in raw.items() if name.lower() == counter]
        if values:
            evidence_require(
                len(values) == 1
                and isinstance(values[0], str)
                and values[0].isascii()
                and values[0].isdecimal()
                and len(values[0]) <= 9,
                "ORDER_COUNTER_HEADER_INVALID",
            )
            safe[counter] = values[0]
    return safe


def admission_read_transport(
    method: str, path: str, query: SecretStr, api_key: SecretStr | None
) -> RESTResponse:
    # Unlike the SDK's generic request transport, this boundary has no mutation
    # branch. Neither caller-controlled URLs nor redirects are permitted.
    evidence_require(REST_HOST == "https://demo-fapi.binance.com", "DEMO_HOST_REQUIRED")
    evidence_require(method == "GET" and path in PROBE_PATHS, "READ_SCOPE_FORBIDDEN")
    params = parse_qs(query.get_secret_value(), keep_blank_values=True)
    if path == ORDER_USAGE_PATH:
        evidence_require(
            set(params) == {"timestamp", "recvWindow", "signature"}
            and all(len(v) == 1 for v in params.values())
            and params["timestamp"][0].isascii()
            and params["timestamp"][0].isdecimal()
            and params["recvWindow"] == ["5000"]
            and len(params["signature"][0]) == 64
            and all(c in "0123456789abcdef" for c in params["signature"][0])
            and isinstance(api_key, SecretStr)
            and bool(api_key.get_secret_value()),
            "SIGNED_READ_SCOPE_FORBIDDEN",
        )
    else:
        evidence_require(
            not query.get_secret_value() and api_key is None, "PUBLIC_READ_SCOPE_FORBIDDEN"
        )
    headers = {"Accept": "application/json"}
    if api_key is not None:
        headers["X-MBX-APIKEY"] = api_key.get_secret_value()
    target = REST_HOST + path + ("?" + query.get_secret_value() if params else "")
    request = Request(target, method="GET", headers=headers)
    try:
        with build_opener(NoRedirect()).open(request, timeout=5) as response:
            return RESTResponse(
                response.status,
                response.read(4_194_305),
                admission_response_headers(response.headers),
            )
    except HTTPError as error:
        try:
            return RESTResponse(
                error.code,
                error.read(4_194_305),
                rate_headers(dict(error.headers.items()))
                if error.code in {418, 429}
                else admission_response_headers(error.headers),
            )
        finally:
            error.close()
    except (URLError, OSError):
        raise DemoUnknown("Read-only admission transport unavailable") from None


class AdmissionReadMonitor:
    def __init__(self, sender: Transport, *, monotonic: Any = time.monotonic):
        self.sender, self.clock = sender, monotonic
        self.started = self.last = monotonic()
        self.records: list[tuple[str, int]] = []
        self.weight_limit: int | None = None
        self.peak_weight = 0
        self.limited = False
        self.last_order_headers: dict[str, int] = {}

    def check(self) -> None:
        now = self.clock()
        evidence_require(
            math.isfinite(now)
            and math.isfinite(self.started)
            and self.last <= now
            and 0 <= now - self.started < MAX_PROBE_SECONDS,
            "PROBE_CLOCK_OR_TIME_BUDGET",
        )
        self.last = now
        evidence_require(not self.limited, "RATE_LIMITED_NO_RETRY")
        if self.weight_limit is not None:
            evidence_require(
                self.peak_weight + 100 < self.weight_limit * 3 // 5,
                "READ_WEIGHT_HEADROOM_INSUFFICIENT",
            )

    def enable(self, info: Any) -> None:
        self.weight_limit = published_limit(info)
        published_order_windows(info)
        self.check()

    def __call__(
        self, method: str, path: str, query: SecretStr, key: SecretStr | None
    ) -> RESTResponse:
        evidence_require(method == "GET" and path in PROBE_PATHS, "READ_SCOPE_FORBIDDEN")
        evidence_require(len(self.records) < MAX_PROBE_REQUESTS, "PROBE_HTTP_BUDGET")
        if path == ORDER_USAGE_PATH:
            evidence_require(self.weight_limit is not None, "LIMIT_DISCOVERY_REQUIRED")
        self.check()
        self.records.append((path, 0))  # Count attempts even when a response is lost.
        response = self.sender(method, path, query, key)
        if response.status in {418, 429}:
            self.limited = True
            return response  # Let the original SDK set cooldown and raise its type.
        self.last_order_headers = {}
        for counter in ("x-mbx-order-count-10s", "x-mbx-order-count-1m"):
            values = [value for name, value in response.headers.items() if name.lower() == counter]
            if values:
                evidence_require(
                    len(values) == 1
                    and isinstance(values[0], str)
                    and values[0].isascii()
                    and values[0].isdecimal()
                    and len(values[0]) <= 9,
                    "ORDER_COUNTER_HEADER_INVALID",
                )
                self.last_order_headers[counter] = int(values[0])
        safe = rate_headers(response.headers)
        value = safe.get("x-mbx-used-weight-1m")
        evidence_require(value is not None, "WEIGHT_HEADER_UNAVAILABLE")
        self.peak_weight = max(self.peak_weight, int(value or "0"))
        self.check()
        return response


class AdmissionReadClient(DemoClient):
    def __init__(self, settings: DemoSettings, key: DemoCredentials, **kwargs: Any):
        evidence_require(
            not settings.demo_trading_enabled
            and not settings.live_trading_enabled
            and key.environment == "DEMO",
            "READ_ONLY_CONTEXT_REQUIRED",
        )
        super().__init__(settings, key, **kwargs)

    def public(self, path: str, params: Mapping[str, str] | None = None) -> Any:
        evidence_require(
            path in {"/fapi/v1/time", "/fapi/v1/exchangeInfo"} and not params,
            "PUBLIC_READ_SCOPE_FORBIDDEN",
        )
        return super().public(path, params)

    def request(self, method: str, path: str, params: Mapping[str, str] | None = None) -> Any:
        raise EvidenceStop("GENERIC_SIGNED_REQUEST_FORBIDDEN")

    def listen_key(self, *args: Any, **kwargs: Any) -> SecretStr | None:
        raise EvidenceStop("LISTEN_KEY_FORBIDDEN")

    def query_order_usage(self) -> Any:
        evidence_require(
            not self.settings.demo_trading_enabled and not self.settings.live_trading_enabled,
            "READ_ONLY_CONTEXT_REQUIRED",
        )
        now = self.clock_ms()
        if now < self.cooldown_until:
            raise DemoRateLimited("Read-only admission cooldown active")
        evidence_require(
            self.synced_at is not None and 0 <= now - self.synced_at <= 5000,
            "ADMISSION_CLOCK_STALE",
        )
        signed = prepare_signed_query(
            {},
            api_key=self.credentials.api_key,
            secret=self.credentials.secret,
            timestamp_ms=now + self.offset,
        )
        return self._decode(
            self.send("GET", ORDER_USAGE_PATH, signed.query, signed.api_key), mutation=False
        )


def review_admission(client: AdmissionReadClient, monitor: AdmissionReadMonitor) -> dict[str, Any]:
    client.sync_clock()
    info = client.public("/fapi/v1/exchangeInfo")
    monitor.enable(info)
    first_body = client.query_order_usage()
    first = OrderSample(monitor.clock(), first_body, monitor.last_order_headers.copy())
    second_body = client.query_order_usage()
    second = OrderSample(monitor.clock(), second_body, monitor.last_order_headers.copy())
    monitor.check()
    report = assess_order_admission(info, first, second, now=monitor.clock())
    report.update(
        {
            "tp_entry_admission_review": "READ_ONLY_EVIDENCE_COLLECTED_KEEP_PAUSED",
            "published_weight_limit_1m": monitor.weight_limit,
            "observed_max_used_weight_1m": monitor.peak_weight,
            "http_attempts": len(monitor.records),
            "signed_order_usage_queries": 2,
            "readiness_rechecked": False,
            "database_accessed": False,
            "database_changed": False,
            "leader_acquired": False,
            "order_writes": 0,
            "pause_modified": False,
            "runtime_installed": False,
            "orders_enabled": False,
            "live_trading_enabled": False,
            "ip_capacity_reserved": False,
            "production_activation_enabled": False,
            "remaining_approval_blockers": [
                "REQUEST_COST_CONTRACT_REVIEW_PENDING",
                "CROSS_PROCESS_PRESSURE_POLICY_REVIEW_PENDING",
                "PRODUCTION_DEFAULT_EXECUTION_DISABLED",
            ],
            "next_action": "REVIEW_EVIDENCE_NO_ORDER_OR_RUNTIME_SWITCH",
        }
    )
    return report


def main() -> None:
    # Reject LIVE/mixed-key context before reading even a session credential.
    code = 2
    report: dict[str, Any] = {
        "tp_entry_admission_review": "BLOCKED_KEEP_PAUSED_NO_RETRY",
        "stop_reason": "CHECK_UNAVAILABLE",
        "database_accessed": False,
        "order_writes": 0,
        "execution_approved": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
        "next_action": "KEEP_PAUSED_NO_TP_RETRY",
    }
    try:
        evidence_require(
            os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE"
            and os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"
            and not any(
                os.getenv(k)
                for k in (
                    "BINANCE_API_KEY",
                    "BINANCE_API_SECRET",
                    "BINANCE_LIVE_API_KEY",
                    "BINANCE_LIVE_API_SECRET",
                )
            ),
            "LIVE_OR_MIXED_CONTEXT_FORBIDDEN",
        )
        verify_installed_sources()
        import yaml

        cfg = DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
            )
        ).model_copy(update={"demo_trading_enabled": False})
        try:
            key = credentials(prompt=False)
        except Exception:
            raise EvidenceStop("DEMO_CREDENTIAL_SESSION_UNAVAILABLE") from None
        monitor = AdmissionReadMonitor(admission_read_transport)
        client = AdmissionReadClient(cfg, key, send=monitor)
        report = review_admission(client, monitor)
        code = 0 if not report["order_quota_blockers"] else 2
    except DemoRateLimited:
        report["stop_reason"] = "RATE_LIMITED_NO_RETRY"
    except (AdmissionEvidenceInvalid, EvidenceStop) as error:
        report["stop_reason"] = str(error) if str(error) in EVIDENCE_CODES else "CHECK_UNAVAILABLE"
    except DemoFailure:
        report["stop_reason"] = "DEMO_READ_QUERY_FAILED"
    except Exception:
        pass  # Never print network/SDK/credential bodies, URLs, queries or keys.
    print(json.dumps(report), flush=True)
    if code:
        raise SystemExit(code)


EVIDENCE_CODES = frozenset(
    {
        "ORDER_COUNTER_HEADER_INVALID",
        "ORDER_INTEGER_INVALID",
        "ORDER_WINDOW_INVALID",
        "ORDER_INTERVAL_UNSUPPORTED",
        "ORDER_LIMITS_UNAVAILABLE",
        "ORDER_WINDOW_DUPLICATE",
        "REQUIRED_ORDER_WINDOWS_MISSING",
        "ORDER_USAGE_UNAVAILABLE",
        "ORDER_SAMPLE_STALE_OR_CLOCK_INVALID",
        "ORDER_WINDOW_COVERAGE_MISMATCH",
        "PIN_COHORT_INVALID",
        "INSTALLED_SOURCE_MISMATCH",
        "DEMO_HOST_REQUIRED",
        "READ_SCOPE_FORBIDDEN",
        "SIGNED_READ_SCOPE_FORBIDDEN",
        "PUBLIC_READ_SCOPE_FORBIDDEN",
        "PROBE_CLOCK_OR_TIME_BUDGET",
        "RATE_LIMITED_NO_RETRY",
        "READ_WEIGHT_HEADROOM_INSUFFICIENT",
        "PROBE_HTTP_BUDGET",
        "LIMIT_DISCOVERY_REQUIRED",
        "WEIGHT_HEADER_UNAVAILABLE",
        "READ_ONLY_CONTEXT_REQUIRED",
        "GENERIC_SIGNED_REQUEST_FORBIDDEN",
        "LISTEN_KEY_FORBIDDEN",
        "ADMISSION_CLOCK_STALE",
        "LIVE_OR_MIXED_CONTEXT_FORBIDDEN",
        "DEMO_CREDENTIAL_SESSION_UNAVAILABLE",
    }
)

if __name__ == "__main__":
    main()
