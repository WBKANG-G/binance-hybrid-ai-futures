"""Synthetic tmpfs lease into real PostgreSQL/SQLite response archive."""

# ruff: noqa: F401,F811
import json

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoCredentials
from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo_order_quota import LocalQuotaStopped
from scripts import demo_key_session as keys
from services.execution import demo_copy_volatile_credentials as module
from tests.integration.test_copy_baseline_evidence import stale_copy
from tests.integration.test_copy_response_archive import capture
from tests.integration.test_read_audit_bootstrap import ready
from tests.integration.test_response_evidence_bootstrap import clock, setup_ready
from tests.integration.test_runtime_read_accounting import financial, settings
from tests.integration.test_shared_weight_checkpoint import anchored, demo_system
from tests.unit.test_demo_key_session import KEY, SECRET, session_dir


def test_disabled_capture_precedes_any_access():
    with pytest.raises(LocalQuotaStopped, match="CAPTURE_DISABLED"):
        module.capture_with_private_copy_credentials(None, None, None, None, sender=None)


@pytest.mark.parametrize("fault", ["valid", "file_change", "expired", "ram_pair", "network"])
def test_private_lease_archive_is_one_shot_and_preserves_financial_state(
    capture, session_dir, monkeypatch, fault
):
    a, journal, parent, sent, build = capture
    monkeypatch.setattr(keys.time, "time", lambda: int(a[2].utc().timestamp()))
    monkeypatch.setattr(module, "_readonly_tmpfs", lambda: True)
    keys.register()
    original = (session_dir / keys.FILENAME).read_bytes()
    lease = module.PrivateReadCredentialLease(
        "DEMO-test", a[0].scope, enable_private_read=True, copy_only=True, clocks=clock(a)
    )
    fin = financial(a)

    def send(*args):
        sent.append(1)
        if fault == "file_change":
            (session_dir / keys.FILENAME).write_bytes(
                original.replace(KEY.encode(), b"X" * len(KEY))
            )
        if fault == "expired":
            a[2].seconds += keys.LIFETIME_SECONDS
        if fault == "ram_pair":
            lease._credentials = DemoCredentials(SecretStr("OTHER"), SecretStr("PAIR"))
        if fault == "network":
            raise OSError("synthetic network failure")
        return RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "100"})

    def run():
        return module.capture_with_private_copy_credentials(
            settings(a),
            journal,
            parent / "private",
            lease,
            enable_capture=True,
            copy_only=True,
            sender=send,
        )

    if fault == "valid":
        report = run()
        assert report["same_file_continuity_checked"] and not report["environment_fallback_used"]
        assert (
            not report["credential_file_written"] and not report["remote_account_identity_verified"]
        )
        assert (session_dir / keys.FILENAME).read_bytes() == original
    else:
        with pytest.raises(LocalQuotaStopped):
            run()
        assert not (parent / "private/receipt.json").exists()
    assert financial(a) == fin and len(sent) == 1
    with pytest.raises(LocalQuotaStopped):
        run()
    assert len(sent) == 1
    for path in (parent / "private").iterdir():
        assert SECRET.encode() not in path.read_bytes()
