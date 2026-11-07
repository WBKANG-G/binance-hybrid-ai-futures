import ast
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs

import pytest
from pydantic import SecretStr

from integrations.binance_usdm import demo
from integrations.binance_usdm.rest import RESTResponse

spec = importlib.util.spec_from_file_location(
    "account_probe", Path(__file__).with_name("compare_account_config.py")
)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


@pytest.fixture
def setup():
    settings = demo.DemoSettings.model_validate(
        p.yaml.load(
            Path("configs/demo-isolated.example.yaml").read_text(), Loader=p.UniqueKeyLoader
        )
    )
    calls = []
    weights = {"value": 100}

    def route(method, path, query, key):
        calls.append((method, path, query, key))
        weights["value"] += 5 if path == p.NEW else 30
        if path == "/fapi/v1/time":
            body = {"serverTime": 1794050000000}
        elif path == "/fapi/v1/exchangeInfo":
            body = {
                "rateLimits": [
                    {
                        "rateLimitType": "REQUEST_WEIGHT",
                        "interval": "MINUTE",
                        "intervalNum": 1,
                        "limit": 6000,
                    }
                ]
            }
        else:
            body = {
                "dualSidePosition": False,
                "multiAssetsMargin": False,
                "private_extra": "must-not-print",
            }
        return RESTResponse(
            200, json.dumps(body).encode(), {"x-mbx-used-weight-1m": str(weights["value"])}
        )

    monitor = p.Monitor(route, clock=lambda: 0)
    client = p.ConfigClient(
        settings,
        demo.DemoCredentials(SecretStr("synthetic-key"), SecretStr("synthetic-secret")),
        send=monitor,
        clock_ms=lambda: 1794050000000,
    )
    monitor.server_ms = lambda: client.clock_ms() + client.offset
    return client, monitor, calls, route


def test_sequence_and_safe_report(setup):
    client, monitor, calls, _ = setup
    allowed_before = demo.SIGNED_GET
    report = p.compare(client, monitor)
    assert [x[1] for x in calls] == [
        "/fapi/v1/time",
        "/fapi/v1/exchangeInfo",
        p.OLD_MODE,
        p.OLD_ASSETS,
        p.NEW,
        p.NEW,
        p.OLD_MODE,
        p.OLD_ASSETS,
    ]
    assert report["signed_queries"] == 6 and report["http_attempts"] == 8
    for key in (
        "database_accessed",
        "database_changed",
        "positions_or_orders_checked",
        "runtime_installed",
        "runtime_switch_approved",
        "active_tp_budget_admission_proven",
        "orders_enabled",
        "live_trading_enabled",
    ):
        assert report[key] is False
    assert report["order_writes"] == 0
    assert "private_extra" not in json.dumps(report)
    assert "synthetic" not in json.dumps(report)
    assert demo.SIGNED_GET is allowed_before and p.NEW not in demo.SIGNED_GET
    for _, _, q, k in calls[2:]:
        parsed = parse_qs(q.get_secret_value())
        assert set(parsed) == {"timestamp", "recvWindow", "signature"}
        assert len(parsed["signature"][0]) == 64
        assert k.get_secret_value() == "synthetic-key"


@pytest.mark.parametrize(
    "method,path,params",
    [
        ("POST", p.NEW, None),
        ("DELETE", "/fapi/v1/order", None),
        ("PUT", p.NEW, None),
        ("GET", "/fapi/v3/account", None),
        ("GET", p.NEW, {"symbol": "BTCUSDT"}),
        ("GET", "https://fapi.binance.com/fapi/v1/accountConfig", None),
    ],
)
def test_client_scope(setup, method, path, params):
    c, m, calls, _ = setup
    with pytest.raises(p.Blocked):
        c.request(method, path, params)
    assert calls == []


@pytest.mark.parametrize(
    "path,params",
    [("/fapi/v1/ticker/bookTicker", None), ("/fapi/v1/time", {"x": "y"}), (p.NEW, None)],
)
def test_public_scope(setup, path, params):
    c, _, calls, _ = setup
    with pytest.raises(p.Blocked):
        c.public(path, params)
    assert not calls


def test_listen_key(setup):
    with pytest.raises(p.Blocked):
        setup[0].listen_key("POST")
    assert not setup[2]


@pytest.mark.parametrize(
    "value",
    [
        {},
        [],
        None,
        {"dualSidePosition": "false"},
        {"dualSidePosition": 0},
        {"dualSidePosition": False, "multiAssetsMargin": None},
    ],
)
def test_strict_fields(value):
    with pytest.raises(p.Blocked):
        p.fields(value, ("dualSidePosition", "multiAssetsMargin"))


