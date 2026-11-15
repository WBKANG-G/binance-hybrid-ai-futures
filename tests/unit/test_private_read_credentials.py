"""Guest lease file continuity on a synthetic tmpfs substitute; no credentials sent."""

# ruff: noqa: F401,F811
import copy
import hashlib
import json
import pickle
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoCredentials
from packages.persistence.demo_order_quota import LocalQuotaStopped
from scripts import demo_key_session as keys
from services.execution import demo_copy_volatile_credentials as module
from services.execution.demo_runtime_storage import RuntimeClocks
from tests.unit.test_demo_key_session import KEY, SECRET, session_dir


@pytest.fixture
def ready_lease(session_dir, monkeypatch):
    state = dict(seconds=0, drift=0, boot="key-read-boot")
    clocks = RuntimeClocks(
        monotonic=lambda: state["seconds"],
        wall_clock=lambda: datetime.fromtimestamp(100000 + state["seconds"] + state["drift"], UTC),
        boot=lambda: state["boot"],
    )
    monkeypatch.setattr(module, "_readonly_tmpfs", lambda: True)
    keys.register()
    return session_dir, state, clocks


def make(case):
    return module.PrivateReadCredentialLease(
        "DEMO-test", "DEMO-keys", enable_private_read=True, copy_only=True, clocks=case[2]
    )


def test_disabled_precedes_any_tmpfs_or_environment_access(monkeypatch):
    monkeypatch.setattr(
        module, "_environment", lambda: pytest.fail("context accessed before opt-in")
    )
    with pytest.raises(LocalQuotaStopped, match="PRIVATE_READ_DISABLED"):
        module.PrivateReadCredentialLease(None, None)


def test_lease_keeps_same_file_and_blocks_registration_until_close(ready_lease, capsys):
    root, state, clocks = ready_lease
    path = root / keys.FILENAME
    body = path.read_bytes()
    mtime = path.stat().st_mtime_ns
    with make(ready_lease) as lease:
        lease.verify()
        assert KEY not in repr(lease) and SECRET not in repr(lease)
        with pytest.raises(keys.SessionKeyError, match="REGISTRATION_IN_PROGRESS"):
            keys.clear()
        assert path.read_bytes() == body and path.stat().st_mtime_ns == mtime
    with pytest.raises(LocalQuotaStopped, match="CLOSED"):
        lease.verify()
    assert path.read_bytes() == body and capsys.readouterr().out == ""
    keys.clear()
    assert not path.exists()


@pytest.mark.parametrize("name", module.CREDENTIAL_ENV + ("LIVE_TRADING_ENABLED", "TRADING_MODE"))
def test_any_environment_credential_or_live_mode_denies_no_fallback(ready_lease, monkeypatch, name):
    root, state, clocks = ready_lease
    before = (root / keys.FILENAME).read_bytes()
    monkeypatch.setenv(name, "LIVE" if name == "TRADING_MODE" else "SYNTHETIC_ENV")
    with pytest.raises((LocalQuotaStopped, keys.SessionKeyError)):
        make(ready_lease)
    assert (root / keys.FILENAME).read_bytes() == before


@pytest.mark.parametrize(
    "fault",
    [
        "expiry",
        "lease_timeout",
        "drift",
        "boot",
        "backwards",
        "replacement",
        "content",
        "extra_file",
        "ram_pair",
        "scope",
        "pid",
    ],
)
def test_changed_or_expired_source_is_never_silently_reloaded(ready_lease, monkeypatch, fault):
    root, state, clocks = ready_lease
    lease = make(ready_lease)
    path = root / keys.FILENAME
    if fault == "expiry":
        state["seconds"] = keys.LIFETIME_SECONDS
    if fault == "lease_timeout":
        state["seconds"] = 75
    if fault == "drift":
        state["drift"] = 2
    if fault == "boot":
        state["boot"] = "other"
    if fault == "backwards":
        state["seconds"] = -1
    if fault == "replacement":
        path.unlink()
        path.write_bytes(b"{}")
        path.chmod(0o600)
    if fault == "content":
        path.write_bytes(path.read_bytes().replace(KEY.encode(), b"X" * len(KEY)))
    if fault == "extra_file":
        (root / "extra").write_text("not-allowed")
    if fault == "ram_pair":
        lease._credentials = DemoCredentials(SecretStr("OTHER"), SecretStr("PAIR"))
    if fault == "scope":
        lease._scope = "DEMO-other"
    if fault == "pid":
        monkeypatch.setattr(module.os, "getpid", lambda: lease._pid + 1)
    try:
        with pytest.raises(LocalQuotaStopped):
            lease.verify()
    finally:
        lease.close()


@pytest.mark.parametrize(
    "fault", ["readonly", "mode", "hardlink", "symlink", "duplicate", "expired_at_open"]
)
def test_invalid_file_or_mount_is_rejected_before_use(ready_lease, monkeypatch, fault):
    root, state, clocks = ready_lease
    path = root / keys.FILENAME
    if fault == "readonly":
        monkeypatch.setattr(module, "_readonly_tmpfs", lambda: False)
    if fault == "mode":
        path.chmod(0o644)
    if fault == "hardlink":
        (root.parent / "second").hardlink_to(path)
    if fault == "symlink":
        other = root.parent / "outside"
        path.rename(other)
        path.symlink_to(other)
    if fault == "duplicate":
        path.write_bytes(path.read_bytes().replace(b'"version": 1', b'"version": 1, "version": 1'))
    if fault == "expired_at_open":
        state["seconds"] = keys.LIFETIME_SECONDS
    with pytest.raises((LocalQuotaStopped, OSError, keys.SessionKeyError)):
        make(ready_lease)


@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle.dumps])
def test_private_lease_cannot_be_copied_or_serialized(ready_lease, operation):
    with make(ready_lease) as lease:
        with pytest.raises(LocalQuotaStopped):
            operation(lease)
