"""Real anchored journals and synthetic HTTP; no account/order writer session."""

# ruff: noqa: F811
import json
import sqlite3
from email.message import Message
from urllib.parse import urlsplit

import pytest
from pydantic import SecretStr
from sqlalchemy import event, select

from integrations.binance_usdm.demo import DemoClient
from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo_order_quota import LocalQuotaStopped
from services.execution import demo_runtime_read_transport as wire
from services.execution.demo_runtime_read_transport import AccountedDemoReadTransport as Reader
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_shared_weight_checkpoint import anchored  # noqa: F401
from tests.unit.test_quota_startup_read_only import files


def settings(a):
    return a[-1].client.settings.model_copy(
        update={"demo_trading_enabled": False, "margin_mode": "ISOLATED", "symbols": ("BTCUSDT",)}
    )


def read(a, sender=None, **kwargs):
    return Reader(
        settings(a),
        a[0],
        enable_reads=True,
        sender=sender or (lambda *a: RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "10"})),
        monotonic=a[2].mono,
        **kwargs,
    )


def signed(path="/fapi/v3/account"):
    return (
        "GET",
        path,
        SecretStr("timestamp=1&recvWindow=5000&signature=" + "a" * 64),
        SecretStr("SYNTHETIC_KEY"),
    )


def journal(a):
    with sqlite3.connect(a[0].path) as c:
        return c.execute("SELECT total,stopped FROM meta").fetchone(), c.execute(
            "SELECT weight,done,floor FROM attempts"
        ).fetchall()


def financial(a):
    store = a[-2]
    from packages.persistence.demo import accounts, events, outbox

    with store.engine.connect() as c:
        return [
            c.execute(select(t).order_by(*t.primary_key.columns)).all()
            for t in (accounts, events, outbox)
        ]


def test_disabled_precedes_access_to_provider_settings_clock_or_wire():
    with pytest.raises(LocalQuotaStopped, match="READ_TRANSPORT_DISABLED"):
        Reader(None, None, monotonic=lambda: pytest.fail("clock must not run"))


def test_signed_read_charges_before_send_and_checkpoints_completion_without_financial_writes(
    anchored,
):
    a = anchored
    before = financial(a)
    seen = []

    def sender(*args):
        state, rows = journal(a)
        assert state == (5, 0) and rows == [(5, None, None)]
        seen.append(args[:2])
        return RESTResponse(200, b'{"ok":true}', {"X-MBX-USED-WEIGHT-1M": "100"})

    reader = read(a, sender)
    response = reader(*signed())
    assert response.body == b'{"ok":true}' and seen == [("GET", "/fapi/v3/account")]
    assert journal(a)[0] == (5, 0) and journal(a)[1][0][2] == 100
    assert a[0].view()["pending_attempts"] == 0 and a[0].view()["pressure"] == 100
    assert financial(a) == before
    metrics = reader.diagnostics()
    assert metrics["http_attempts"] == metrics["completed_reads"] == 1
    assert metrics["modeled_weight_charged"] == 5 and not metrics["execution_approved"]
    assert metrics["order_writes"] == 0 and not metrics["account_order_usage_verified"]
    assert (
        not metrics["actual_ip_capacity_reserved"]
        and not metrics["request_cost_upper_bounds_proven"]
    )


