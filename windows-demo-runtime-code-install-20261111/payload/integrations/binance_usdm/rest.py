"""Bounded, GET-only public transport. Signing and order intents never imply permission to trade."""

import hashlib
import hmac
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, NoReturn
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import SecretStr

PUBLIC_ENDPOINTS: dict[str, frozenset[str]] = {
    "/fapi/v1/time": frozenset(),
    "/fapi/v1/exchangeInfo": frozenset(),
    "/fapi/v1/klines": frozenset({"symbol", "interval", "startTime", "endTime", "limit"}),
    "/fapi/v1/depth": frozenset({"symbol", "limit"}),
    "/fapi/v1/ticker/bookTicker": frozenset({"symbol"}),
    "/fapi/v1/premiumIndex": frozenset({"symbol"}),
    "/fapi/v1/fundingRate": frozenset({"symbol", "startTime", "endTime", "limit"}),
    "/fapi/v1/openInterest": frozenset({"symbol"}),
}


class DisabledTrading(RuntimeError):
    pass


class ExchangeUnavailable(RuntimeError):
    pass


class RateLimited(ExchangeUnavailable):
    pass


@dataclass(frozen=True)
class RESTResponse:
    status: int
    body: bytes = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)


@dataclass(frozen=True)
class SignedQuery:
    query: SecretStr
    api_key: SecretStr


def sign_payload(secret: SecretStr, payload: str) -> str:
    return hmac.new(
        secret.get_secret_value().encode(), payload.encode(), hashlib.sha256
    ).hexdigest()


def prepare_signed_query(
    params: Mapping[str, str],
    *,
    api_key: SecretStr,
    secret: SecretStr,
    timestamp_ms: int,
    recv_window: int = 5000,
) -> SignedQuery:
    if {"signature", "timestamp", "recvWindow"} & params.keys():
        raise ValueError("Reserved signing fields")
    if (
        not isinstance(timestamp_ms, int)
        or isinstance(timestamp_ms, bool)
        or timestamp_ms < 0
        or not isinstance(recv_window, int)
        or isinstance(recv_window, bool)
        or not 1 <= recv_window <= 5000
    ):
        raise ValueError("Invalid signing time/window")
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in params.items()):
        raise ValueError("Signed parameters must be strings")
    if not api_key.get_secret_value() or not secret.get_secret_value():
        raise ValueError("Credentials must be present")
    values = dict(params) | {"timestamp": str(timestamp_ms), "recvWindow": str(recv_window)}
    query = urlencode(sorted(values.items()))
    return SignedQuery(SecretStr(query + "&signature=" + sign_payload(secret, query)), api_key)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def public_transport(path: str, params: Mapping[str, str]) -> RESTResponse:
    if path not in PUBLIC_ENDPOINTS or set(params) - PUBLIC_ENDPOINTS[path]:
        raise ValueError("Public endpoint/parameter is not allowed")
    url = "https://fapi.binance.com" + path + ("?" + urlencode(params) if params else "")
    request = Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=10) as response:
            body = response.read(4_194_305)
            if len(body) > 4_194_304:
                raise ExchangeUnavailable("Market response too large")
            return RESTResponse(response.status, body, dict(response.headers))
    except HTTPError as error:
        return RESTResponse(
            error.code, b"", {"Retry-After": error.headers.get("Retry-After", "60")}
        )
    except (URLError, OSError):
        raise ExchangeUnavailable("Public market connection failed") from None


