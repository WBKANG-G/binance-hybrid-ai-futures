"""SDK-isolated GET boundary, exact request cap, metadata and redaction tests."""

import json
from email.message import Message

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.demo import SIGNED_GET, DemoFailure, DemoRateLimited
from integrations.binance_usdm.rest import RESTResponse, prepare_signed_query
from scripts import review_demo_tp_admission as m
from tests.unit.test_demo_order_admission import limits, usage
from tests.unit.test_demo_transport import client


def setup(mode="body"):
    source, _ = client(enabled=False)
    now = [1.0]
    calls = []

    def send(method, path, query, key):
        assert method == "GET"
        calls.append(path)
        now[0] += 0.01
        headers = {"x-mbx-used-weight-1m": str(100 + len(calls))}
        if path == "/fapi/v1/time":
            body = {"serverTime": source.clock_ms()}
        elif path == "/fapi/v1/exchangeInfo":
            body = limits()
        else:
            assert path == m.ORDER_USAGE_PATH
            body = usage(body=mode == "body")
            if mode == "header":
                headers |= {"X-MBX-ORDER-COUNT-10S": "0", "X-MBX-ORDER-COUNT-1M": "0"}
        return RESTResponse(200, json.dumps(body).encode(), headers)

    monitor = m.AdmissionReadMonitor(send, monotonic=lambda: now[0])
    c = m.AdmissionReadClient(
        source.settings, source.credentials, send=monitor, clock_ms=source.clock_ms
    )
    return c, monitor, calls, now


@pytest.mark.parametrize("mode", ["body", "header", "missing"])
def test_four_gets_only_and_missing_counters_stay_unknown(mode):
    c, mon, calls, _ = setup(mode)
    before = SIGNED_GET
    report = m.review_admission(c, mon)
    assert calls == [
        "/fapi/v1/time",
        "/fapi/v1/exchangeInfo",
        m.ORDER_USAGE_PATH,
        m.ORDER_USAGE_PATH,
    ]
    assert report["http_attempts"] == 4 and report["signed_order_usage_queries"] == 2
    assert (
        not report["execution_approved"]
        and not report["database_accessed"]
        and report["order_writes"] == 0
    )
    assert m.ORDER_USAGE_PATH not in SIGNED_GET and SIGNED_GET == before
    assert ("ORDER_COUNTER_NOT_PROVIDED" in report["order_quota_blockers"]) == (mode == "missing")
    with pytest.raises(m.EvidenceStop, match="PROBE_HTTP_BUDGET"):
        c.query_order_usage()
    assert len(calls) == 4


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/fapi/v1/order"),
        ("DELETE", "/fapi/v1/algoOrder"),
        ("PUT", "/fapi/v1/listenKey"),
        ("GET", "/fapi/v1/trade/asyn"),
        ("GET", "/fapi/v3/account"),
    ],
)
def test_generic_sdk_request_and_network_boundary_forbid_every_other_route(method, path):
    c, mon, calls, _ = setup()
    with pytest.raises(m.EvidenceStop):
        c.request(method, path, {})
    with pytest.raises(m.EvidenceStop):
        mon(method, path, SecretStr(""), None)
    with pytest.raises(m.EvidenceStop):
        m.admission_read_transport(method, path, SecretStr(""), None)
    assert calls == []


def test_listen_key_and_enabled_context_are_forbidden():
    c, _, calls, _ = setup()
    with pytest.raises(m.EvidenceStop):
        c.listen_key("POST")
    with pytest.raises(m.EvidenceStop):
        m.AdmissionReadClient(
            c.settings.model_copy(update={"demo_trading_enabled": True}), c.credentials
        )
    assert calls == []


@pytest.mark.parametrize(
    "fault",
    [
        "missing_weight",
        "high_weight",
        "backward",
        "nan",
        "timeout",
        "body_private",
        "rate429",
        "rate418",
    ],
)
def test_metadata_errors_do_not_retry_or_authorize(fault):
    c, mon, calls, now = setup()
    real = mon.sender

    def sender(method, path, query, key):
        response = real(method, path, query, key)
        if fault == "missing_weight":
            return RESTResponse(200, response.body, {})
        if fault == "high_weight":
            return RESTResponse(200, response.body, {"x-mbx-used-weight-1m": "5999"})
        if fault == "backward":
            now[0] -= 1
        if fault == "nan":
            now[0] = float("nan")
        if fault == "timeout":
            now[0] += 11
        if fault in {"rate429", "rate418"}:
            return RESTResponse(int(fault[-3:]), b"SYNTHETIC_PRIVATE_BODY", {"retry-after": "120"})
        if fault == "body_private":
            return RESTResponse(200, b"SYNTHETIC_PRIVATE_BODY", response.headers)
        return response

    mon.sender = sender
    with pytest.raises(DemoFailure) as error:
        m.review_admission(c, mon)
    if fault in {"rate429", "rate418"}:
        assert isinstance(error.value, DemoRateLimited)
        assert c.cooldown_until - c.clock_ms() >= 120000
    assert len(calls) <= 2