@pytest.mark.parametrize("mode", ["missing", "mismatch", "hedge", "multi"])
def test_modes_fail_closed(setup, mode):
    c, m, _, route = setup

    def changed(method, path, q, key):
        r = route(method, path, q, key)
        body = json.loads(r.body)
        if path in {p.NEW, p.OLD_MODE, p.OLD_ASSETS}:
            if mode == "missing" and path == p.NEW:
                body.pop("multiAssetsMargin")
            if mode == "mismatch" and path == p.NEW:
                body["dualSidePosition"] = True
            if mode == "hedge":
                body["dualSidePosition"] = True
            if mode == "multi":
                body["multiAssetsMargin"] = True
        return RESTResponse(r.status, json.dumps(body).encode(), r.headers)

    m.route = changed
    with pytest.raises(p.Blocked):
        p.compare(c, m)


@pytest.mark.parametrize("status", [418, 429])
@pytest.mark.parametrize("body", [b"not-json", b'{"code":-1003,"msg":"secret-url"}'])
def test_rate_limit_no_retry(setup, status, body):
    c, m, calls, route = setup

    def limited(method, path, q, key):
        if path == p.NEW:
            calls.append((method, path, q, key))
            return RESTResponse(status, body, {"retry-after": "120", "x-mbx-used-weight-1m": "300"})
        return route(method, path, q, key)

    m.route = limited
    with pytest.raises(demo.DemoRateLimited) as caught:
        p.compare(c, m)
    assert sum(x[1] == p.NEW for x in calls) == 1
    assert c.cooldown_until == c.clock_ms() + 120000
    assert p.safe_reason(caught.value) == "RATE_LIMITED_NO_RETRY"
    with pytest.raises(p.ProbeStop):
        m.check()


@pytest.mark.parametrize(
    "kind", ["missing_header", "headroom", "timeout", "http_cap", "clock_cap", "no_discovery"]
)
def test_monitor_budgets(setup, kind):
    c, m, _, route = setup
    if kind == "no_discovery":
        with pytest.raises(p.ProbeStop):
            m("GET", p.NEW, SecretStr(""), None)
        return
    if kind == "missing_header":
        m.route = lambda *args: RESTResponse(200, route(*args).body, {})
        with pytest.raises(p.ProbeStop):
            p.compare(c, m)
        return
    m.last_had_weight = True
    m.latest_weight = 1
    m.enable(6000)
    if kind == "headroom":
        m.latest_weight = 3500
    if kind == "timeout":
        m.clock = lambda: 30
    if kind == "http_cap":
        m.records = [{}] * 16
    if kind == "clock_cap":
        m.clock_attempts = 3
        with pytest.raises(p.ProbeStop):
            m("GET", "/fapi/v1/time", SecretStr(""), None)
        return
    with pytest.raises(p.ProbeStop):
        m.check()


@pytest.mark.parametrize(
    "raw",
    [
        {},
        [],
        {"rateLimits": []},
        {
            "rateLimits": [
                {
                    "rateLimitType": "REQUEST_WEIGHT",
                    "interval": "MINUTE",
                    "intervalNum": 1,
                    "limit": True,
                }
            ]
        },
    ],
)
def test_bad_limits(raw):
    with pytest.raises(p.Blocked):
        p.published_limit(raw)


@pytest.mark.parametrize(
    "synced,cooldown",
    [(None, 0), (1794049900000, 0), (1794050000001, 0), (1794050000000, 1794050000001)],
)
def test_clock_and_cooldown(setup, synced, cooldown):
    c, _, calls, _ = setup
    c.synced_at = synced
    c.cooldown_until = cooldown
    with pytest.raises(p.Blocked):
        c.request("GET", p.NEW)
    assert not calls


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", p.NEW),
        ("PUT", p.NEW),
        ("DELETE", p.NEW),
        ("GET", "/fapi/v1/order"),
        ("GET", "//evil.test/fapi/v1/accountConfig"),
    ],
)
def test_network_scope(monkeypatch, method, path):
    monkeypatch.setattr(p, "build_opener", lambda *a: pytest.fail("network reached"))
    with pytest.raises(ValueError):
        p.scoped_transport(method, path, SecretStr(""), None)


def test_transport_exact_approved_except_scope():
    source = ast.parse(Path(demo.__file__).read_text())
    old = next(x for x in source.body if isinstance(x, ast.FunctionDef) and x.name == "transport")
    new = next(
        x
        for x in ast.parse(Path(p.__file__).read_text()).body
        if isinstance(x, ast.FunctionDef) and x.name == "scoped_transport"
    )
    assert ast.dump(ast.Module(body=old.body[1:], type_ignores=[])) == ast.dump(
        ast.Module(body=new.body[1:], type_ignores=[])
    )