@pytest.mark.parametrize(
    "method,path,query,key",
    [
        ("POST", "/fapi/v1/order", "", None),
        ("DELETE", "/fapi/v1/order", "", None),
        ("POST", "/fapi/v1/algoOrder", "", None),
        ("DELETE", "/fapi/v1/algoOrder", "", None),
        ("POST", "/fapi/v1/listenKey", "", None),
        ("PUT", "/fapi/v1/listenKey", "", None),
        (
            "GET",
            "/fapi/v3/positionRisk",
            "symbol=BTCUSDT&timestamp=1&recvWindow=5000&signature=" + "a" * 64,
            "key",
        ),
        ("GET", "/fapi/v1/time", "unrecognized=1", None),
        ("GET", "https://fapi.binance.com/fapi/v1/time", "", None),
        ("GET", "/fapi/v1/time", "timestamp=1&timestamp=2", None),
    ],
)
def test_forbidden_requests_stop_before_any_shared_charge_or_wire(
    anchored, method, path, query, key
):
    a = anchored
    before = files(a[3])
    reader = read(a, lambda *a: pytest.fail("wire forbidden"))
    with pytest.raises(LocalQuotaStopped):
        reader(method, path, SecretStr(query), SecretStr(key) if key else None)
    assert files(a[3]) == before and reader.diagnostics()["http_attempts"] == 0


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "invalid",
        "duplicate",
        "rate429",
        "rate418",
        "unknown",
        "bad_response",
        "oversize",
    ],
)
def test_ambiguous_or_limited_response_retains_charge_and_blocks_every_other_reader(anchored, kind):
    a = anchored
    calls = []

    def sender(*args):
        calls.append(True)
        if kind == "unknown":
            raise RuntimeError("PRIVATE_NETWORK_BODY")
        if kind == "bad_response":
            return None
        headers = {"x-mbx-used-weight-1m": "10"}
        if kind == "missing":
            headers = {}
        if kind == "invalid":
            headers = {"x-mbx-used-weight-1m": "-1"}
        if kind == "duplicate":
            headers["X-MBX-USED-WEIGHT-1M"] = "0"
        status = 429 if kind == "rate429" else 418 if kind == "rate418" else 200
        return RESTResponse(status, b"x" * (4_194_305 if kind == "oversize" else 2), headers)

    reader = read(a, sender)
    with pytest.raises(LocalQuotaStopped) as error:
        reader(*signed())
    assert "PRIVATE" not in str(error.value)
    state, rows = journal(a)
    assert state == (5, 1) and len(rows) == 1 and rows[0][1] is None
    other = read(a, lambda *args: pytest.fail("shared stop must precede network"))
    with pytest.raises(LocalQuotaStopped):
        other(*signed())
    with pytest.raises(LocalQuotaStopped):
        reader(*signed())
    assert len(calls) == 1


def test_non_authoritative_quote_header_never_grants_weight_credit(anchored):
    a = anchored
    reader = read(a, lambda *a: RESTResponse(200, b"{}", {}))
    reader("GET", "/fapi/v1/ticker/bookTicker", SecretStr("symbol=BTCUSDT"), None)
    assert journal(a)[0] == (2, 0) and a[0].view()["pressure"] == 2
    assert reader.diagnostics()["completed_reads"] == 1


def test_http_error_has_no_retry_and_retains_completed_charge(anchored):
    a = anchored
    calls = []

    def fail(*args):
        calls.append(True)
        return RESTResponse(503, b"PRIVATE_BODY", {"x-mbx-used-weight-1m": "10"})

    reader = read(a, fail)
    with pytest.raises(LocalQuotaStopped, match="HTTP_ERROR_NO_RETRY"):
        reader(*signed())
    with pytest.raises(LocalQuotaStopped):
        reader(*signed())
    assert len(calls) == 1 and journal(a)[0] == (5, 0) and journal(a)[1][0][1] is not None


def test_close_does_not_refund_or_reopen_owned_budget(anchored):
    a = anchored
    reader = read(a)
    reader(*signed())
    before = files(a[3])
    reader.close()
    reader.close()
    with pytest.raises(LocalQuotaStopped, match="CLOSED_OR_STOPPED"):
        reader(*signed())
    assert files(a[3]) == before


@pytest.mark.parametrize(
    "fault", ["live", "host", "credentials", "binding", "cost", "bounds", "clock_backwards"]
)
def test_context_or_binding_drift_is_rejected_before_shared_access(anchored, monkeypatch, fault):
    from integrations.binance_usdm import demo

    a = anchored
    reader = read(a)
    before = files(a[3])
    if fault == "live":
        monkeypatch.setenv("TRADING_MODE", "LIVE")
    elif fault == "host":
        monkeypatch.setattr(demo, "REST_HOST", "https://fapi.binance.com")
    elif fault == "credentials":
        monkeypatch.setenv("BINANCE_API_KEY", "PRIVATE_GENERIC")
    elif fault == "binding":
        a[0].scope = "DEMO-other"
    elif fault == "cost":
        reader._costs[("GET", "/fapi/v3/account")] = 1
    elif fault == "bounds":
        reader._max_requests = 100
    else:
        a[2].seconds -= 1
    with pytest.raises(LocalQuotaStopped):
        reader(*signed())
    assert files(a[3]) == before and reader.diagnostics()["http_attempts"] == 0


