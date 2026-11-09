"""Isolated USDⓈ-M DEMO transport. Production hosts and redirects are impossible.

No retries for mutations. No credentials, signed URLs, listen keys or raw response
bodies escape in errors. HMAC secret must be a direct runtime secret, not a proxy
placeholder; keys are never loaded from the application/PAPER/LIVE configuration.
"""

import json
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from pydantic import Field, SecretStr, model_validator

from integrations.binance_usdm.demo_rate_limits import rate_headers, retry_delay_ms
from integrations.binance_usdm.orders import OrderIntent
from integrations.binance_usdm.rest import NoRedirect, RESTResponse, prepare_signed_query
from packages.domain.config import ImmutableModel
from services.risk.paper import DemoPolicy

REST_HOST = "https://demo-fapi.binance.com"
WS_HOST = "wss://demo-fstream.binance.com"
PUBLIC = frozenset(
    {
        "/fapi/v1/time",
        "/fapi/v1/exchangeInfo",
        "/fapi/v1/ticker/bookTicker",
        "/fapi/v1/premiumIndex",
        "/fapi/v1/klines",
    }
)
SIGNED_GET = frozenset(
    {
        "/fapi/v1/accountConfig",
        "/fapi/v3/account",
        "/fapi/v1/symbolConfig",
        "/fapi/v1/income",
        "/fapi/v3/positionRisk",
        "/fapi/v1/positionSide/dual",
        "/fapi/v1/multiAssetsMargin",
        "/fapi/v1/order",
        "/fapi/v1/openOrders",
        "/fapi/v1/algoOrder",
        "/fapi/v1/openAlgoOrders",
        "/fapi/v1/userTrades",
        "/fapi/v1/trade/asyn",
        "/fapi/v1/trade/asyn/id",
    }
)


class DemoSettings(ImmutableModel):
    mode: Literal["DEMO"] = "DEMO"
    demo_trading_enabled: bool = Field(default=False, strict=True)
    live_trading_enabled: Literal[False] = False
    policy: DemoPolicy
    margin_mode: Literal["CROSSED", "ISOLATED"] = "CROSSED"
    symbols: tuple[str, ...] = ()

    @model_validator(mode="after")
    def verify_scope(self) -> "DemoSettings":
        if self.margin_mode == "ISOLATED" and (
            not self.symbols
            or len(set(self.symbols)) != len(self.symbols)
            or any(
                not s.isascii() or not s.isalnum() or not s.endswith("USDT") for s in self.symbols
            )
        ):
            raise ValueError("ISOLATED DEMO requires explicit unique USDT symbols")
        return self


@dataclass(frozen=True)
class DemoCredentials:
    api_key: SecretStr = field(repr=False)
    secret: SecretStr = field(repr=False)
    environment: Literal["DEMO"] = "DEMO"

    def __post_init__(self) -> None:
        if (
            self.environment != "DEMO"
            or not self.api_key.get_secret_value()
            or not self.secret.get_secret_value()
        ):
            raise ValueError("DEMO-only credentials required")

    @classmethod
    def from_environment(cls) -> "DemoCredentials":
        if any(
            os.getenv(k)
            for k in (
                "BINANCE_API_KEY",
                "BINANCE_API_SECRET",
                "BINANCE_LIVE_API_KEY",
                "BINANCE_LIVE_API_SECRET",
            )
        ):
            raise ValueError("Mixed generic/LIVE credentials are forbidden in the DEMO worker")
        if not os.getenv("BINANCE_DEMO_API_KEY") or not os.getenv("BINANCE_DEMO_API_SECRET"):
            raise ValueError("Missing direct runtime BINANCE_DEMO_API_KEY/BINANCE_DEMO_API_SECRET")
        return cls(
            SecretStr(os.environ["BINANCE_DEMO_API_KEY"]),
            SecretStr(os.environ["BINANCE_DEMO_API_SECRET"]),
        )


class DemoFailure(RuntimeError):
    pass


class DemoUnknown(DemoFailure):
    """Mutation outcome uncertain: reconcile; never retry the POST automatically."""


class DemoNotFound(DemoFailure):
    """A query did not find an order. This is not proof of a failed submit."""


class DemoRejected(DemoFailure):
    pass


class DemoRateLimited(DemoFailure):
    pass


Transport = Callable[[str, str, SecretStr, SecretStr | None], RESTResponse]


