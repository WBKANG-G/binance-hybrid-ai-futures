"""TEST mail cannot be confused with a real critical incident or trigger a trade."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.persistence.demo_alerts import AlertEnvelope
from scripts import check_demo_alert_pipeline as cli


def envelope(**changes):
    values = dict(
        alert_id=uuid4(),
        event_id=uuid4(),
        code="DELIVERY_PIPELINE_TEST",
        severity="TEST",
        account_ref="a" * 24,
        occurred_at=datetime.now(UTC),
        risk_state="NORMAL",
        paused=True,
    )
    # JSON validation matches the worker's transport decoder and strict enum parsing.
    values.update(changes)
    return AlertEnvelope.model_validate_json(json.dumps(values, default=str))


def test_pipeline_test_is_explicit_test_with_no_order_references():
    assert envelope().severity == "TEST"


@pytest.mark.parametrize(
    "changes",
    [{"severity": "CRITICAL"}, {"code": "EMERGENCY_ENTERED"}, {"order_ids": [str(uuid4())]}],
)
def test_test_or_critical_mislabeling_and_fake_order_refs_are_rejected(changes):
    with pytest.raises(ValidationError):
        envelope(**changes)


@pytest.mark.parametrize(
    "args",
    [
        ["--operation-id", str(uuid4())],
        ["--enqueue-self-test", "--operation-id", str(uuid4()), "--wait-seconds", "61"],
    ],
)
def test_explicit_action_and_bounded_wait_required_before_db(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["pipeline", *args])
    monkeypatch.setattr(cli, "create_engine", lambda *a, **kw: pytest.fail("must not access DB"))
    with pytest.raises(SystemExit):
        cli.main()


@pytest.mark.parametrize("mode", ["live", "missing_email"])
def test_context_blocks_before_queue_write(monkeypatch, mode, capsys):
    monkeypatch.setattr(
        "sys.argv", ["pipeline", "--enqueue-self-test", "--operation-id", str(uuid4())]
    )
    if mode == "live":
        monkeypatch.setenv("TRADING_MODE", "LIVE")
    monkeypatch.setattr(cli, "email_status", lambda: "NOT_CONFIGURED")
    monkeypatch.setattr(cli, "create_engine", lambda *a, **kw: pytest.fail("must not access DB"))
    with pytest.raises(SystemExit):
        cli.main()
    assert json.loads(capsys.readouterr().out)["pipeline_check"] == "BLOCKED"
