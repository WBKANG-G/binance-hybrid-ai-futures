"""Explicit factual attestation and separate apply acceptance precede credential access."""

import json
from uuid import uuid4

import pytest

from scripts import review_demo_paused_day as cli


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--apply-review", "a" * 64],
        ["--attest-no-other-trades-since-exit", "--apply-review", "a" * 64],
        ["--attest-no-other-trades-since-exit", "--accept-operator-evidence"],
        [
            "--attest-no-other-trades-since-exit",
            "--apply-review",
            "bad",
            "--accept-operator-evidence",
            "--operation-id",
            str(uuid4()),
        ],
    ],
)
def test_attestation_and_apply_flags_before_credentials(monkeypatch, capsys, args):
    monkeypatch.setattr("sys.argv", ["review"] + args)
    monkeypatch.setattr(cli, "session_credentials", lambda **kw: pytest.fail("must not read keys"))
    with pytest.raises(SystemExit):
        cli.main()
    result = json.loads(capsys.readouterr().out)
    assert result["recovery"] == "BLOCKED_KEEP_LOCK"
    assert result["error_code"] in {
        "EXPLICIT_OPERATOR_ATTESTATION_REQUIRED",
        "REVIEW_TOKEN_AND_OPERATION_ID_REQUIRED",
    }


@pytest.mark.parametrize("env", [{"LIVE_TRADING_ENABLED": "true"}, {"TRADING_MODE": "LIVE"}])
def test_live_context_before_credentials(monkeypatch, capsys, env):
    monkeypatch.setattr("sys.argv", ["review", "--attest-no-other-trades-since-exit"])
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cli, "session_credentials", lambda **kw: pytest.fail("must not read keys"))
    with pytest.raises(SystemExit):
        cli.main()
    assert json.loads(capsys.readouterr().out)["error_code"] == "LIVE_CONTEXT_FORBIDDEN"