@pytest.mark.parametrize("bad", ["-1", "1.2", "NaN", "１２", "x" * 500])
def test_order_header_malformed_values_are_rejected(bad):
    with pytest.raises(m.EvidenceStop):
        m.admission_response_headers({"X-MBX-ORDER-COUNT-10S": bad})


def test_header_duplicates_and_unknown_private_fields():
    headers = Message()
    headers["X-MBX-ORDER-COUNT-10S"] = "0"
    headers["x-mbx-order-count-10s"] = "1"
    with pytest.raises(m.EvidenceStop):
        m.admission_response_headers(headers)
    safe = m.admission_response_headers(
        {"X-MBX-ORDER-COUNT-10S": "4", "Set-Cookie": "PRIVATE", "X-API-KEY": "PRIVATE"}
    )
    assert safe == {"x-mbx-order-count-10s": "4"}


@pytest.mark.parametrize(
    "context,value",
    [
        ("TRADING_MODE", "live"),
        ("LIVE_TRADING_ENABLED", "true"),
        ("BINANCE_API_KEY", "private"),
        ("BINANCE_LIVE_API_SECRET", "private"),
    ],
)
def test_live_and_mixed_context_reject_before_keys(monkeypatch, capsys, context, value):
    monkeypatch.setenv(context, value)
    monkeypatch.setattr(m, "credentials", lambda **kwargs: pytest.fail("No credentials"))
    with pytest.raises(SystemExit):
        m.main()
    output = capsys.readouterr().out
    assert json.loads(output)["stop_reason"] == "LIVE_OR_MIXED_CONTEXT_FORBIDDEN"
    assert "private" not in output


def test_unexpected_private_failure_is_redacted(monkeypatch, capsys):
    def fail():
        raise RuntimeError("SYNTHETIC_SECRET_UNEXPECTED")

    monkeypatch.setattr(m, "verify_installed_sources", fail)
    with pytest.raises(SystemExit):
        m.main()
    assert "SYNTHETIC_SECRET" not in capsys.readouterr().out


def test_fixed_host_signed_get_has_no_request_body_and_does_not_follow_redirects(monkeypatch):
    c, _, _, _ = setup()
    signed = prepare_signed_query(
        {}, api_key=c.credentials.api_key, secret=c.credentials.secret, timestamp_ms=c.clock_ms()
    )
    requests = []

    class Response:
        status = 200
        headers = {
            "x-mbx-used-weight-1m": "4",
            "X-MBX-ORDER-COUNT-10S": "0",
            "X-MBX-ORDER-COUNT-1M": "0",
        }

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, n):
            assert n == 4194305
            return b"[]"

    class Opener:
        def open(self, req, timeout):
            requests.append(req)
            assert timeout == 5
            return Response()

    def build(handler):
        assert type(handler).__name__ == "NoRedirect"
        return Opener()

    monkeypatch.setattr(m, "build_opener", build)
    r = m.admission_read_transport("GET", m.ORDER_USAGE_PATH, signed.query, signed.api_key)
    req = requests[0]
    assert req.get_method() == "GET" and req.data is None
    assert req.full_url.startswith("https://demo-fapi.binance.com/fapi/v1/rateLimit/order?")
    assert r.headers["x-mbx-order-count-10s"] == "0"


@pytest.mark.parametrize("mode", ["body", "header", "missing"])
def test_main_reports_only_evidence_and_never_approves(monkeypatch, capsys, mode):
    source, mon, calls, _ = setup(mode)
    real = m.AdmissionReadClient
    monkeypatch.setattr(m, "admission_read_transport", mon.sender)
    monkeypatch.setattr(m, "credentials", lambda **kwargs: source.credentials)
    monkeypatch.setattr(
        m,
        "AdmissionReadClient",
        lambda cfg, key, **kwargs: real(cfg, key, clock_ms=source.clock_ms, **kwargs),
    )
    if mode == "missing":
        with pytest.raises(SystemExit) as status:
            m.main()
        assert status.value.code == 2
    else:
        m.main()
    output = capsys.readouterr().out
    report = json.loads(output)
    assert not report["execution_approved"] and report["http_attempts"] == 4
    assert len(calls) == 4 and "synthetic-secret" not in output and "signature" not in output
    if mode == "missing":
        assert report["order_quota_evidence"] == "LIMITS_VERIFIED_USAGE_UNAVAILABLE"


