"""Slow preparation is separate from a single non-renewable signing lease."""

# ruff: noqa: F401,F811
import copy
import json
import pickle

import pytest

from packages.persistence.demo_order_quota import LocalQuotaStopped
from scripts import demo_key_session as keys
from services.execution import demo_copy_private_preparation as module
from services.execution.demo_copy_evidence_rehearsal import (
    SYNTHETIC_PRIVATE_KEY,
    SYNTHETIC_PRIVATE_SECRET,
)
from tests.unit.test_demo_key_session import session_dir
from tests.unit.test_private_read_credentials import ready_lease


def guard(case, monkeypatch):
    root, state, clocks = case
    keys.clear()
    answers = iter((SYNTHETIC_PRIVATE_KEY, SYNTHETIC_PRIVATE_SECRET))
    monkeypatch.setattr(keys.getpass, "getpass", lambda *args: next(answers))
    keys.register()
    monkeypatch.setattr(module, "_readonly_tmpfs", lambda: True)
    return module.SyntheticPrivatePreparation(
        "DEMO-test", "DEMO-keys", copy_only=True, synthetic_only=True, clocks=clocks
    )


def test_slow_preparation_opens_one_new_75_second_lease_at_capture(ready_lease, monkeypatch):
    root, state, clocks = ready_lease
    proof = guard(ready_lease, monkeypatch)
    before = (root / keys.FILENAME).read_bytes()
    try:
        assert not hasattr(proof, "_credentials")
        state["seconds"] = 154
        proof.verify()
        lease = proof.acquire_once()
        try:
            assert proof.preparation_seconds == 154 and lease._created_mono == 154
            state["seconds"] = 228.999
            lease.verify()
            state["seconds"] = 229
            with pytest.raises(LocalQuotaStopped) as error:
                lease.verify()
            assert error.value.private_timing_reasons == ("PRIVATE_READ_LEASE_DEADLINE_EXCEEDED",)
            with pytest.raises(LocalQuotaStopped):
                proof.acquire_once()
        finally:
            lease.close()
        assert (root / keys.FILENAME).read_bytes() == before
    finally:
        proof.close()
    keys.clear()


@pytest.mark.parametrize(
    "fault",
    [
        "deadline",
        "drift",
        "boot",
        "backwards",
        "replacement",
        "content",
        "extra",
        "mode",
        "session_expiry",
        "scope",
        "pid",
    ],
)
def test_changed_preparation_cannot_open_a_lease(ready_lease, monkeypatch, fault):
    root, state, clocks = ready_lease
    proof = guard(ready_lease, monkeypatch)
    path = root / keys.FILENAME
    if fault == "deadline":
        state["seconds"] = 300
    if fault == "drift":
        state["drift"] = 2
    if fault == "boot":
        state["boot"] = "other-boot"
    if fault == "backwards":
        state["seconds"] = -1
    if fault == "replacement":
        body = path.read_bytes()
        path.unlink()
        path.write_bytes(body)
        path.chmod(0o600)
    if fault == "content":
        path.write_bytes(path.read_bytes().replace(b"SYNTHETIC_TMPFS_DOCKER_KEY", b"X" * 24))
    if fault == "extra":
        (root / "extra").write_text("blocked")
    if fault == "mode":
        path.chmod(0o644)
    if fault == "session_expiry":
        state["seconds"] = keys.LIFETIME_SECONDS
    if fault == "scope":
        proof._scope = "DEMO-other"
    if fault == "pid":
        monkeypatch.setattr(module.os, "getpid", lambda: proof._pid + 1)
    monkeypatch.setattr(
        module,
        "PrivateReadCredentialLease",
        lambda *a, **kw: pytest.fail("lease opened after changed proof"),
    )
    try:
        with pytest.raises(LocalQuotaStopped):
            proof.acquire_once()
    finally:
        proof.close()


def test_wrong_pair_denied_and_registration_lock_released(ready_lease, monkeypatch):
    monkeypatch.setattr(module, "_readonly_tmpfs", lambda: True)
    with pytest.raises(LocalQuotaStopped, match="SYNTHETIC_KEYS_REQUIRED"):
        module.SyntheticPrivatePreparation(
            "DEMO-test", "DEMO-keys", copy_only=True, synthetic_only=True, clocks=ready_lease[2]
        )
    keys.clear()


