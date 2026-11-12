"""One-shot actual-journal probe with synthetic network responses only."""

# ruff: noqa: F811
import json
from uuid import uuid5

import pytest
from sqlalchemy import select, update

from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo import accounts
from packages.persistence.demo_order_quota import LocalQuotaStopped
from scripts import probe_demo_accounted_reads as probe
from scripts import review_demo_read_code_install as fingerprint
from scripts import review_demo_read_deployment as deployment
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_read_audit_bootstrap import ready  # noqa: F401
from tests.integration.test_read_code_install_guard import installation  # noqa: F401
from tests.integration.test_read_deployment_review import candidate  # noqa: F401
from tests.integration.test_runtime_read_accounting import financial, settings
from tests.integration.test_shared_weight_checkpoint import anchored  # noqa: F401


def invoke(case, sender, **kwargs):
    a, _, _, clock = case
    values = dict(apply=True, clocks=clock, sender=sender)
    values.update(kwargs)
    return probe.run_probe(
        a[1].engine,
        deployment.STORAGE,
        a[0].scope,
        a[4],
        a[1].installation_id(),
        "DEMO-test",
        settings(a),
        values.pop("credential_loader", lambda: a[7].client.credentials),
        fingerprint,
        **values,
    )


def marker(case):
    return deployment.STORAGE.parent / (
        "accounted-read-probe-" + str(uuid5(case[0][4], probe.POLICY))
    )


def sender(case, seen):
    def send(method, path, query, key):
        resources, actions = probe.resource_rows(case[0][1].engine, case[0][0].scope)
        assert resources
        assert actions[max(actions)]["kind"] == "RESERVE"
        assert actions[max(actions)]["path"] == path
        seen.append((method, path))
        body = (
            dict(serverTime=int(case[0][2].utc().timestamp() * 1000))
            if path.endswith("/time")
            else dict(dualSidePosition=False, multiAssetsMargin=False)
        )
        return RESTResponse(
            200,
            json.dumps(body).encode(),
            {"x-mbx-used-weight-1m": str(1 if len(seen) == 1 else 6)},
        )

    return send


def test_two_gets_persist_reservation_before_wire_and_preserve_financials(installation):
    before = financial(installation[0])
    old, _ = probe.resource_rows(installation[0][1].engine, installation[0][0].scope)
    backup = deployment.STORAGE.parent / "old-receipt"
    backup.write_bytes(b"immutable historical receipt")
    backup.chmod(0o600)
    seen = []
    result = invoke(installation, sender(installation, seen))
    assert seen == [("GET", "/fapi/v1/time"), ("GET", "/fapi/v1/accountConfig")]
    assert result["http_attempts"] == result["completed_reads"] == 2
    assert result["modeled_weight_charged"] == 6
    assert result["audit_events_added"] == result["resource_checkpoints_added"] == 4
    assert result["financial_ledger_unchanged_verified"]
    assert not result["orders_enabled"] and not result["execution_approved"]
    assert (
        not result["exchange_account_identity_verified"]
        and not result["positions_or_orders_checked"]
    )
    assert financial(installation[0]) == before
    new, _ = probe.resource_rows(installation[0][1].engine, installation[0][0].scope)
    assert all(new[k] == v for k, v in old.items()) and len(new) == len(old) + 8
    assert backup.read_bytes() == b"immutable historical receipt"
    assert marker(installation).stat().st_mode & 0o777 == 0o700
    for path in marker(installation).iterdir():
        assert path.stat().st_mode & 0o777 == 0o600
        assert "signature" not in path.read_text() and "SYNTHETIC_KEY" not in path.read_text()
    with pytest.raises(probe.ProbeBlocked, match="ALREADY_ATTEMPTED"):
        invoke(installation, lambda *a: pytest.fail("third send"))
    assert len(seen) == 2


def test_credentials_missing_leaves_no_intent_or_charge(installation):
    before = probe.resource_rows(installation[0][1].engine, installation[0][0].scope)

    def missing():
        raise RuntimeError("PRIVATE_SECRET")

    with pytest.raises(probe.ProbeBlocked, match="CREDENTIAL_SESSION_UNAVAILABLE"):
        invoke(installation, lambda *a: pytest.fail("wire"), credential_loader=missing)
    assert not marker(installation).exists()
    assert probe.resource_rows(installation[0][1].engine, installation[0][0].scope) == before


