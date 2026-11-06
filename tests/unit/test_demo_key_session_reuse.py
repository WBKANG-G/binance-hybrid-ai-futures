"""One pair of hidden inputs per fixed-lifetime shared RAM session."""

# ruff: noqa: F811

import json
from pathlib import Path

import pytest
import yaml

from scripts import demo_key_session as keys
from tests.unit.test_demo_key_session import KEY, SECRET, session_dir  # noqa: F401


def test_ensure_twice_preserves_original_expiry_and_never_prompts_again(
    session_dir, monkeypatch, capsys
):
    monkeypatch.setattr("sys.argv", ["keys", "--ensure"])
    keys.main()
    original = (session_dir / keys.FILENAME).read_bytes()
    payload = json.loads(original)
    monkeypatch.setattr(keys.getpass, "getpass", lambda *a: pytest.fail("must not prompt again"))
    monkeypatch.setattr(keys.time, "time", lambda: 100300)
    keys.main()
    assert (session_dir / keys.FILENAME).read_bytes() == original
    assert payload["expires"] == 100000 + 12 * 3600
    out = capsys.readouterr().out
    assert out.count('"AVAILABLE"') == 2 and KEY not in out and SECRET not in out


def test_registration_lock_denies_second_prompt_and_clear(session_dir, monkeypatch):  # noqa: F811
    monkeypatch.setattr(
        keys.getpass, "getpass", lambda *a: pytest.fail("must not prompt in competing registration")
    )
    with keys._registration_lock():
        with pytest.raises(keys.SessionKeyError, match="REGISTRATION_IN_PROGRESS"):
            keys.ensure()
        with pytest.raises(keys.SessionKeyError, match="REGISTRATION_IN_PROGRESS"):
            keys.clear()
    assert not list(session_dir.iterdir())


def test_unknown_session_error_cannot_disclose_secrets(session_dir, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setattr("sys.argv", ["keys", "--ensure"])

    def blocked():
        raise keys.SessionKeyError(SECRET)

    monkeypatch.setattr(keys, "ensure", blocked)
    with pytest.raises(SystemExit):
        keys.main()
    out = capsys.readouterr().out
    assert SECRET not in out and "KEY_SESSION_OPERATION_FAILED" in out


def test_existing_register_refusal_now_reports_fixed_code(session_dir, monkeypatch, capsys):  # noqa: F811
    keys.ensure()
    monkeypatch.setattr("sys.argv", ["keys", "--register"])
    with pytest.raises(SystemExit):
        keys.main()
    out = capsys.readouterr().out
    assert "KEY_SESSION_EXISTS_CLEAR_FIRST" in out and KEY not in out and SECRET not in out


def test_compose_keeps_ram_mount_alive_without_network_or_disk_secret_store():
    cfg = yaml.safe_load(Path("infra/docker-compose.yml").read_text())
    keeper = cfg["services"]["key-session"]
    assert keeper["network_mode"] == "none" and keeper["read_only"]
    assert keeper["user"] == "10001:10001" and keeper["cap_drop"] == ["ALL"]
    assert not keeper.get("ports") and not keeper.get("environment")
    assert keeper["volumes"][0]["read_only"]
    opts = cfg["volumes"]["demo_key_session_ram"]["driver_opts"]
    assert opts["type"] == opts["device"] == "tmpfs"
    assert {"noexec", "nosuid", "nodev", "mode=0700", "uid=10001", "gid=10001"} <= set(
        opts["o"].split(",")
    )
    api = cfg["services"]["api"]
    assert api["depends_on"]["key-session"]["condition"] == "service_healthy"
    assert any(
        isinstance(v, dict)
        and v.get("source") == "demo_key_session_ram"
        and v.get("target") == "/run/demo-keys"
        for v in api["volumes"]
    )
    assert not api.get("tmpfs")