@pytest.mark.parametrize("op", [copy.copy, copy.deepcopy, pickle.dumps])
def test_preparation_is_not_transferable(ready_lease, monkeypatch, op):
    proof = guard(ready_lease, monkeypatch)
    try:
        with pytest.raises(LocalQuotaStopped):
            op(proof)
    finally:
        proof.close()


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("boot", "PRIVATE_READ_BOOT_CHANGED"),
        ("drift", "PRIVATE_READ_CLOCK_DIVERGED"),
        ("backwards", "PRIVATE_READ_MONOTONIC_ROLLBACK"),
    ],
)
def test_signing_clock_denials_keep_legacy_code_and_specific_reason(
    ready_lease, monkeypatch, fault, reason
):
    proof = guard(ready_lease, monkeypatch)
    lease = proof.acquire_once()
    state = ready_lease[1]
    if fault == "boot":
        state["boot"] = "other"
    if fault == "drift":
        state["drift"] = 2
    if fault == "backwards":
        state["seconds"] = -1
    try:
        with pytest.raises(LocalQuotaStopped, match="PRIVATE_READ_EXPIRED_OR_CLOCK_CHANGED") as err:
            lease.verify()
        assert reason in err.value.private_timing_reasons
    finally:
        lease.close()


def test_preparation_and_handed_off_lease_both_block_registration(ready_lease, monkeypatch):
    proof = guard(ready_lease, monkeypatch)
    try:
        with pytest.raises(keys.SessionKeyError, match="REGISTRATION_IN_PROGRESS"):
            keys.clear()
        lease = proof.acquire_once()
        try:
            with pytest.raises(keys.SessionKeyError, match="REGISTRATION_IN_PROGRESS"):
                keys.clear()
        finally:
            lease.close()
    finally:
        proof.close()
    keys.clear()


def test_failed_acquire_is_spent_and_never_renews(ready_lease, monkeypatch):
    proof = guard(ready_lease, monkeypatch)
    calls = []

    def fail(*args, **kwargs):
        calls.append(True)
        raise LocalQuotaStopped("SYNTHETIC_ACQUIRE_FAILURE")

    monkeypatch.setattr(module, "PrivateReadCredentialLease", fail)
    with pytest.raises(LocalQuotaStopped, match="SYNTHETIC_ACQUIRE_FAILURE"):
        proof.acquire_once()
    with pytest.raises(LocalQuotaStopped, match="CLOSED"):
        proof.acquire_once()
    assert calls == [True]
    keys.clear()


def test_handoff_race_closes_the_new_lease_without_capture(ready_lease, monkeypatch):
    proof = guard(ready_lease, monkeypatch)
    real = module.PrivateReadCredentialLease
    made = []

    def raced(*args, **kwargs):
        lease = real(*args, **kwargs)
        made.append(lease)
        path = ready_lease[0] / keys.FILENAME
        path.write_bytes(b"{}")
        return lease

    monkeypatch.setattr(module, "PrivateReadCredentialLease", raced)
    with pytest.raises(LocalQuotaStopped):
        proof.acquire_once()
    assert len(made) == 1 and made[0]._closed
    assert made[0]._file_fd is None and made[0]._directory_fd is None
    keys.clear()


@pytest.mark.parametrize("fault", ["mount", "environment"])
def test_mount_or_environment_change_cannot_open_signing_lease(ready_lease, monkeypatch, fault):
    proof = guard(ready_lease, monkeypatch)
    if fault == "mount":
        monkeypatch.setattr(module, "_readonly_tmpfs", lambda: False)
    else:
        monkeypatch.setenv("BINANCE_DEMO_API_KEY", "SYNTHETIC_ENV_FORBIDDEN")
    monkeypatch.setattr(
        module,
        "PrivateReadCredentialLease",
        lambda *a, **kw: pytest.fail("opened after context change"),
    )
    try:
        with pytest.raises(LocalQuotaStopped):
            proof.acquire_once()
    finally:
        proof.close()