@pytest.mark.parametrize("failure", ["unknown", "rate", "header", "mode"])
def test_failed_probe_keeps_intent_and_never_retries(installation, failure):
    seen = []
    good = sender(installation, seen)
    state = {}

    def fail(*args):
        if failure == "unknown":
            seen.append(args[:2])
            raise OSError("PRIVATE_NETWORK_ERROR")
        response = good(*args)
        if failure == "rate":
            return RESTResponse(429, b"{}", {"x-mbx-used-weight-1m": "1"})
        if failure == "header":
            return RESTResponse(200, response.body, {})
        if len(seen) == 2:
            return RESTResponse(
                200, b'{"dualSidePosition":true,"multiAssetsMargin":false}', response.headers
            )
        return response

    with pytest.raises((probe.ProbeBlocked, LocalQuotaStopped)):
        invoke(installation, fail, state=state)
    assert marker(installation).joinpath("intent.json").exists()
    assert not marker(installation).joinpath("complete.json").exists()
    assert state["transport"]["http_attempts"] == (2 if failure == "mode" else 1)
    assert state["transport"]["modeled_weight_charged"] == (6 if failure == "mode" else 1)
    with pytest.raises(probe.ProbeBlocked, match="ALREADY_ATTEMPTED"):
        invoke(installation, lambda *a: pytest.fail("retry"))


def test_singleton_blocks_nested_invocation_before_network(installation):
    seen = []

    def credentials():
        with pytest.raises(probe.ProbeBlocked, match="ALREADY_RUNNING"):
            invoke(installation, lambda *a: pytest.fail("nested wire"))
        return installation[0][7].client.credentials

    invoke(installation, sender(installation, seen), credential_loader=credentials)
    assert len(seen) == 2


def test_financial_change_after_reads_is_detected(installation, monkeypatch):
    real = probe.protected_snapshot
    calls = []

    def changed(*args):
        calls.append(1)
        if len(calls) == 2:
            with installation[0][1].engine.begin() as c:
                state = c.execute(select(accounts.c.state)).scalar_one()
                state["high_water"] = "7777"
                c.execute(update(accounts).values(state=state))
        return real(*args)

    monkeypatch.setattr(probe, "protected_snapshot", changed)
    with pytest.raises(probe.ProbeBlocked, match="PROTECTED_STATE_CHANGED"):
        invoke(installation, sender(installation, []))
    assert not marker(installation).joinpath("complete.json").exists()


def test_no_opt_in_refuses_before_any_access():
    with pytest.raises(probe.ProbeBlocked, match="EXPLICIT_RUN"):
        probe.run_probe(None, None, None, None, None, None, None, None, None)


def test_live_environment_refuses_before_intent(installation, monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    with pytest.raises(LocalQuotaStopped, match="LIVE_OR_MIXED"):
        invoke(installation, lambda *a: pytest.fail("wire"))
    assert not marker(installation).exists()


def test_default_installed_wire_uses_accounting_and_no_redirect_opener(installation, monkeypatch):
    from email.message import Message
    from urllib.parse import urlsplit

    from services.execution import demo_runtime_read_transport as wire

    seen = []

    class Response:
        status = 200

        def __init__(self, response):
            self.body = response.body
            self.headers = Message()
            for k, v in response.headers.items():
                self.headers[k] = v

        def read(self, limit):
            assert limit == 4_194_305
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Opener:
        def open(self, request, timeout):
            assert request.get_method() == "GET" and timeout == 5
            target = urlsplit(request.full_url)
            assert target.scheme == "https" and target.netloc == "demo-fapi.binance.com"
            if target.path.endswith("/accountConfig"):
                assert "signature=" in target.query
                assert request.get_header("X-mbx-apikey") is not None
            return Response(sender(installation, seen)("GET", target.path, None, None))

    def opener(handler):
        assert isinstance(handler, wire.NoRedirect)
        return Opener()

    monkeypatch.setattr(wire, "build_opener", opener)
    result = invoke(installation, None)
    assert len(seen) == 2 and not result["synthetic_sender_configured"]


def test_bad_clock_sample_stops_before_signed_call(installation):
    from integrations.binance_usdm.demo import DemoFailure

    seen = []
    good = sender(installation, seen)

    def shifted(*args):
        response = good(*args)
        return RESTResponse(200, b'{"serverTime":1}', response.headers)

    with pytest.raises(DemoFailure, match="clock offset"):
        invoke(installation, shifted)
    assert len(seen) == 1 and not marker(installation).joinpath("complete.json").exists()


def test_source_hash_or_symlink_refused(tmp_path):
    import hashlib

    source = tmp_path / "source.py"
    source.write_bytes(b"original")
    pins = {"source.py": hashlib.sha256(b"original").hexdigest()}
    probe.verify_files(tmp_path, pins)
    source.write_bytes(b"changed")
    with pytest.raises(probe.ProbeBlocked, match="SOURCE_CHANGED"):
        probe.verify_files(tmp_path, pins)
    source.unlink()
    source.symlink_to("/etc/passwd")
    with pytest.raises(probe.ProbeBlocked, match="SOURCE_MISSING"):
        probe.verify_files(tmp_path, pins)