def test_standalone_bundle_has_same_policy_and_runs_against_installed_sdk(tmp_path, monkeypatch):
    import ast
    import importlib.util
    import sys
    from pathlib import Path

    from scripts.build_tp_admission_review_bundle import build_bundle

    root = Path(__file__).parents[2]
    text = build_bundle(root)
    expected = ast.parse((root / "services/execution/demo_order_admission.py").read_text()).body
    expected += [
        node
        for node in ast.parse((root / "scripts/review_demo_tp_admission.py").read_text()).body
        if not (
            isinstance(node, ast.ImportFrom)
            and node.module == "services.execution.demo_order_admission"
        )
    ]
    assert ast.dump(ast.parse(text)) == ast.dump(ast.Module(body=expected, type_ignores=[]))
    path = tmp_path / "review_tp_entry_admission.py"
    path.write_text(text)
    spec = importlib.util.spec_from_file_location("admission_bundle_under_test", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module.verify_installed_sources()
    c, mon, calls, _ = setup("header")
    reader = module.AdmissionReadClient(c.settings, c.credentials, send=mon, clock_ms=c.clock_ms)
    report = module.review_admission(reader, mon)
    assert (
        report["order_quota_evidence"] == "SAMPLED_HEADROOM_FITS"
        and not report["execution_approved"]
    )
    assert len(calls) == 4


@pytest.mark.parametrize(
    "host",
    [
        "https://fapi.binance.com",
        "http://demo-fapi.binance.com",
        "https://demo-fapi.binance.com.evil.invalid",
    ],
)
def test_fixed_demo_origin_cannot_be_overridden(monkeypatch, host):
    monkeypatch.setattr(m, "REST_HOST", host)
    monkeypatch.setattr(m, "build_opener", lambda *args: pytest.fail("No network for wrong host"))
    with pytest.raises(m.EvidenceStop, match="DEMO_HOST_REQUIRED"):
        m.admission_read_transport("GET", "/fapi/v1/time", SecretStr(""), None)


@pytest.mark.parametrize(
    "query",
    [
        "",
        "timestamp=1&recvWindow=5000&signature=abc",
        "timestamp=1&timestamp=2&recvWindow=5000&signature=" + "0" * 64,
        "timestamp=1&recvWindow=5000&signature=" + "0" * 64 + "&symbol=BTCUSDT",
    ],
)
def test_signed_limit_query_rejects_unexpected_or_duplicate_fields(monkeypatch, query):
    monkeypatch.setattr(
        m, "build_opener", lambda *args: pytest.fail("No network for invalid scope")
    )
    with pytest.raises(m.EvidenceStop, match="SIGNED_READ_SCOPE_FORBIDDEN"):
        m.admission_read_transport(
            "GET", m.ORDER_USAGE_PATH, SecretStr(query), SecretStr("synthetic-key")
        )


def test_source_pin_mismatch_blocks_before_credentials(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        m, "credentials", lambda **kwargs: pytest.fail("No credentials for unknown runtime")
    )
    with pytest.raises(SystemExit):
        m.main()
    assert json.loads(capsys.readouterr().out)["stop_reason"] == "INSTALLED_SOURCE_MISMATCH"


def test_stale_signing_clock_blocks_before_private_request():
    c, mon, calls, _ = setup()
    c.sync_clock()
    mon.enable(limits())
    original = c.clock_ms()
    c.clock_ms = lambda: original + 5001
    with pytest.raises(m.EvidenceStop, match="ADMISSION_CLOCK_STALE"):
        c.query_order_usage()
    assert calls == ["/fapi/v1/time"]


def test_standalone_version_mismatch_blocks_before_sdk_import(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    from scripts.build_tp_admission_review_bundle import build_bundle

    path = tmp_path / "review_tp_entry_admission.py"
    path.write_text(build_bundle(Path(__file__).parents[2]))
    result = subprocess.run(
        [sys.executable, str(path)], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert result.returncode == 2 and result.stderr == ""
    report = json.loads(result.stdout)
    assert report["stop_reason"] == "INSTALLED_SOURCE_MISMATCH" and not report["execution_approved"]
