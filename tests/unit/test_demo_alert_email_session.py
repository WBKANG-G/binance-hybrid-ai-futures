"""Email app password is reusable private RAM, separate from the trading key."""

import json
import os

import pytest

from scripts import demo_alert_email_session as mail
from scripts import demo_key_session as keys

ADDRESS = "fixture-user@naver.com"
PASSWORD = "SYNTHETIC_APP_PASSWORD_NEVER_USE"


@pytest.fixture
def ram(tmp_path, monkeypatch):
    root = tmp_path / "ram"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(keys, "DIRECTORY", root)
    monkeypatch.setattr(keys, "_tmpfs", lambda: True)
    monkeypatch.setattr(mail.time, "time", lambda: 100000)
    monkeypatch.setattr("builtins.input", lambda *a: ADDRESS)
    monkeypatch.setattr(mail.getpass, "getpass", lambda *a: PASSWORD)
    return root


def test_first_config_is_hidden_ram_and_repeated_config_never_prompts_or_extends(ram, monkeypatch):
    (ram / keys.FILENAME).write_bytes(b"EXISTING_API_SESSION_UNCHANGED")
    mail.configure()
    path = ram / mail.FILENAME
    original = path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(original)["expires"] == 100000 + 12 * 3600
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("do not ask again"))
    monkeypatch.setattr(
        mail.getpass, "getpass", lambda *a: pytest.fail("do not ask password again")
    )
    monkeypatch.setattr(mail.time, "time", lambda: 100300)
    mail.configure()
    assert mail.load().password.get_secret_value() == PASSWORD
    assert PASSWORD not in repr(mail.load())
    assert path.read_bytes() == original
    assert (ram / keys.FILENAME).read_bytes() == b"EXISTING_API_SESSION_UNCHANGED"
    mail.clear()
    mail.clear()
    assert not path.exists() and (ram / keys.FILENAME).exists()


@pytest.mark.parametrize(
    "fault",
    [
        "expired",
        "backwards",
        "mode",
        "symlink",
        "hardlink",
        "fifo",
        "oversize",
        "ttl",
        "json",
        "bool_created",
    ],
)
def test_unsafe_email_cache_is_rejected_without_prompt(ram, monkeypatch, fault):
    mail.configure()
    path = ram / mail.FILENAME
    data = json.loads(path.read_bytes())
    if fault == "expired":
        monkeypatch.setattr(mail.time, "time", lambda: data["expires"])
    elif fault == "backwards":
        monkeypatch.setattr(mail.time, "time", lambda: data["created"] - 1)
    elif fault == "mode":
        path.chmod(0o644)
    elif fault in {"symlink", "hardlink", "fifo"}:
        outside = ram.parent / "do-not-read"
        outside.write_text(PASSWORD)
        path.unlink()
        if fault == "symlink":
            path.symlink_to(outside)
        elif fault == "hardlink":
            os.link(outside, path)
        else:
            os.mkfifo(path, 0o600)
    elif fault == "oversize":
        path.write_text("x" * 5000)
    elif fault == "json":
        path.write_text(PASSWORD)
    else:
        data["expires"] += 1 if fault == "ttl" else 0
        if fault == "bool_created":
            data["created"] = True
        path.write_text(json.dumps(data))
    monkeypatch.setattr(mail.getpass, "getpass", lambda *a: pytest.fail("must not prompt"))
    with pytest.raises((ValueError, OSError)):
        mail.load()
    assert mail.status() == "BLOCKED_EXPIRED_OR_INVALID"


@pytest.mark.parametrize(
    "address",
    [
        "fixture@example.com",
        "fixture@naver.com\r\nBcc: someone@example.com",
        "fixture@naver.com,other@naver.com",
    ],
)
def test_self_address_validation_rejects_other_domains_or_header_injection(
    ram, monkeypatch, address
):
    monkeypatch.setattr("builtins.input", lambda *a: address)
    with pytest.raises(ValueError):
        mail.configure()
    assert not (ram / mail.FILENAME).exists()


def test_no_email_secret_storage_on_non_tmpfs(ram, monkeypatch):
    monkeypatch.setattr(keys, "_tmpfs", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("must reject before input"))
    with pytest.raises(ValueError, match="TMPFS"):
        mail.configure()


def test_cli_status_never_prints_address_or_password(ram, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["email", "--enable-naver-email"])
    mail.main()
    output = capsys.readouterr().out
    assert "CONFIGURED_PRIVATE_RAM" in output and ADDRESS not in output and PASSWORD not in output
