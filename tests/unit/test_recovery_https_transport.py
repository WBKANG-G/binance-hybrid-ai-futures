"""Real local verified TLS connection reuse; never actual broker credentials."""

import json
import socket
import ssl
import subprocess
import threading
import urllib.request
from http.client import HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from integrations.binance_usdm import recovery_transport as module
from integrations.binance_usdm.demo import DemoUnknown


@pytest.fixture
def tls_server(tmp_path, monkeypatch):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-nodes",
            "-newkey",
            "rsa:2048",
            "-days",
            "1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-subj",
            "/CN=demo-fapi.binance.com",
            "-addext",
            "subjectAltName=DNS:demo-fapi.binance.com",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(str(cert), str(key))
    trusted = ssl.create_default_context(cafile=str(cert))
    state = SimpleNamespace(
        records=[], connections=0, responder=None, on_connect=None, on_request=None
    )

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        disable_nagle_algorithm = True

        def setup(self):
            super().setup()
            state.connections += 1

        def log_message(self, *args):
            pass

        def do_GET(self):
            state.records.append((self.path, dict(self.headers)))
            if state.on_request:
                state.on_request()
            status, body, headers = (
                state.responder(self.path, self.headers)
                if state.responder
                else (200, b'{"serverTime":123}', {})
            )
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    runner = threading.Thread(target=server.serve_forever, daemon=True)
    runner.start()
    original_default = ssl.create_default_context

    class Routed(HTTPSConnection):
        def __init__(self, host, *args, context=None, **kwargs):
            assert host == module.EXPECTED_HOST
            super().__init__(host, *args, context=context or trusted, **kwargs)

        def connect(self):
            if state.on_connect:
                state.on_connect()
            raw = socket.create_connection(server.server_address, timeout=5)
            raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock = self._context.wrap_socket(raw, server_hostname=module.EXPECTED_HOST)

    monkeypatch.setattr(module, "HTTPSConnection", Routed)

    def original_local_https_open(handler, request):
        return handler.do_open(Routed, request, context=trusted)

    monkeypatch.setattr(urllib.request.HTTPSHandler, "https_open", original_local_https_open)
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    monkeypatch.setattr(module.ssl, "create_default_context", lambda: trusted)
    state.trusted = trusted
    state.original_default = original_default
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        runner.join(timeout=3)


def test_success_and_not_found_bodies_use_single_verified_tls_connection(tls_server):
    state = tls_server

    def response(path, headers):
        return (404, b'{"code":-2013}', {}) if "order" in path else (200, b'{"serverTime":123}', {})

    state.responder = response
    pooled = module.RecoveryTransport()
    try:
        a = pooled("GET", "/fapi/v1/time", SecretStr(""), None)
        b = pooled(
            "GET",
            "/fapi/v1/order",
            SecretStr("symbol=BTCUSDT&signature=fixture"),
            SecretStr("fixture-key"),
        )
        c = pooled("GET", "/fapi/v1/time", SecretStr(""), None)
        assert (
            a.status == c.status == 200 and b.status == 404 and json.loads(b.body)["code"] == -2013
        )
        assert state.connections == 1
        assert pooled.diagnostics()["audit_connection_attempts"] == 1
        assert pooled.diagnostics()["audit_reused_requests"] == 2
        assert state.records[1][1]["X-Mbx-Apikey"] == "fixture-key"
        assert "signature=fixture" in state.records[1][0]
        assert "X-Mbx-Apikey" not in state.records[2][1]
        assert "fixture-key" not in json.dumps(pooled.diagnostics())
    finally:
        pooled.close()
    assert pooled.handler.connection is None


def test_redirect_is_not_followed_or_given_credentials(tls_server):
    state = tls_server
    state.responder = lambda p, h: (302, b"{}", {"Location": "https://forbidden.invalid/PRIVATE"})
    pooled = module.RecoveryTransport()
    try:
        response = pooled(
            "GET", "/fapi/v1/order", SecretStr("symbol=BTCUSDT"), SecretStr("fixture-key")
        )
        assert response.status == 302 and len(state.records) == 1
    finally:
        pooled.close()


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/fapi/v1/order"),
        ("DELETE", "/fapi/v1/algoOrder"),
        ("GET", "/fapi/v1/trade/asyn"),
        ("GET", "/fapi/v1/trade/asyn/id"),
        ("GET", "/not-allowed"),
    ],
)
def test_write_export_and_unknown_paths_block_before_network(method, path):
    pooled = module.RecoveryTransport()
    try:
        with pytest.raises(ValueError):
            pooled(method, path, SecretStr(""), None)
        assert pooled.handler.requests == 0
    finally:
        pooled.close()