class BinanceREST:
    def __init__(
        self, transport: Callable[[str, Mapping[str, str]], RESTResponse] = public_transport
    ) -> None:
        self.transport = transport

    def get(self, path: str, params: Mapping[str, str] | None = None) -> Any:
        values = dict(params or {})
        if path not in PUBLIC_ENDPOINTS or set(values) - PUBLIC_ENDPOINTS[path]:
            raise ValueError("Only approved public GET endpoints/parameters are available")
        if any(not isinstance(value, str) for value in values.values()):
            raise ValueError("Request parameters must be strings")
        response = self.transport(path, values)
        if response.status in {418, 429}:
            raise RateLimited("Exchange rate limit; stop and retry after cooldown")
        if response.status != 200:
            raise ExchangeUnavailable(f"Public market HTTP status {response.status}")
        try:

            def reject_constant(value: str) -> NoReturn:
                raise ValueError("Non-finite JSON")

            result = json.loads(response.body, parse_float=Decimal, parse_constant=reject_constant)
            if not isinstance(result, (dict, list)):
                raise ValueError("Unexpected response")
            if (
                isinstance(result, dict)
                and isinstance(result.get("code"), int)
                and result["code"] < 0
            ):
                raise ExchangeUnavailable("Public exchange API error")
            if path == "/fapi/v1/time" and (
                not isinstance(result, dict) or type(result.get("serverTime")) is not int
            ):
                raise ExchangeUnavailable("Invalid server time response")
            if path == "/fapi/v1/exchangeInfo" and (
                not isinstance(result, dict) or not isinstance(result.get("symbols"), list)
            ):
                raise ExchangeUnavailable("Invalid exchange metadata response")
            return result
        except (ValueError, UnicodeError):
            raise ExchangeUnavailable("Invalid public market response") from None

    def submit(self, intent: object) -> NoReturn:
        raise DisabledTrading("All order transmission is disabled in Phase 1")

    def historical_klines(
        self,
        symbol: str,
        interval: str,
        *,
        start_ms: int,
        end_ms: int,
        page_size: int = 1000,
        max_pages: int = 100,
    ) -> list[list[Any]]:
        from services.market_data.normalization import interval_ms

        step = interval_ms(interval)
        if (
            not 1 <= page_size <= 1500
            or max_pages < 1
            or not 0 <= start_ms < end_ms
            or start_ms % step != 0
            or end_ms % step != 0
        ):
            raise ValueError("Invalid historical range or pagination budget")
        cursor = start_ms
        result: list[list[Any]] = []
        for _ in range(max_pages):
            if cursor >= end_ms:
                return result
            page = self.get(
                "/fapi/v1/klines",
                {
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": str(cursor),
                    "endTime": str(end_ms - 1),
                    "limit": str(page_size),
                },
            )
            if not isinstance(page, list):
                raise ExchangeUnavailable("Invalid klines response")
            if not page:
                raise ExchangeUnavailable("Historical range contains missing candles")
            for row in page:
                if (
                    not isinstance(row, list)
                    or len(row) < 8
                    or not isinstance(row[0], int)
                    or not cursor <= row[0] < end_ms
                ):
                    raise ExchangeUnavailable("Non-progressing or malformed historical page")
                expected = result[-1][0] + step if result else start_ms
                if row[0] != expected:
                    raise ExchangeUnavailable(
                        "Historical candles contain a gap, duplicate or reverse ordering"
                    )
                result.append(row)
            cursor = int(page[-1][0]) + step
            if len(page) < page_size:
                if cursor < end_ms:
                    raise ExchangeUnavailable("Historical range ended before requested boundary")
                return result
        if cursor < end_ms:
            raise ExchangeUnavailable("Historical pagination budget exhausted")
        return result


@dataclass
class ServerClock:
    offset_ms: int = 0
    synced_at: float | None = None

    def synchronize(self, server_ms: int, *, sent_ms: int, received_ms: int) -> None:
        if received_ms < sent_ms or received_ms - sent_ms > 1000:
            raise ValueError("Clock sample RTT is unsafe")
        self.offset_ms = server_ms - (sent_ms + received_ms) // 2
        self.synced_at = time.monotonic()

    def timestamp(self, local_ms: int) -> int:
        if self.synced_at is None or time.monotonic() - self.synced_at > 60:
            raise ValueError("Server clock synchronization required")
        return local_ms + self.offset_ms