def transport(method: str, path: str, query: SecretStr, api_key: SecretStr | None) -> RESTResponse:
    # Path/host validation also lives at the lowest network boundary.
    if path not in PUBLIC | SIGNED_GET | {"/fapi/v1/listenKey"} or method not in {
        "GET",
        "POST",
        "PUT",
        "DELETE",
    }:
        raise ValueError("Unknown DEMO route")
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


@dataclass(frozen=True)
class DemoClockSample:
    before_ms: int
    after_ms: int
    server_ms: int


class DemoClient:
    def __init__(
        self,
        settings: DemoSettings,
        credentials: DemoCredentials,
        *,
        send: Transport = transport,
        clock_ms: Callable[[], int] | None = None,
    ):
        self.settings = DemoSettings.model_validate(settings.model_dump())
        self.credentials = credentials
        self.send = send
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.offset = 0
        self.synced_at: int | None = None
        self.clock_sample: DemoClockSample | None = None
        self._rate_lock = threading.Lock()
        self.cooldown_until = 0
        self.rate_limit_status = 0
        self.max_used_weight_1m = 0
        self.weight_header_samples = 0

    def rate_limit_diagnostics(self) -> dict[str, int | bool]:
        now = self.clock_ms()
        with self._rate_lock:
            remaining = max(0, self.cooldown_until - now)
            return {
                "http_rate_limit_status": self.rate_limit_status,
                "observed_max_used_weight_1m": self.max_used_weight_1m,
                "weight_header_samples": self.weight_header_samples,
                "cooldown_remaining_ms": min(3_600_000, remaining),
                "cooldown_remaining_capped": remaining > 3_600_000,
            }

    def _decode(self, response: RESTResponse, *, mutation: bool) -> Any:
        metadata = rate_headers(response.headers)
        now = self.clock_ms() if response.status in {418, 429} else 0
        with self._rate_lock:
            if "x-mbx-used-weight-1m" in metadata:
                self.weight_header_samples += 1
            self.max_used_weight_1m = max(
                self.max_used_weight_1m, int(metadata.get("x-mbx-used-weight-1m", "0"))
            )
            if response.status in {418, 429}:
                delay = retry_delay_ms(metadata.get("retry-after", ""), now)
                self.cooldown_until = max(self.cooldown_until, now + max(60_000, delay or 0))
                self.rate_limit_status = response.status
        if response.status in {418, 429}:
            raise DemoRateLimited("DEMO rate limited; stop submissions during cooldown")
        if len(response.body) > 4_194_304:
            raise DemoUnknown("DEMO response exceeded size budget")
        try:

            def reject(value: str) -> Any:
                raise ValueError("Non-finite JSON")

            data = json.loads(response.body, parse_float=Decimal, parse_constant=reject)
        except (ValueError, UnicodeError):
            raise DemoUnknown("Invalid DEMO JSON; reconcile") from None
        code = data.get("code") if isinstance(data, dict) else None
        if code in {-2013, -2011} and not mutation:
            raise DemoNotFound("DEMO order query not found; keep unresolved intents")
        if response.status >= 500 or code in {-1001, -1006, -1007}:
            raise DemoUnknown("DEMO outcome uncertain; reconcile by original client ID")
        if response.status != 200 or (isinstance(code, int) and code < 0):
            if (
                mutation
                and 400 <= response.status < 500
                and code in {-1013, -1100, -1102, -1111, -2010, -2019, -2021, -2022}
            ):
                raise DemoRejected("DEMO explicitly rejected request")
            raise DemoUnknown("DEMO request failed; no automatic mutation retry")
        if not isinstance(data, (dict, list)):
            raise DemoUnknown("Invalid DEMO response shape")
        return data

    def public(self, path: str, params: Mapping[str, str] | None = None) -> Any:
        if self.clock_ms() < self.cooldown_until:
            raise DemoRateLimited("DEMO rate-limit cooldown active")
        from urllib.parse import urlencode

        if path not in PUBLIC:
            raise ValueError("DEMO public path not allowed")
        return self._decode(
            self.send("GET", path, SecretStr(urlencode(params or {})), None), mutation=False
        )

    def sync_clock(self) -> None:
        before = self.clock_ms()
        data = self.public("/fapi/v1/time")
        after = self.clock_ms()
        if (
            not isinstance(data, dict)
            or type(data.get("serverTime")) is not int
            or not 0 <= after - before <= 1000
        ):
            self.synced_at = None
            self.clock_sample = None
            raise DemoFailure("Untrusted DEMO clock/RTT")
        offset = data["serverTime"] - (before + after) // 2
        if abs(offset) > 1000:
            self.synced_at = None
            self.clock_sample = None
            raise DemoFailure("DEMO clock offset exceeds safe budget")
        self.offset, self.synced_at = offset, after
        self.clock_sample = DemoClockSample(before, after, data["serverTime"])

    def request(self, method: str, path: str, params: Mapping[str, str] | None = None) -> Any:
        values = dict(params or {})
        mutation = method != "GET"
        allowed = (method == "GET" and path in SIGNED_GET) or (
            method in {"POST", "DELETE"} and path in {"/fapi/v1/order", "/fapi/v1/algoOrder"}
        )
        if not allowed or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in values.items()
        ):
            raise ValueError("DEMO signed request is not allowed")
        if mutation and not self.settings.demo_trading_enabled:
            raise DemoFailure("DEMO mutations are disabled by default")
        if mutation and self.settings.symbols:
            symbol = values.get("symbol")
            if method == "DELETE" and path == "/fapi/v1/algoOrder":
                # Algo cancellation identifies the order without a symbol. Query
                # the immutable client ID first rather than bypassing scope checks.
                identifier = values.get("clientAlgoId")
                if not identifier:
                    raise DemoFailure("Scoped DEMO Algo cancellation requires client ID")
                algo = self.request("GET", path, {"clientAlgoId": identifier})
                if not isinstance(algo, dict):
                    raise DemoFailure("Invalid DEMO Algo scope response")
                symbol = algo.get("symbol")
            if symbol not in self.settings.symbols:
                raise DemoFailure("DEMO mutation symbol outside configured scope")
        now = self.clock_ms()
        if (
            self.synced_at is None
            or not 0 <= now - self.synced_at <= 60_000
            or now < self.cooldown_until
        ):
            raise DemoFailure("Clock stale or cooldown active")
        if method == "POST":
            if path == "/fapi/v1/order" and not (
                values.get("type") == "LIMIT"
                or (values.get("type") == "MARKET" and values.get("reduceOnly") == "true")
            ):
                raise ValueError("New DEMO entries must be LIMIT; MARKET requires reduceOnly=true")
            if path == "/fapi/v1/algoOrder" and not (
                values.get("algoType") == "CONDITIONAL"
                and values.get("type") in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
                and values.get("closePosition") == "true"
                and not {"quantity", "reduceOnly"} & values.keys()
            ):
                raise ValueError("Separate close-position Algo contract required")
        signed = prepare_signed_query(
            values,
            api_key=self.credentials.api_key,
            secret=self.credentials.secret,
            timestamp_ms=now + self.offset,
        )
        try:
            response = self.send(method, path, signed.query, signed.api_key)
        except DemoFailure:
            raise
        except Exception:
            raise DemoUnknown("DEMO transport failed; reconcile pending intent") from None
        return self._decode(response, mutation=mutation)

    def submit(self, intent: OrderIntent) -> Any:
        return self.request("POST", intent.path, dict(intent.params))

    def listen_key(self, method: Literal["POST", "PUT", "DELETE"]) -> SecretStr | None:
        if self.clock_ms() < self.cooldown_until:
            raise DemoRateLimited("DEMO rate-limit cooldown active")
        if not self.settings.demo_trading_enabled:
            raise DemoFailure("DEMO session creation disabled")
        data = self._decode(
            self.send(method, "/fapi/v1/listenKey", SecretStr(""), self.credentials.api_key),
            mutation=True,
        )
        if method == "POST":
            key = data.get("listenKey") if isinstance(data, dict) else None
            if not isinstance(key, str) or not key.isalnum() or not 16 <= len(key) <= 256:
                raise DemoUnknown("Invalid private session")
            return SecretStr(key)
        return None

    @staticmethod
    def private_url(listen_key: SecretStr) -> SecretStr:
        key = listen_key.get_secret_value()
        if not key.isalnum() or not 16 <= len(key) <= 256:
            raise ValueError("Invalid private session")
        return SecretStr(WS_HOST + "/private/ws/" + key)