def test_transport_uses_tls_redirect_guard_and_limit(monkeypatch):
    seen = {}

    class Response:
        status = 200
        headers = {"x-mbx-used-weight-1m": "5"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            seen["closed"] = True

        def read(self, n):
            assert n == 4194305
            return b"{}"

    class Opener:
        def open(self, request, timeout):
            assert request.full_url.startswith(
                "https://demo-fapi.binance.com/fapi/v1/accountConfig?"
            )
            assert request.method == "GET" and request.data is None and timeout == 5
            return Response()

    def opener(handler):
        assert isinstance(handler, p.NoRedirect)
        return Opener()

    monkeypatch.setattr(p, "build_opener", opener)
    p.scoped_transport("GET", p.NEW, SecretStr("signature=synthetic"), SecretStr("synthetic-key"))
    assert seen["closed"]


def test_http_error_closed(monkeypatch):
    body = io.BytesIO(b"not-json")
    error = HTTPError("https://demo-fapi.binance.com", 429, "secret", {"Retry-After": "60"}, body)

    class Opener:
        def open(self, *a, **k):
            raise error

    monkeypatch.setattr(p, "build_opener", lambda *a: Opener())
    result = p.scoped_transport("GET", p.NEW, SecretStr(""), None)
    assert result.status == 429 and body.closed


def test_source_pins():
    assert len(p.NEW_PINS) == 31
    p.verify_sources()
    assert (
        p.NEW_PINS["integrations/binance_usdm/demo_snapshot.py"]
        == hashlib.sha256(
            Path("integrations/binance_usdm/demo_snapshot.py").read_bytes()
        ).hexdigest()
    )


def test_source_check_precedes_keys(monkeypatch, capsys):
    monkeypatch.setattr(
        p,
        "verify_sources",
        lambda: (_ for _ in ()).throw(p.Blocked("INSTALLED_SOURCE_HASH_MISMATCH")),
    )
    monkeypatch.setattr(p, "credentials", lambda **k: pytest.fail("credentials reached"))
    with pytest.raises(SystemExit) as e:
        p.main()
    assert e.value.code == 2
    assert json.loads(capsys.readouterr().out)["stop_reason"] == "INSTALLED_SOURCE_HASH_MISMATCH"


def test_credentials_unavailable(monkeypatch, capsys):
    monkeypatch.setattr(
        p, "credentials", lambda **k: (_ for _ in ()).throw(ValueError("secret-key-url"))
    )
    with pytest.raises(SystemExit):
        p.main()
    out = capsys.readouterr().out
    assert "secret-key-url" not in out
    assert json.loads(out)["stop_reason"] == "DEMO_CREDENTIAL_SESSION_UNAVAILABLE"


@pytest.mark.parametrize(
    "error",
    [ValueError("private-key"), demo.DemoUnknown("signed-url"), p.Blocked("unknown-secret")],
)
def test_redaction(error):
    assert (
        "private" not in p.safe_reason(error)
        and "secret" not in p.safe_reason(error)
        and "url" not in p.safe_reason(error)
    )


def test_counter_reset(setup):
    _, m, _, _ = setup
    m("GET", "/fapi/v1/time", SecretStr(""), None)
    m.route = lambda *a: RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "1"})
    m("GET", "/fapi/v1/exchangeInfo", SecretStr(""), None)
    assert m.records[-1]["counter_reset_or_window_change"] is True
    assert m.records[-1]["counter_delta"] is None


@pytest.mark.parametrize(
    "status,body",
    [
        (404, b'{"code":-404,"msg":"private"}'),
        (500, b"private"),
        (200, b"not-json"),
        (200, b"null"),
        (200, b'{"x":NaN}'),
        (200, b"x" * 4194305),
    ],
    ids=["unsupported-route", "server-error", "bad-json", "null", "non-finite", "oversized"],
)
def test_unsupported_endpoint_or_invalid_response_no_retry(setup, status, body):
    c, m, calls, route = setup

    def bad(method, path, q, key):
        if path == p.NEW:
            calls.append((method, path, q, key))
            return RESTResponse(status, body, {"x-mbx-used-weight-1m": "300"})
        return route(method, path, q, key)

    m.route = bad
    with pytest.raises(demo.DemoFailure):
        p.compare(c, m)
    assert sum(x[1] == p.NEW for x in calls) == 1


def test_live_main_before_keys(monkeypatch, capsys):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setattr(p, "credentials", lambda **k: pytest.fail("keys reached"))
    with pytest.raises(SystemExit):
        p.main()
    assert json.loads(capsys.readouterr().out)["stop_reason"] == "LIVE_CONTEXT_FORBIDDEN"


def test_readonly_main_success(setup, monkeypatch, capsys):
    c, m, _, _ = setup
    monkeypatch.setattr(p, "credentials", lambda **k: c.credentials)
    monkeypatch.setattr(p, "Monitor", lambda *a: m)
    monkeypatch.setattr(p, "ConfigClient", lambda *a, **k: c)
    with pytest.raises(SystemExit) as e:
        p.main()
    assert e.value.code == 0
    assert json.loads(capsys.readouterr().out)["signed_queries"] == 6
