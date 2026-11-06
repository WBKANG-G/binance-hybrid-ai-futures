"""GET-only export quota and privacy protection with PostgreSQL and synthetic replies."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs

import pytest

from integrations.binance_usdm.demo import DemoUnknown
from packages.domain.contracts import RiskState
from services.execution.demo_trade_export import ExportBlocked, request_export, status_export
from tests.integration.test_demo import enter
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_cancelled_correction import event_count
from tests.integration.test_demo_isolated import configure


@pytest.fixture
def export_case(demo_system, tmp_path):  # noqa: F811
    system, _ = configure(demo_system)
    store, c, fake = system
    sig, order = enter(system)
    c.cancel(str(sig.signal_id))

    def lock(ledger):
        ledger.paused = True
        ledger.risk_state = RiskState.EMERGENCY

    store.change(c.lease, "TEST_LOCK_EXPORT", lock)
    fake.at += timedelta(days=2)
    c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": False})
    journal = tmp_path / "private" / "request.json"
    counts = {"create": 0, "status": 0}
    replies = {
        "create": {"downloadId": "job-123"},
        "status": {"downloadId": "job-123", "status": "processing"},
    }
    original = c.client.send

    def send(method, path, query, key):
        assert method == "GET"
        if path in {"/fapi/v1/trade/asyn", "/fapi/v1/trade/asyn/id"}:
            params = {k: v[0] for k, v in parse_qs(query.get_secret_value()).items()}
            action = "create" if path.endswith("asyn") else "status"
            counts[action] += 1
            fake.calls.append((method, path, {}))
            if action == "create":
                assert json.loads(journal.read_text())["phase"] == "INTENT_RECORDED"
                assert set(params) == {
                    "startTime",
                    "endTime",
                    "timestamp",
                    "recvWindow",
                    "signature",
                }
                assert int(params["startTime"]) < int(params["endTime"])
            else:
                assert params["downloadId"] == "job-123"
            reply = replies[action]
            if isinstance(reply, Exception):
                raise reply
            return fake.response(reply)
        return original(method, path, query, key)

    c.client.send = send
    c.client.sync_clock()
    return store, c, fake, journal, counts, replies, order


def request(case):
    store, c, _, journal, _, _, _ = case
    return request_export(store, c.client, store.read("DEMO-test"), "DEMO-test", journal)


def status(case):
    store, c, _, journal, _, _, _ = case
    return status_export(store, c.client, store.read("DEMO-test"), "DEMO-test", journal)


def test_intent_before_network_and_repeated_request_never_creates_another_job(export_case):
    store, c, fake, journal, counts, _, _ = export_case
    before = store.read("DEMO-test").model_dump_json()
    count = event_count(store)
    start = len(fake.calls)
    result = request(export_case)
    assert result["export"] == "JOB_REQUESTED_NOT_DOWNLOADED"
    assert result["download_id"] == "job-123"
    assert result["historical_completeness_verified"] is False
    assert result["recovery_eligible"] is False
    assert request(export_case)["export"] == "ALREADY_REQUESTED_NO_NEW_JOB"
    assert counts == {"create": 1, "status": 0}
    assert journal.stat().st_mode & 0o777 == 0o600
    assert journal.parent.stat().st_mode & 0o777 == 0o700
    assert store.read("DEMO-test").model_dump_json() == before
    assert event_count(store) == count
    assert all(method == "GET" for method, _, _ in fake.calls[start:])


def test_timeout_is_not_retried_even_when_called_again(export_case):
    *_, replies, _ = export_case
    replies["create"] = DemoUnknown("SYNTHETIC_PRIVATE_RESPONSE")
    with pytest.raises(ExportBlocked, match="EXPORT_REQUEST_UNRESOLVED_DO_NOT_REPEAT"):
        request(export_case)
    with pytest.raises(ExportBlocked, match="EXPORT_INTENT_EXISTS_DO_NOT_REPEAT"):
        request(export_case)
    assert export_case[4]["create"] == 1


@pytest.mark.parametrize(
    "reply",
    [
        {},
        [],
        {"downloadId": None},
        {"downloadId": True},
        {"downloadId": "../bad"},
        {"downloadId": "x" * 129},
    ],
)
def test_malformed_creation_reply_keeps_intent_without_retry(export_case, reply):
    export_case[5]["create"] = reply
    with pytest.raises(ExportBlocked):
        request(export_case)
    with pytest.raises(ExportBlocked, match="INTENT_EXISTS"):
        request(export_case)
    assert export_case[4]["create"] == 1


def test_receipt_write_failure_preserves_intent_and_prevents_regeneration(export_case, monkeypatch):
    import services.execution.demo_trade_export as module

    def broken(*args):
        raise OSError("SYNTHETIC_PRIVATE_FAILURE")

    monkeypatch.setattr(module.os, "replace", broken)
    with pytest.raises(ExportBlocked, match="KEEP_INTENT"):
        request(export_case)
    with pytest.raises(ExportBlocked, match="INTENT_EXISTS"):
        request(export_case)
    assert export_case[4]["create"] == 1
    assert json.loads(export_case[3].read_text())["download_id"] is None


def test_concurrent_requests_create_at_most_one_job(export_case):
    def run():
        try:
            return request(export_case)["export"]
        except ExportBlocked:
            return "BLOCKED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert "JOB_REQUESTED_NOT_DOWNLOADED" in results
    assert export_case[4]["create"] == 1


def test_status_checks_existing_job_once_without_creating_or_fetching_url(export_case):
    request(export_case)
    assert status(export_case)["export"] == "PROCESSING"
    assert export_case[4] == {"create": 1, "status": 1}
    secret_url = "https://download.binance.com/report.zip?token=SYNTHETIC_PRIVATE_LINK"
    export_case[5]["status"] = {"downloadId": "job-123", "status": "completed", "url": secret_url}
    result = status(export_case)
    assert result["export"] == "LINK_READY_NOT_DOWNLOADED"
    assert result["download_host"] == "download.binance.com"
    assert "SYNTHETIC_PRIVATE_LINK" not in json.dumps(result)
    assert json.loads(export_case[3].read_text())["download_url"] == secret_url
    assert export_case[3].stat().st_mode & 0o777 == 0o600
    assert export_case[4] == {"create": 1, "status": 2}


@pytest.mark.parametrize(
    "reply",
    [
        {"downloadId": "other", "status": "processing"},
        {"downloadId": "job-123", "status": "SYNTHETIC_PRIVATE_VALUE"},
        {"downloadId": "job-123", "status": "completed", "url": "http://binance.com/file"},
        {
            "downloadId": "job-123",
            "status": "completed",
            "url": "https://user:password@binance.com/file",
        },
        {"downloadId": "job-123", "status": "completed", "url": "https://binance.com:444/file"},
    ],
)
def test_invalid_status_or_link_is_never_exposed_or_followed(export_case, reply):
    request(export_case)
    export_case[5]["status"] = reply
    with pytest.raises(ExportBlocked):
        status(export_case)
    assert export_case[4] == {"create": 1, "status": 1}
    assert "download_url" not in json.loads(export_case[3].read_text())


@pytest.mark.parametrize(
    "problem", ["symlink_directory", "symlink_file", "broad_permissions", "corrupt_journal"]
)
def test_untrusted_local_journal_blocks_without_recreating(export_case, tmp_path, problem):
    journal = export_case[3]
    if problem == "symlink_directory":
        target = tmp_path / "target"
        target.mkdir(mode=0o700)
        journal.parent.symlink_to(target, target_is_directory=True)
    elif problem == "symlink_file":
        journal.parent.mkdir(mode=0o700)
        target = tmp_path / "target.json"
        target.write_text("{}")
        journal.symlink_to(target)
    else:
        request(export_case)
        if problem == "broad_permissions":
            journal.chmod(0o644)
        else:
            journal.write_text("not-json")
    before = export_case[4]["create"]
    with pytest.raises(ExportBlocked):
        request(export_case)
    assert export_case[4]["create"] == before


@pytest.mark.parametrize(
    "problem", ["current_position", "missing_entry", "wrong_entry_identity", "enabled_client"]
)
def test_preflight_blocks_exposure_wrong_account_or_write_client_before_job(export_case, problem):
    _, c, fake, journal, counts, _, order = export_case
    if problem == "current_position":
        fake.positions["BTCUSDT"] = Decimal("0.001")
        fake.prices["BTCUSDT"] = Decimal(100)
    elif problem == "missing_entry":
        del fake.orders[order.client_id]
    elif problem == "wrong_entry_identity":
        fake.orders[order.client_id]["orderId"] = "wrong"
    else:
        c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": True})
    with pytest.raises(ExportBlocked):
        request(export_case)
    assert counts["create"] == 0 and not journal.exists()


def test_new_routes_are_get_only_and_never_redirect_to_production(export_case):
    _, c, fake, _, _, _, _ = export_case
    before = len(fake.calls)
    for method in ("POST", "DELETE", "PUT"):
        for path in ("/fapi/v1/trade/asyn", "/fapi/v1/trade/asyn/id"):
            with pytest.raises(ValueError):
                c.client.request(method, path, {})
    with pytest.raises(ValueError):
        c.client.request("GET", "https://fapi.binance.com/fapi/v1/trade/asyn", {})
    assert len(fake.calls) == before


def test_cli_plan_does_not_prompt_acquire_write_or_create_journal(export_case, monkeypatch, capsys):
    import sys

    import scripts.request_demo_trade_export as cli

    store, _, _, journal, counts, _, _ = export_case
    original = store.read
    before = original("DEMO-test").model_dump_json()
    monkeypatch.setattr(cli, "ACCOUNT_ID", "DEMO-test")
    monkeypatch.setattr(cli, "JOURNAL", journal)
    monkeypatch.setattr(sys, "argv", ["request_demo_trade_export"])
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: store.engine)
    monkeypatch.setattr(cli, "DemoStore", lambda engine: store)

    def forbidden(*args):
        raise AssertionError("Plan must not prompt, acquire or change")

    monkeypatch.setattr(cli.getpass, "getpass", forbidden)
    monkeypatch.setattr(store, "acquire", forbidden)
    monkeypatch.setattr(store, "change", forbidden)
    cli.main()
    assert "PLAN_ONLY_NO_EXPORT_REQUEST" in capsys.readouterr().out
    assert not journal.exists() and counts["create"] == 0
    assert original("DEMO-test").model_dump_json() == before


def test_cli_explicit_request_clears_keys_and_keeps_database_unchanged(
    export_case, monkeypatch, capsys
):
    import sys

    import scripts.request_demo_trade_export as cli

    store, c, _, journal, counts, _, _ = export_case
    original = store.read
    before = original("DEMO-test").model_dump_json()
    monkeypatch.setattr(cli, "ACCOUNT_ID", "DEMO-test")
    monkeypatch.setattr(cli, "JOURNAL", journal)
    monkeypatch.setattr(sys, "argv", ["request_demo_trade_export", "--request-export"])
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: store.engine)
    monkeypatch.setattr(cli, "DemoStore", lambda engine: store)
    monkeypatch.setattr(cli, "DemoClient", lambda *a, **k: c.client)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "SYNTHETIC_PRIVATE_KEY")
    cli.main()
    output = capsys.readouterr().out
    assert "JOB_REQUESTED_NOT_DOWNLOADED" in output and "SYNTHETIC_PRIVATE_KEY" not in output
    assert "BINANCE_DEMO_API_KEY" not in os.environ and "BINANCE_DEMO_API_SECRET" not in os.environ
    assert counts["create"] == 1
    assert original("DEMO-test").model_dump_json() == before


def test_plan_covers_full_kst_initial_tracking_day_and_existing_cutoff(export_case):
    from services.execution.demo_trade_export import plan

    store, c, _, _, _, _, _ = export_case
    result = plan(store.read("DEMO-test"), store.now(), "DEMO-test")
    assert result["start_kst"] == "2026-10-19T00:00:00+09:00"
    assert result["end_ms"] == int(store.now().timestamp() * 1000)
    assert request(export_case)["start_ms"] == result["start_ms"]
    assert status(export_case)["start_ms"] == result["start_ms"]


def test_cli_creation_failure_hides_private_errors_and_preserves_intent(
    export_case, monkeypatch, capsys
):
    import sys

    import scripts.request_demo_trade_export as cli

    store, c, _, journal, counts, replies, _ = export_case
    replies["create"] = DemoUnknown("https://example.invalid/?SYNTHETIC_PRIVATE_LINK")
    monkeypatch.setattr(cli, "ACCOUNT_ID", "DEMO-test")
    monkeypatch.setattr(cli, "JOURNAL", journal)
    monkeypatch.setattr(sys, "argv", ["request_demo_trade_export", "--request-export"])
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: store.engine)
    monkeypatch.setattr(cli, "DemoStore", lambda engine: store)
    monkeypatch.setattr(cli, "DemoClient", lambda *a, **k: c.client)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "SYNTHETIC_PRIVATE_KEY")
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    output = capsys.readouterr().out
    assert "EXPORT_REQUEST_UNRESOLVED_DO_NOT_REPEAT" in output
    assert "SYNTHETIC_PRIVATE" not in output and "https://" not in output
    assert counts["create"] == 1
    assert json.loads(journal.read_text())["phase"] == "INTENT_RECORDED"
    assert "BINANCE_DEMO_API_KEY" not in os.environ


def test_journal_survives_new_client_process_context_without_regenerating(export_case):
    store, c, _, journal, counts, _, _ = export_case
    request(export_case)
    # A new Path and deserialized ledger represent another invocation. No
    # in-memory retry counter is needed to suppress a second creation.
    from pathlib import Path

    result = request_export(
        store, c.client, store.read("DEMO-test"), "DEMO-test", Path(str(journal))
    )
    assert result["export"] == "ALREADY_REQUESTED_NO_NEW_JOB"
    assert counts["create"] == 1
