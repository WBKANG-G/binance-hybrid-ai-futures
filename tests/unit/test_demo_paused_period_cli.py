"""Preview-only CLI opt-in/context guards run before credentials or DB."""

import json

import pytest

from scripts import review_demo_paused_periods as cli


@pytest.mark.parametrize("arg", ["--apply-review", "--operation-id", "--accept-operator-evidence"])
def test_incomplete_apply_flags_are_rejected_before_credentials(arg, monkeypatch):
    monkeypatch.setattr("sys.argv", ["preview", "--attest-no-activity-since-anchor", arg, "value"])
    monkeypatch.setattr(
        cli, "session_credentials", lambda **kw: pytest.fail("no credential prompt")
    )
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 2


@pytest.mark.parametrize("mode", ["no_attestation", "live_flag", "live_mode"])
def test_guard_blocks_before_keys_and_db(mode, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        ["preview"] + ([] if mode == "no_attestation" else ["--attest-no-activity-since-anchor"]),
    )
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true" if mode == "live_flag" else "false")
    monkeypatch.setenv("TRADING_MODE", "live" if mode == "live_mode" else "PAPER")
    monkeypatch.setattr(
        cli, "session_credentials", lambda **kw: pytest.fail("no credential prompt")
    )
    monkeypatch.setattr(cli, "create_engine", lambda *a, **kw: pytest.fail("no DB"))
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["recovery"] == "BLOCKED_KEEP_LOCK"
    assert not report["orders_enabled"] and not report["live_trading_enabled"]


@pytest.mark.parametrize(
    "extra",
    [
        ["--apply-review", "0" * 64],
        ["--apply-review", "0" * 64, "--accept-operator-evidence"],
        ["--apply-review", "0" * 64, "--operation-id", "7230ec92-b17e-4fa9-bfef-336801a2182e"],
        ["--accept-operator-evidence", "--operation-id", "7230ec92-b17e-4fa9-bfef-336801a2182e"],
        [
            "--apply-review",
            "x" * 64,
            "--accept-operator-evidence",
            "--operation-id",
            "7230ec92-b17e-4fa9-bfef-336801a2182e",
        ],
        [
            "--apply-review",
            "0" * 64,
            "--accept-operator-evidence",
            "--operation-id",
            "00000000-0000-0000-0000-000000000000",
        ],
    ],
)
def test_incomplete_or_invalid_apply_contract_blocks_before_keys_and_db(extra, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["apply", "--attest-no-activity-since-anchor", *extra])
    monkeypatch.setattr(cli, "session_credentials", lambda **kw: pytest.fail("no keys"))
    monkeypatch.setattr(cli, "create_engine", lambda *a, **kw: pytest.fail("no DB"))
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 2
    assert (
        json.loads(capsys.readouterr().out)["error_code"]
        == "REVIEW_TOKEN_AND_OPERATION_ID_REQUIRED"
    )
