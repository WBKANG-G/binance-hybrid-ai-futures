"""Volatile secret lifecycle: no hidden persistence or automatic trading."""

import json
import os
from pathlib import Path

import pytest

from scripts import demo_key_session as keys

KEY = "SYNTHETIC_DEMO_KEY_DO_NOT_USE"
SECRET = "SYNTHETIC_DEMO_SECRET_DO_NOT_USE"
ENV_KEYS = (
    "BINANCE_DEMO_API_KEY",
    "BINANCE_DEMO_API_SECRET",
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "BINANCE_LIVE_API_KEY",
    "BINANCE_LIVE_API_SECRET",
    "LIVE_TRADING_ENABLED",
    "TRADING_MODE",
)


@pytest.fixture
def session_dir(tmp_path, monkeypatch):
    for name in ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "ram-session"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(keys, "DIRECTORY", root)
    monkeypatch.setattr(keys, "_tmpfs", lambda: True)
    monkeypatch.setattr(keys.time, "time", lambda: 100000)
    answers = iter([KEY, SECRET])
    monkeypatch.setattr(keys.getpass, "getpass", lambda prompt: next(answers))
    return root


def test_registration_load_and_clear_never_print_or_export_values(session_dir, capsys):
    keys.register()
    assert (session_dir / keys.FILENAME).stat().st_mode & 0o777 == 0o600
    cached = keys.credentials()
    assert cached.api_key.get_secret_value() == KEY and cached.secret.get_secret_value() == SECRET
    assert KEY not in repr(cached) and SECRET not in repr(cached)
    assert not os.getenv("BINANCE_DEMO_API_KEY") and not os.getenv("BINANCE_DEMO_API_SECRET")
    assert capsys.readouterr().out == ""
    keys.clear()
    keys.clear()
    assert not list(session_dir.iterdir())


def test_cached_pair_skips_all_prompts(session_dir, monkeypatch):
    keys.register()
    monkeypatch.setattr(
        keys.getpass, "getpass", lambda *a: pytest.fail("cached credentials must not prompt")
    )
    assert keys.credentials(prompt=True).secret.get_secret_value() == SECRET


def test_first_hidden_prompt_registers_once_and_later_call_reuses(session_dir, monkeypatch):
    assert keys.credentials(prompt=True).api_key.get_secret_value() == KEY
    original = (session_dir / keys.FILENAME).read_bytes()
    assert (session_dir / keys.FILENAME).stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(keys.getpass, "getpass", lambda *a: pytest.fail("must not prompt twice"))
    assert keys.credentials(prompt=True).secret.get_secret_value() == SECRET
    assert (session_dir / keys.FILENAME).read_bytes() == original


def test_missing_session_has_no_implicit_prompt(session_dir, monkeypatch):
    monkeypatch.setattr(keys.getpass, "getpass", lambda *a: pytest.fail("must not prompt"))
    with pytest.raises(ValueError, match="Missing direct runtime"):
        keys.credentials()


def test_expired_or_backward_clock_does_not_fall_back_to_prompt(session_dir, monkeypatch):
    keys.register()
    monkeypatch.setattr(
        keys.getpass, "getpass", lambda *a: pytest.fail("must not switch credentials")
    )
    for stamp in (99999, 100000 + keys.LIFETIME_SECONDS):
        monkeypatch.setattr(keys.time, "time", lambda stamp=stamp: stamp)
        with pytest.raises(keys.SessionKeyError, match="EXPIRED"):
            keys.credentials(prompt=True)


@pytest.mark.parametrize("name", ENV_KEYS[2:6] + ("LIVE_TRADING_ENABLED", "TRADING_MODE"))
def test_live_or_generic_context_rejected_before_prompts_or_file_access(
    session_dir, monkeypatch, name
):
    monkeypatch.setenv(name, "LIVE" if name == "TRADING_MODE" else "true")
    monkeypatch.setattr(keys.getpass, "getpass", lambda *a: pytest.fail("must not prompt"))
    with pytest.raises(keys.SessionKeyError, match="DEMO_ONLY"):
        keys.register()
    with pytest.raises(keys.SessionKeyError, match="DEMO_ONLY"):
        keys.credentials(prompt=True)
    assert not list(session_dir.iterdir())


def test_explicit_runtime_pair_takes_precedence_without_overwriting_cache(session_dir, monkeypatch):
    keys.register()
    before = (session_dir / keys.FILENAME).read_bytes()
    monkeypatch.setenv("BINANCE_DEMO_API_KEY", "OTHER_SYNTHETIC_KEY")
    monkeypatch.setenv("BINANCE_DEMO_API_SECRET", "OTHER_SYNTHETIC_SECRET")
    assert keys.credentials().api_key.get_secret_value() == "OTHER_SYNTHETIC_KEY"
    assert (session_dir / keys.FILENAME).read_bytes() == before


def test_partial_runtime_pair_does_not_use_cached_other_account(session_dir, monkeypatch):
    keys.register()
    monkeypatch.setenv("BINANCE_DEMO_API_KEY", "OTHER_SYNTHETIC_KEY")
    with pytest.raises(ValueError, match="Missing direct runtime"):
        keys.credentials(prompt=True)