def test_request_limit_and_deadline_do_not_reserve_extra_credit(anchored):
    a = anchored
    reader = read(a, max_requests=1)
    reader(*signed())
    before = files(a[3])
    with pytest.raises(LocalQuotaStopped, match="REQUEST_LIMIT"):
        reader(*signed())
    assert files(a[3]) == before
    reader = read(a, max_seconds=1)
    a[2].seconds += 1
    before = files(a[3])
    with pytest.raises(LocalQuotaStopped, match="DEADLINE"):
        reader(*signed())
    assert files(a[3]) == before


def test_checkpoint_failure_before_reserve_returns_never_sends(anchored):
    a = anchored
    calls = []
    reader = read(a, lambda *args: calls.append(True))

    def fail(connection):
        raise RuntimeError("PRIVATE_PG_FAILURE")

    event.listen(a[1].engine, "commit", fail)
    try:
        with pytest.raises(LocalQuotaStopped, match="READ_TRANSPORT_ADMISSION_FAILED"):
            reader(*signed())
    finally:
        event.remove(a[1].engine, "commit", fail)
    assert not calls and journal(a)[1][0][1] is None
    with pytest.raises(LocalQuotaStopped, match="SHARED_CHECKPOINT_MISMATCH"):
        a[0].view()


def test_default_sdk_network_boundary_is_demo_get_only_with_durable_accounting(
    anchored, monkeypatch
):

    a = anchored
    requests = []

    class Response:
        status = 200
        headers = Message()
        headers["x-mbx-used-weight-1m"] = "20"

        def read(self, max_bytes):
            return b'{"ok":true}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            assert urlsplit(request.full_url).netloc == "demo-fapi.binance.com"
            assert request.method == "GET" and timeout == 5
            assert journal(a)[1][0][1] is None
            return Response()

    monkeypatch.setattr(wire, "build_opener", lambda *args: Opener())
    reader = Reader(settings(a), a[0], enable_reads=True, monotonic=a[2].mono)
    response = reader(*signed())
    assert response.body == b'{"ok":true}' and len(requests) == 1 and journal(a)[0] == (5, 0)


def test_lowest_http_boundary_requires_a_single_accounted_attempt(monkeypatch):
    monkeypatch.setattr(
        wire, "build_opener", lambda *a: pytest.fail("unaccounted network forbidden")
    )
    with pytest.raises(LocalQuotaStopped, match="ACCOUNTED_ATTEMPT_REQUIRED"):
        wire._demo_get_http(*signed())


