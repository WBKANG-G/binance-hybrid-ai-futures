"""One-review GET-only keep-alive, preserving urllib proxy/TLS/no-redirect policy."""

import ssl
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.client import HTTPException, HTTPResponse, HTTPSConnection
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, Request, build_opener

from pydantic import SecretStr

from integrations.binance_usdm.demo import (
    REST_HOST,
    SIGNED_GET,
    DemoClient,
    DemoUnknown,
    transport,
)
from integrations.binance_usdm.rest import NoRedirect, RESTResponse

# Async exports are deliberately excluded: GET trade/asyn creates an export request.
GET_PATHS = (SIGNED_GET - {"/fapi/v1/trade/asyn", "/fapi/v1/trade/asyn/id"}) | {"/fapi/v1/time"}
MAX_BODY = 4_194_305
ORIGIN = urlsplit(REST_HOST)
EXPECTED_HOST = ORIGIN.hostname or ""
assert EXPECTED_HOST


def safe_transport_diagnostics(raw: dict[str, str | int] | None) -> dict[str, str | int]:
    raw = raw or {}
    result: dict[str, str | int] = {}
    if raw.get("audit_transport_mode") in {"POOLED_HTTPS", "UNPOOLED_TRANSPORT"}:
        result["audit_transport_mode"] = raw["audit_transport_mode"]
    if raw.get("audit_last_route") in {"NONE", "CLOCK", "REGULAR_ORDER", "ALGO_ORDER", "OTHER_GET"}:
        result["audit_last_route"] = raw["audit_last_route"]
    for key in (
        "audit_http_attempts",
        "audit_connection_attempts",
        "audit_reused_requests",
        "audit_server_close_responses",
        "audit_transport_elapsed_ms",
    ):
        value = raw.get(key)
        if type(value) is int:
            result[key] = max(0, min(3_600_000, value))
    return result


class RecoveryHTTPSHandler(HTTPSHandler):
    def __init__(self, context: ssl.SSLContext | None = None):
        secure = context or ssl.create_default_context()
        if not secure.check_hostname or secure.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError("Verified TLS required")
        super().__init__(debuglevel=0, context=secure)
        self.secure = secure
        self.connection: HTTPSConnection | None = None
        self.route: tuple[str, str | None, str | None] | None = None
        self.connection_attempts = self.reused_requests = self.requests = 0
        self.server_close_responses = 0

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
        self.connection = None
        self.route = None

    def https_open(self, request: Request) -> HTTPResponse:
        origin = urlsplit(request.full_url)
        tunnel = vars(request).get("_tunnel_host")
        selector = origin.path + ("?" + origin.query if origin.query else "")
        if (
            request.get_method() != "GET"
            or origin.scheme != "https"
            or origin.hostname != EXPECTED_HOST
            or origin.port not in {None, 443}
            or origin.username is not None
            or origin.password is not None
            or origin.fragment
            or origin.path not in GET_PATHS
            or request.selector != selector
            or tunnel not in {None, EXPECTED_HOST, EXPECTED_HOST + ":443"}
        ):
            raise ValueError("Recovery GET route forbidden")
        host = request.host
        if not host:
            raise ValueError("Recovery host unavailable")
        # Match urllib's proxy handling: proxy authorization belongs only on CONNECT.
        headers = dict(request.unredirected_hdrs)
        headers.update({k: v for k, v in request.headers.items() if k not in headers})
        headers = {k.title(): v for k, v in headers.items()}
        proxy_auth = headers.pop("Proxy-Authorization", None)
        route = (host, tunnel, proxy_auth)
        if self.route != route or self.connection is None:
            self.close()
            connection = HTTPSConnection(host, timeout=5, context=self.secure)
            connection.set_debuglevel(0)
            if tunnel:
                connection.set_tunnel(
                    tunnel, headers={"Proxy-Authorization": proxy_auth} if proxy_auth else {}
                )
            self.connection, self.route = connection, route
        connection = self.connection
        assert connection is not None
        if connection.sock is None:
            self.connection_attempts += 1
        else:
            self.reused_requests += 1
        headers["Connection"] = "keep-alive"
        self.requests += 1
        try:
            connection.request("GET", request.selector, None, headers, encode_chunked=False)
            response = connection.getresponse()
        except BaseException:
            self.close()
            raise  # The outer transport sanitizes errors; no implicit replay.
        if response.will_close:
            self.server_close_responses += 1
        # Same compatibility attributes used by urllib's standard do_open.
        compatible: Any = response
        compatible.url = request.full_url
        compatible.msg = response.reason
        return response


class RecoveryTransport:
    def __init__(self, *, context: ssl.SSLContext | None = None):
        self.handler = RecoveryHTTPSHandler(context)
        # Default ProxyHandler is retained by build_opener; redirects are refused.
        self.opener = build_opener(NoRedirect(), self.handler)
        self.started = time.monotonic()
        self.last_route = "NONE"

    def close(self) -> None:
        self.handler.close()

    def diagnostics(self) -> dict[str, str | int]:
        return {
            "audit_transport_mode": "POOLED_HTTPS",
            "audit_http_attempts": self.handler.requests,
            "audit_connection_attempts": self.handler.connection_attempts,
            "audit_reused_requests": self.handler.reused_requests,
            "audit_server_close_responses": self.handler.server_close_responses,
            "audit_transport_elapsed_ms": max(
                0, min(3_600_000, int((time.monotonic() - self.started) * 1000))
            ),
            "audit_last_route": self.last_route,
        }

    def __call__(
        self, method: str, path: str, query: SecretStr, api_key: SecretStr | None
    ) -> RESTResponse:
        if method != "GET" or path not in GET_PATHS:
            raise ValueError("Recovery transport GET route forbidden")
        self.last_route = (
            "CLOCK"
            if path == "/fapi/v1/time"
            else "REGULAR_ORDER"
            if path == "/fapi/v1/order"
            else "ALGO_ORDER"
            if path == "/fapi/v1/algoOrder"
            else "OTHER_GET"
        )
        payload = query.get_secret_value()
        headers = {"Accept": "application/json"}
        if api_key is not None:
            headers["X-MBX-APIKEY"] = api_key.get_secret_value()
        request = Request(
            REST_HOST + path + ("?" + payload if payload else ""), headers=headers, method="GET"
        )
        try:
            try:
                with self.opener.open(request, timeout=5) as response:
                    status, body = response.status, response.read(MAX_BODY)
            except HTTPError as error:
                try:
                    status, body = error.code, error.read(MAX_BODY)
                finally:
                    error.close()
            if len(body) >= MAX_BODY:
                self.close()  # Never reuse a connection with an unconsumed body.
            return RESTResponse(status, body, {})
        except (URLError, OSError, HTTPException, ValueError):
            self.close()
            raise DemoUnknown("Recovery HTTPS query failed; no automatic retry") from None


@contextmanager
def recovery_connection(client: DemoClient) -> Iterator[RecoveryTransport | None]:
    original = client.send
    if (
        type(client) is not DemoClient
        or original is not transport
        or client.settings.demo_trading_enabled
    ):
        yield None  # Preserve custom clients/transports and the original context rejection.
        return
    pooled = RecoveryTransport()
    try:
        client.send = pooled
        yield pooled
    finally:
        client.send = original
        pooled.close()
