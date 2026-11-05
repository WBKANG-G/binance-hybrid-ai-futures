import json

import pytest

import inspect_alert_worker as worker


def api_body():
    return {
        "status": "READ_ONLY_ALERT_STATUS",
        "is_stale": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
        "worker_liveness_checked": False,
        "inbox_confirmation_available": False,
        "confirmation_scope": "RECEIVER_ACK_ONLY",
        "email": {"state": "CONFIGURED_PRIVATE_RAM"},
        "queue": {"pending": 0, "critical_pending": 0, "test_pending": 0},
        "password": "fixture-secret-must-not-output",
    }


@pytest.mark.parametrize(
    "raw,now",
    [
        (b"nan", 100),
        (b"inf", 100),
        (b"101", 100),
        (b"70", 100),
        (b"x" * 65, 100),
        (b"", 100),
    ],
)
def test_invalid_or_old_heartbeat_is_never_recent(tmp_path, raw, now):
    path = tmp_path / "heartbeat"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        worker.heartbeat(path, now)


def test_missing_heartbeat_is_unavailable(tmp_path):
    with pytest.raises(OSError):
        worker.heartbeat(tmp_path / "absent", 100)


def observe_case(tmp_path, *, advance=True, before="CONFIGURED_PRIVATE_RAM", after=None):
    path = tmp_path / "heartbeat"
    path.write_text("99.5")

    def pause(seconds):
        assert seconds == 3
        if advance:
            path.write_text("102.5")

    clocks = iter([100, 103])
    states = iter([before, after or before])
    return worker.observe(
        path=path,
        clock=lambda: next(clocks),
        pause=pause,
        email_status=lambda: next(states),
        api_status=lambda: worker.public_view(api_body()),
    )


def test_progress_and_shared_config_is_only_limited_evidence(tmp_path):
    report = observe_case(tmp_path)
    assert report["worker_observation"] == "HEARTBEAT_ADVANCING_CONFIG_VISIBLE"
    assert report["first_heartbeat_age_ms"] == report["last_heartbeat_age_ms"] == 500
    assert report["worker_api_config_consistent"] and report["heartbeat_advancing"]
    assert not report["delivery_readiness_verified"] and not report["smtp_authentication_checked"]
    assert not report["inbox_checked"] and not report["new_mail_created"]
    assert not report["orders_enabled"] and not report["live_trading_enabled"]
    assert "fixture-secret" not in json.dumps(report)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"advance": False},
        {"before": "NOT_CONFIGURED"},
        {"before": "CONFIGURED_PRIVATE_RAM", "after": "BLOCKED_EXPIRED_OR_INVALID"},
    ],
)
def test_recent_but_stalled_or_config_changed_requires_review(tmp_path, kwargs):
    assert observe_case(tmp_path, **kwargs)["worker_observation"] == "REVIEW_REQUIRED_KEEP_PAUSED"


@pytest.mark.parametrize(
    "field",
    [
        "orders_enabled",
        "live_trading_enabled",
        "worker_liveness_checked",
        "inbox_confirmation_available",
    ],
)
def test_unsafe_or_unsupported_api_assertions_rejected(field):
    body = api_body()
    body[field] = True
    with pytest.raises(ValueError):
        worker.public_view(body)


@pytest.mark.parametrize("count", [-1, True, "0", None])
def test_invalid_queue_cannot_be_claimed_empty(count):
    body = api_body()
    body["queue"]["pending"] = count
    with pytest.raises(ValueError):
        worker.public_view(body)


def test_api_unavailable_or_inconsistent_counts_rejected():
    body = api_body()
    body["status"] = "UNAVAILABLE"
    with pytest.raises(ValueError):
        worker.public_view(body)
    body = api_body()
    body["queue"]["critical_pending"] = 1
    with pytest.raises(ValueError):
        worker.public_view(body)


def test_live_context_blocks_before_any_file_or_network(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    with pytest.raises(ValueError, match="DEMO_ONLY"):
        worker.observe(clock=lambda: pytest.fail("must not read"))


def test_cli_redacts_exception_instead_of_logging_private_details(monkeypatch, capsys):
    def fail():
        raise ValueError("fixture-secret@naver.com")

    monkeypatch.setattr(worker, "observe", fail)
    with pytest.raises(SystemExit) as error:
        worker.main()
    assert error.value.code == 2
    result = capsys.readouterr().out
    assert "fixture-secret" not in result and "UNAVAILABLE_KEEP_PAUSED" in result


def test_api_reader_is_fixed_get_without_proxy_redirect_or_private_output(monkeypatch):
    from urllib.request import HTTPRedirectHandler, ProxyHandler, Request

    seen = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, bound):
            assert bound == 65537
            return json.dumps(api_body()).encode()

    class Opener:
        def open(self, request, timeout):
            assert isinstance(request, Request)
            assert request.full_url == "http://api:8000/api/v1/demo/alerts/status"
            assert request.get_method() == "GET" and request.data is None
            assert timeout == 8
            seen.append("GET")
            return Response()

    def build(*handlers):
        assert isinstance(handlers[0], ProxyHandler) and handlers[0].proxies == {}
        redirect = handlers[1]
        assert isinstance(redirect, HTTPRedirectHandler)
        assert redirect.redirect_request(None, None, None, None, None, None) is None
        return Opener()

    monkeypatch.setattr(worker, "build_opener", build)
    result = worker.read_api()
    assert seen == ["GET"] and "fixture-secret" not in json.dumps(result)


@pytest.mark.parametrize(
    "status,raw", [(503, b"fixture-secret"), (200, b"x" * 65537), (200, b"fixture-secret")]
)
def test_invalid_api_transport_is_not_reported_ready(monkeypatch, status, raw):
    class Response:
        def __enter__(self):
            self.status = status
            return self

        def __exit__(self, *args):
            pass

        def read(self, bound):
            return raw

    class Opener:
        def open(self, request, timeout):
            return Response()

    monkeypatch.setattr(worker, "build_opener", lambda *args: Opener())
    with pytest.raises(ValueError):
        worker.read_api()