def test_duplicate_raw_weight_headers_do_not_survive_network_filtering(anchored, monkeypatch):
    a = anchored

    class Response:
        status = 200
        headers = Message()
        headers["X-MBX-USED-WEIGHT-1M"] = "5000"
        headers["x-mbx-used-weight-1m"] = "0"

        def read(self, limit):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Opener:
        def open(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(wire, "build_opener", lambda *a: Opener())
    reader = Reader(settings(a), a[0], enable_reads=True, monotonic=a[2].mono)
    with pytest.raises(LocalQuotaStopped, match="RESPONSE_UNKNOWN"):
        reader(*signed())
    assert journal(a)[0] == (5, 1) and journal(a)[1][0][1] is None


def test_expired_deadline_after_reserve_finishes_only_proven_unsent_attempt(anchored, monkeypatch):
    a = anchored
    reserve = a[0].reserve

    def delayed(*args):
        token = reserve(*args)
        a[2].seconds += 1
        return token

    monkeypatch.setattr(a[0], "reserve", delayed)
    reader = read(a, lambda *a: pytest.fail("deadline forbids wire"), max_seconds=1)
    with pytest.raises(LocalQuotaStopped, match="DEADLINE"):
        reader(*signed())
    assert journal(a)[0] == (5, 0) and journal(a)[1][0][1] is not None
    assert a[0].view()["pressure"] == 5
    a[2].seconds += 60.002
    assert a[0].view()["pressure"] == 0


def test_separate_readers_cannot_spend_the_same_shared_headroom(anchored):
    from tests.integration.test_shared_weight_checkpoint import reopen

    a = anchored
    token = a[0].reserve(5000, "RESCUE")
    a[0].finish(token, used_weight=5000, status=200)
    other = reopen(a)
    calls = []

    def sender(*args):
        calls.append(True)
        return RESTResponse(200, b"[]", {"x-mbx-used-weight-1m": "0"})

    first = read(a, sender)
    second = Reader(settings(a), other, enable_reads=True, sender=sender, monotonic=a[2].mono)
    first(*signed("/fapi/v1/openOrders"))
    with pytest.raises(LocalQuotaStopped, match="SHARED_WEIGHT_CAPACITY"):
        second(*signed("/fapi/v1/openOrders"))
    assert len(calls) == 1 and journal(a)[0] == (5040, 0) and a[0].view()["pressure"] == 5040


def test_existing_sdk_clock_and_signed_get_use_accounted_read_transport(anchored):
    a = anchored
    source = a[-1].client
    paths = []

    def sender(method, path, query, key):
        paths.append(path)
        if path == "/fapi/v1/time":
            body = {"serverTime": source.clock_ms()}
        else:
            assert key is source.credentials.api_key
            body = {"availableBalance": "5000"}
        return RESTResponse(
            200, json.dumps(body).encode(), {"x-mbx-used-weight-1m": str(len(paths))}
        )

    reader = read(a, sender)
    client = DemoClient(settings(a), source.credentials, send=reader, clock_ms=source.clock_ms)
    client.sync_clock()
    assert client.request("GET", "/fapi/v3/account", {}) == {"availableBalance": "5000"}
    assert paths == ["/fapi/v1/time", "/fapi/v3/account"] and journal(a)[0] == (6, 0)
    assert reader.diagnostics()["http_attempts"] == 2


def test_completion_checkpoint_failure_does_not_return_a_response_or_refund_credit(anchored):
    a = anchored
    calls = []

    def sender(*args):
        calls.append(True)
        return RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "10"})

    reader = read(a, sender)
    commits = [0]

    def fail_completion(connection):
        commits[0] += 1
        if commits[0] == 2:
            raise RuntimeError("PRIVATE_COMPLETION_FAILURE")

    event.listen(a[1].engine, "commit", fail_completion)
    try:
        with pytest.raises(LocalQuotaStopped, match="CHECKPOINT_OR_STOP_FAILED"):
            reader(*signed())
    finally:
        event.remove(a[1].engine, "commit", fail_completion)
    assert len(calls) == 1 and journal(a)[0][0] == 5
    with pytest.raises(LocalQuotaStopped, match="SHARED_CHECKPOINT_MISMATCH"):
        a[0].view()
    with pytest.raises(LocalQuotaStopped):
        reader(*signed())
    assert reader.diagnostics()["completed_reads"] == 0 and len(calls) == 1


def test_successful_but_late_response_is_charged_and_not_handed_out(anchored):
    a = anchored

    def late(*args):
        a[2].seconds += 1
        return RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "10"})

    reader = read(a, late, max_seconds=1)
    with pytest.raises(LocalQuotaStopped, match="DEADLINE"):
        reader(*signed())
    assert journal(a)[0] == (5, 0) and journal(a)[1][0][1] is not None
    assert reader.diagnostics()["completed_reads"] == 0 and reader.diagnostics()["stopped"]


def test_lower_network_boundary_cannot_be_reused_for_a_second_send(anchored, monkeypatch):
    a = anchored
    requests = []

    class Response:
        status = 200
        headers = Message()
        headers["x-mbx-used-weight-1m"] = "10"

        def read(self, limit):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            return Response()

    monkeypatch.setattr(wire, "build_opener", lambda *args: Opener())

    def repeated(*args):
        wire._demo_get_http(*args)
        return wire._demo_get_http(*args)

    reader = read(a, repeated)
    with pytest.raises(LocalQuotaStopped, match="RESPONSE_UNKNOWN"):
        reader(*signed())
    assert len(requests) == 1 and journal(a)[0] == (5, 1) and journal(a)[1][0][1] is None
