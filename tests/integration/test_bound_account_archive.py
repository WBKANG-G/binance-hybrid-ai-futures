"""Same RAM credential context through synthetic V2 account archive; real PG/SQLite."""

# ruff: noqa: F401,F811
import json

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoCredentials
from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo_order_quota import LocalQuotaStopped
from services.execution.demo_read_credential_context import (
    LocalReadCredentialContext,
    capture_bound_copy_account,
)
from tests.integration.test_copy_baseline_evidence import stale_copy
from tests.integration.test_copy_response_archive import capture
from tests.integration.test_read_audit_bootstrap import ready
from tests.integration.test_response_evidence_bootstrap import clock, setup_ready
from tests.integration.test_runtime_read_accounting import financial, settings
from tests.integration.test_shared_weight_checkpoint import anchored, demo_system
from tests.unit.test_copy_account_content import account_body

KEY = "SYNTHETIC_CONTEXT_KEY"
SECRET = "SYNTHETIC_CONTEXT_SECRET"


def context_for(a, scope=None):
    return LocalReadCredentialContext(
        "DEMO-test",
        scope or a[0].scope,
        DemoCredentials(SecretStr(KEY), SecretStr(SECRET)),
        enable_local_context=True,
        copy_only=True,
        clocks=clock(a),
    )


def test_disabled_capture_precedes_provider_access():
    with pytest.raises(LocalQuotaStopped, match="CAPTURE_DISABLED"):
        capture_bound_copy_account(None, None, None, None, sender=None)


def test_signed_context_is_archived_and_closed_without_identity_or_financial_authority(capture):
    a, journal, parent, sent, build = capture
    context = context_for(a)
    descriptor = context.descriptor()
    fin = financial(a)
    body = json.dumps(account_body()).encode()

    def send(*args):
        sent.append(1)
        a[2].seconds += 6  # Completed response need not have a fresh signing timestamp again.
        return RESTResponse(200, body, {"x-mbx-used-weight-1m": "100"})

    result = capture_bound_copy_account(
        settings(a),
        journal,
        parent / "bound",
        context,
        enable_capture=True,
        copy_only=True,
        sender=send,
    )
    assert result["local_signature_checked"] and result["sender_calls"] == 1 and financial(a) == fin
    assert result["credential_context_id"] == descriptor["credential_context_id"]
    intent = json.loads((parent / "bound/intent.json").read_bytes())
    assert intent["credential_context_id"] == descriptor["credential_context_id"]
    assert (
        not result["remote_account_identity_verified"]
        and not result["credential_source_independently_verified"]
    )
    assert not result["baseline_adoption_approved"] and not result["execution_approved"]
    assert len(sent) == 1
    for p in (parent / "bound").iterdir():
        assert KEY.encode() not in p.read_bytes() and SECRET.encode() not in p.read_bytes()
    with pytest.raises(LocalQuotaStopped, match="CLOSED"):
        context.prepare()


@pytest.mark.parametrize("fault", ["scope", "expired", "old_context"])
def test_context_preflight_failure_creates_no_archive_and_sends_nothing(capture, fault):
    a, journal, parent, sent, build = capture
    context = context_for(a, "DEMO-other" if fault == "scope" else None)
    if fault == "expired":
        a[2].seconds += 75
    if fault == "old_context":
        context.close()

    def send(*args):
        sent.append(1)
        return RESTResponse(200, b"{}", {})

    with pytest.raises(LocalQuotaStopped):
        capture_bound_copy_account(
            settings(a),
            journal,
            parent / "bound",
            context,
            enable_capture=True,
            copy_only=True,
            sender=send,
        )
    assert not sent and not (parent / "bound").exists()


@pytest.mark.parametrize(
    "fault", ["secret_echo", "key_change", "boot_change", "network", "http_error"]
)
def test_send_failure_retains_partial_without_resend_or_secret_archive(capture, fault):
    a, journal, parent, sent, build = capture
    context = context_for(a)
    fin = financial(a)

    def send(*args):
        sent.append(1)
        if fault == "network":
            raise OSError("synthetic failure")
        if fault == "secret_echo":
            return RESTResponse(200, SECRET.encode(), {"x-mbx-used-weight-1m": "100"})
        if fault == "key_change":
            context._key = SecretStr("OTHER_KEY")
        if fault == "boot_change":
            context._clocks.boot = lambda: "another-boot"
        return RESTResponse(
            400 if fault == "http_error" else 200, b"{}", {"x-mbx-used-weight-1m": "100"}
        )

    with pytest.raises(LocalQuotaStopped):
        capture_bound_copy_account(
            settings(a),
            journal,
            parent / "bound",
            context,
            enable_capture=True,
            copy_only=True,
            sender=send,
        )
    assert len(sent) == 1 and (parent / "bound/intent.json").exists()
    assert not (parent / "bound/receipt.json").exists() and financial(a) == fin
    for p in (parent / "bound").iterdir():
        assert SECRET.encode() not in p.read_bytes()
    with pytest.raises(LocalQuotaStopped):
        capture_bound_copy_account(
            settings(a),
            journal,
            parent / "bound",
            context,
            enable_capture=True,
            copy_only=True,
            sender=send,
        )
    assert len(sent) == 1