def test_insecure_tls_context_is_rejected():
    insecure = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    insecure.check_hostname = False
    insecure.verify_mode = ssl.CERT_NONE
    with pytest.raises(ValueError):
        module.RecoveryTransport(context=insecure)


def test_network_failure_is_sanitized_and_never_retried(monkeypatch):
    calls = []

    class Broken:
        sock = None

        def __init__(self, *a, **kw):
            pass

        def set_debuglevel(self, n):
            assert n == 0

        def request(self, *a, **kw):
            calls.append(1)
            raise OSError("PRIVATE_KEY signed_url")

        def close(self):
            pass

    monkeypatch.setattr(module, "HTTPSConnection", Broken)
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    pooled = module.RecoveryTransport()
    with pytest.raises(DemoUnknown) as error:
        pooled("GET", "/fapi/v1/time", SecretStr(""), None)
    assert len(calls) == 1 and "PRIVATE_KEY" not in str(error.value)
    assert pooled.handler.connection is None


def test_proxy_connect_auth_is_kept_out_of_origin_headers(monkeypatch):
    made = []

    class Connection:
        sock = None

        def __init__(self, host, **kwargs):
            self.host = host
            self.headers = None
            self.tunnel = None
            made.append(self)
            assert kwargs["timeout"] == 5 and kwargs["context"].check_hostname

        def set_debuglevel(self, n):
            assert n == 0

        def set_tunnel(self, host, headers):
            self.tunnel = (host, headers)

        def request(self, m, p, b, headers, **kw):
            self.headers = headers
            self.sock = object()

        def getresponse(self):
            return SimpleNamespace(will_close=False, reason="OK")

        def close(self):
            self.sock = None

    monkeypatch.setattr(module, "HTTPSConnection", Connection)
    handler = module.RecoveryHTTPSHandler()
    request = urllib.request.Request(module.REST_HOST + "/fapi/v1/time", method="GET")
    request.set_proxy("proxy.fixture:8080", "http")
    request.add_header("Proxy-Authorization", "Basic private-proxy-token")
    handler.https_open(request)
    handler.https_open(request)
    assert len(made) == 1 and made[0].host == "proxy.fixture:8080"
    assert made[0].tunnel == (
        module.EXPECTED_HOST,
        {"Proxy-Authorization": "Basic private-proxy-token"},
    )
    assert "Proxy-Authorization" not in made[0].headers
    handler.close()


def test_transport_diagnostics_cannot_export_unknown_strings_or_secret_keys():
    result = module.safe_transport_diagnostics(
        {
            "audit_transport_mode": "PRIVATE_KEY",
            "audit_last_route": "https://private/signed_url",
            "secret": "VALUE",
            "audit_http_attempts": 3,
            "audit_connection_attempts": True,
        }
    )
    assert result == {"audit_http_attempts": 3}


def test_server_connection_close_opens_a_new_connection_for_next_query(tls_server):
    state = tls_server
    state.responder = lambda p, h: (200, b"{}", {"Connection": "close"})
    pooled = module.RecoveryTransport()
    try:
        pooled("GET", "/fapi/v1/time", SecretStr(""), None)
        pooled("GET", "/fapi/v1/time", SecretStr(""), None)
        assert state.connections == 2 and pooled.handler.connection_attempts == 2
        assert pooled.handler.server_close_responses == 2
    finally:
        pooled.close()


@pytest.mark.parametrize(
    "url",
    [
        "https://fapi.binance.com/fapi/v1/time",
        "http://demo-fapi.binance.com/fapi/v1/time",
        "https://demo-fapi.binance.com:444/fapi/v1/time",
        "https://private:secret@demo-fapi.binance.com/fapi/v1/time",
        "https://demo-fapi.binance.com/fapi/v1/time#secret",
    ],
)
def test_origin_scheme_port_and_credentials_are_pinned_before_connection(monkeypatch, url):
    monkeypatch.setattr(module, "HTTPSConnection", lambda *a, **kw: pytest.fail("must not connect"))
    handler = module.RecoveryHTTPSHandler()
    with pytest.raises(ValueError):
        handler.https_open(urllib.request.Request(url, method="GET"))


def test_custom_client_is_not_rewired_even_with_default_sdk_sender():
    from integrations.binance_usdm.demo import DemoClient
    from tests.unit.test_demo_transport import client

    source, _ = client(enabled=False)

    class Custom(DemoClient):
        pass

    custom = Custom(source.settings, source.credentials)
    original = custom.send
    with module.recovery_connection(custom) as pooled:
        assert pooled is None and custom.send is original
    assert custom.send is original
