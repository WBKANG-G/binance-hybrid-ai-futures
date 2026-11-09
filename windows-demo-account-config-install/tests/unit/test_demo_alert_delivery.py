"""Destination opt-in and secret-free CLI failures."""

import json
import sys

import pytest

from scripts import review_demo_alerts as cli
from services.operations.demo_alert_delivery import DeliveryFailure, LocalWebhook


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/alerts",
        "http://localhost:8000/alerts",
        "http://127.0.0.1:8000/other",
        "http://u:p@127.0.0.1:8000/alerts",
        "http://127.0.0.1:8000/alerts?token=hidden",
        "http://127.0.0.1:8000/alerts#hidden",
    ],
)
def test_remote_or_ambiguous_receivers_rejected(url):
    with pytest.raises(DeliveryFailure):
        LocalWebhook(url)


@pytest.mark.parametrize(
    "args",
    [["--endpoint", "http://127.0.0.1:1/alerts"], ["--deliver-local"], ["--watch-seconds", "1"]],
)
def test_explicit_local_delivery_arguments_required(monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["review", *args])
    monkeypatch.setattr(cli, "create_engine", lambda *a, **kw: pytest.fail("must not access DB"))
    with pytest.raises(SystemExit):
        cli.main()


def test_live_context_rejected_before_db_or_network(monkeypatch, capsys):
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    monkeypatch.setattr(sys, "argv", ["review"])
    monkeypatch.setattr(cli, "create_engine", lambda *a, **kw: pytest.fail("must not access DB"))
    with pytest.raises(SystemExit):
        cli.main()
    assert json.loads(capsys.readouterr().out)["demo_alerts"] == "BLOCKED"


def test_default_summary_does_not_construct_receiver(monkeypatch, capsys):
    class Engine:
        def dispose(self):
            pass

    monkeypatch.setattr(sys, "argv", ["review"])
    monkeypatch.setattr(cli, "create_engine", lambda *a, **kw: Engine())
    monkeypatch.setattr(cli, "LocalWebhook", lambda *a: pytest.fail("default must not send"))
    monkeypatch.setattr(
        cli, "summary", lambda *a: {"pending": 0, "external_receiver_configured": False}
    )
    cli.main()
    report = json.loads(capsys.readouterr().out)
    assert report["demo_alerts"] == "READ_ONLY_QUEUE_SUMMARY" and report["delivery_results"] == []


def test_email_worker_is_idle_without_config_and_disposes_on_shutdown(
    monkeypatch, tmp_path, capsys
):
    from scripts import watch_demo_alert_email as worker

    disposed = []

    class Engine:
        def dispose(self):
            disposed.append(True)

    monkeypatch.setattr(worker, "create_engine", lambda *a, **kw: Engine())
    monkeypatch.setattr(worker, "load", lambda: None)
    monkeypatch.setattr(
        worker, "NaverEmail", lambda *a: pytest.fail("unconfigured worker must not send")
    )
    monkeypatch.setattr(worker, "Path", lambda *a: tmp_path / "health")

    def stop(*a):
        raise KeyboardInterrupt

    monkeypatch.setattr(worker.time, "sleep", stop)
    with pytest.raises(KeyboardInterrupt):
        worker.main()
    assert disposed and (tmp_path / "health").exists()
    assert "WAITING_FOR_PRIVATE_RAM_CONFIG" in capsys.readouterr().out


def test_configured_email_worker_uses_queue_dispatcher_without_secret_output(
    monkeypatch, tmp_path, capsys
):
    from pydantic import SecretStr

    from scripts import watch_demo_alert_email as worker
    from scripts.demo_alert_email_session import EmailSettings

    settings = EmailSettings("fixture-user@naver.com", SecretStr("SYNTHETIC_PRIVATE_PASSWORD"))
    seen = []
    outcome = ["ACKNOWLEDGED"]

    class Engine:
        def dispose(self):
            pass

    class Dispatcher:
        def __init__(self, engine):
            pass

        def deliver_one(self, account, sink):
            seen.append(account)
            return {
                "alert_delivery": "DELIVERED" if outcome[0] == "ACKNOWLEDGED" else "RETRY_PENDING",
                "result": outcome[0],
                "attempt": 1,
            }

    monkeypatch.setattr(worker, "create_engine", lambda *a, **kw: Engine())
    monkeypatch.setattr(worker, "Dispatcher", Dispatcher)
    monkeypatch.setattr(worker, "load", lambda: settings)
    monkeypatch.setattr(worker, "Path", lambda *a: tmp_path / "health")

    def stop(*a):
        raise KeyboardInterrupt

    monkeypatch.setattr(worker.time, "sleep", stop)
    with pytest.raises(KeyboardInterrupt):
        worker.main()
    output = capsys.readouterr().out
    assert (
        seen
        and "DELIVERED" in output
        and "fixture-user" not in output
        and "SYNTHETIC_PRIVATE" not in output
    )

    seen.clear()
    outcome[0] = "SMTP_AUTH_FAILED"
    loops = [0]

    def stop_after_three(*a):
        loops[0] += 1
        if loops[0] == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(worker.time, "sleep", stop_after_three)
    with pytest.raises(KeyboardInterrupt):
        worker.main()
    assert len(seen) == 1
    output = capsys.readouterr().out
    assert "WAITING_FOR_EMAIL_CONFIG_CHANGE" in output and "SYNTHETIC_PRIVATE" not in output


def test_alert_worker_compose_has_readonly_key_mount_and_no_secret_env():
    from pathlib import Path

    import yaml

    cfg = yaml.safe_load(Path("infra/docker-compose.yml").read_text())
    service = cfg["services"]["alerts"]
    assert service["command"] == ["python", "-m", "scripts.watch_demo_alert_email"]
    assert service["read_only"] and not service.get("ports")
    assert service["cap_drop"] == ["ALL"]
    assert service["depends_on"]["api"]["condition"] == "service_healthy"
    assert (
        service["volumes"][0]["read_only"]
        and service["volumes"][0]["source"] == "demo_key_session_ram"
    )
    assert not any(
        "KEY" in name or "PASSWORD" in name or "SMTP" in name for name in service["environment"]
    )


def test_self_email_test_requires_explicit_flag_before_configuration(monkeypatch):
    from scripts import send_demo_alert_email_test as test_cli

    monkeypatch.setattr(sys, "argv", ["test-email"])
    monkeypatch.setattr(test_cli, "load", lambda: pytest.fail("must not read config"))
    with pytest.raises(SystemExit):
        test_cli.main()


def test_self_email_test_failure_cannot_log_provider_secrets(monkeypatch, capsys):
    from uuid import uuid4

    from scripts import send_demo_alert_email_test as test_cli

    monkeypatch.setattr(
        sys, "argv", ["test-email", "--send-self-test", "--operation-id", str(uuid4())]
    )

    def fail():
        raise RuntimeError("SYNTHETIC_PASSWORD_FROM_PROVIDER")

    monkeypatch.setattr(test_cli, "load", fail)
    with pytest.raises(SystemExit):
        test_cli.main()
    output = capsys.readouterr().out
    assert "NOT_CONFIRMED_DO_NOT_REPEAT" in output and "SYNTHETIC" not in output