def test_register_existing_refuses_before_prompt_and_preserves_original(session_dir, monkeypatch):
    keys.register()
    before = (session_dir / keys.FILENAME).read_bytes()
    monkeypatch.setattr(keys.getpass, "getpass", lambda *a: pytest.fail("must not prompt"))
    with pytest.raises(keys.SessionKeyError, match="EXISTS_CLEAR_FIRST"):
        keys.register()
    assert (session_dir / keys.FILENAME).read_bytes() == before


@pytest.mark.parametrize("mode", [0o644, 0o660, 0o666, 0o400])
def test_wrong_file_permissions_rejected(session_dir, mode):
    keys.register()
    (session_dir / keys.FILENAME).chmod(mode)
    with pytest.raises(keys.SessionKeyError, match="FILE_INVALID"):
        keys.credentials()


def test_wrong_directory_permissions_rejected(session_dir):
    session_dir.chmod(0o755)
    with pytest.raises(keys.SessionKeyError, match="PERMISSIONS"):
        keys.register()


def test_persistent_filesystem_is_never_accepted(session_dir, monkeypatch):
    monkeypatch.setattr(keys, "_tmpfs", lambda: False)
    with pytest.raises(keys.SessionKeyError, match="TMPFS_REQUIRED"):
        keys.register()
    with pytest.raises(keys.SessionKeyError, match="TMPFS_REQUIRED"):
        keys.credentials(prompt=True)
    assert not list(session_dir.iterdir())


@pytest.mark.parametrize(
    "kind",
    [
        "symlink",
        "hardlink",
        "fifo",
        "oversize",
        "json",
        "invalid_key",
        "wrong_version",
        "wrong_ttl",
    ],
)
def test_malformed_or_redirected_cache_is_fail_closed(session_dir, kind):
    path = session_dir / keys.FILENAME
    if kind in {"symlink", "hardlink"}:
        target = session_dir.parent / "outside"
        target.write_text("DO_NOT_READ_OR_OVERWRITE")
        if kind == "symlink":
            path.symlink_to(target)
        else:
            os.link(target, path)
            path.chmod(0o600)
    elif kind == "fifo":
        os.mkfifo(path, 0o600)
    else:
        keys.register()
        data = json.loads(path.read_text())
        if kind == "oversize":
            path.write_text("x" * 5000)
        elif kind == "json":
            path.write_text("not-json")
        else:
            if kind == "invalid_key":
                data["key"] = "bad\nkey"
            elif kind == "wrong_version":
                data["version"] = True
            else:
                data["expires"] += 1
            path.write_text(json.dumps(data))
    with pytest.raises(keys.SessionKeyError):
        keys.credentials(prompt=True)


@pytest.mark.parametrize("value", ["", " ", "bad\nkey", "한글", "x" * 513])
def test_invalid_input_is_never_saved(session_dir, monkeypatch, value):
    monkeypatch.setattr(keys.getpass, "getpass", lambda *a: value)
    with pytest.raises(keys.SessionKeyError):
        keys.register()
    assert not list(session_dir.iterdir())


def test_mount_parser_rejects_bind_mount_or_unsafe_flags(monkeypatch):
    target = Path("/run/demo-keys")
    monkeypatch.setattr(keys, "DIRECTORY", target)
    cases = [
        ("42 40 0:1 / /run/demo-keys rw,nosuid,nodev,noexec - tmpfs tmpfs rw", True),
        ("42 40 0:1 / /run/demo-keys rw,nosuid,nodev - tmpfs tmpfs rw", False),
        ("42 40 0:1 / /run/demo-keys rw,nosuid,nodev,noexec - ext4 /dev/x rw", False),
        ("42 40 0:1 / /run/other rw,nosuid,nodev,noexec - tmpfs tmpfs rw", False),
    ]
    for content, expected in cases:
        monkeypatch.setattr(Path, "read_text", lambda *a, content=content, **k: content)
        assert keys._tmpfs() is expected


def test_cli_status_and_clear_redact_keys(session_dir, monkeypatch, capsys):
    keys.register()
    for flag in ("--status", "--clear", "--status"):
        monkeypatch.setattr("sys.argv", ["demo_key_session", flag])
        keys.main()
    output = capsys.readouterr().out
    assert "AVAILABLE" in output and "CLEARED" in output and "NOT_REGISTERED" in output
    assert KEY not in output and SECRET not in output and not list(session_dir.iterdir())


def test_cli_invalid_cache_never_exposes_its_content(session_dir, monkeypatch, capsys):
    path = session_dir / keys.FILENAME
    path.write_text(SECRET)
    path.chmod(0o600)
    monkeypatch.setattr("sys.argv", ["demo_key_session", "--status"])
    with pytest.raises(SystemExit) as error:
        keys.main()
    output = capsys.readouterr().out
    assert error.value.code == 2 and "BLOCKED" in output and SECRET not in output


def test_container_memory_reset_means_registered_pair_is_gone(session_dir, monkeypatch):
    keys.register()
    (session_dir / keys.FILENAME).unlink()
    monkeypatch.setattr(keys.getpass, "getpass", lambda *a: pytest.fail("must not prompt silently"))
    with pytest.raises(ValueError):
        keys.credentials()
