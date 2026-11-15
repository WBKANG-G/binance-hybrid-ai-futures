"""Local signing continuity, no key loader, persistence, remote identity or HTTP."""

import copy
import pickle
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoCredentials
from packages.persistence.demo_order_quota import LocalQuotaStopped
from services.execution.demo_read_credential_context import (
    LocalReadCredentialContext,
    PreparedAccountRead,
)
from services.execution.demo_runtime_storage import RuntimeClocks

KEY = "SYNTHETIC_READ_KEY"
SECRET = "SYNTHETIC_READ_SECRET"


@pytest.fixture
def case():
    state = dict(seconds=0, wall_delta=0, boot="read-boot")

    def mono():
        return state["seconds"]

    def wall():
        return datetime(2026, 11, 15, tzinfo=UTC) + timedelta(
            seconds=state["seconds"] + state["wall_delta"]
        )

    clocks = RuntimeClocks(monotonic=mono, wall_clock=wall, boot=lambda: state["boot"])
    context = LocalReadCredentialContext(
        "DEMO-test",
        "DEMO-read",
        DemoCredentials(SecretStr(KEY), SecretStr(SECRET)),
        enable_local_context=True,
        copy_only=True,
        clocks=clocks,
    )
    return context, state, clocks


def test_disabled_before_credentials_or_clock_access():
    with pytest.raises(LocalQuotaStopped, match="READ_CONTEXT_DISABLED"):
        LocalReadCredentialContext(None, None, None)


def test_one_shot_signing_and_close_never_prints_or_claims_identity(case, capsys):
    context, state, clocks = case
    descriptor = context.descriptor()
    prepared = context.prepare()
    context.consume(prepared)
    context.verify_consumed(prepared)
    assert (
        not descriptor["remote_account_identity_verified"]
        and not descriptor["credential_source_independently_verified"]
    )
    assert all(
        KEY not in text and SECRET not in text
        for text in (repr(context), repr(prepared), str(descriptor))
    )
    with pytest.raises(LocalQuotaStopped, match="ONE_SHOT"):
        context.consume(prepared)
    with pytest.raises(LocalQuotaStopped, match="ONE_SHOT"):
        context.prepare()
    context.close()
    context.close()
    with pytest.raises(LocalQuotaStopped, match="CLOSED"):
        context.descriptor()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "fault", ["expiry", "backwards", "drift", "boot", "key", "secret", "scope", "id", "pid"]
)
def test_context_changes_block_use_without_credential_disclosure(case, fault, monkeypatch):
    context, state, clocks = case
    prepared = context.prepare()
    if fault == "expiry":
        state["seconds"] = 75
    if fault == "backwards":
        state["seconds"] = -1
    if fault == "drift":
        state["wall_delta"] = 2
    if fault == "boot":
        state["boot"] = "different"
    if fault == "key":
        context._key = SecretStr("OTHER_KEY")
    if fault == "secret":
        context._secret = SecretStr("OTHER_SECRET")
    if fault == "scope":
        context._scope = "DEMO-other"
    if fault == "id":
        context._id = "00000000-0000-4000-8000-000000000000"
    if fault == "pid":
        monkeypatch.setattr(
            "services.execution.demo_read_credential_context.os.getpid", lambda: context._pid + 1
        )
    with pytest.raises(LocalQuotaStopped) as error:
        context.consume(prepared)
    assert KEY not in str(error.value) and SECRET not in str(error.value)
    with pytest.raises(LocalQuotaStopped, match="ONE_SHOT"):
        context.consume(prepared)


@pytest.mark.parametrize("fault", ["query", "key", "foreign", "none"])
def test_prepared_query_or_key_substitution_spends_context(case, fault):
    context, state, clocks = case
    prepared = context.prepare()
    if fault == "query":
        object.__setattr__(
            prepared, "query", SecretStr("timestamp=1&recvWindow=5000&signature=" + "a" * 64)
        )
    if fault == "key":
        object.__setattr__(prepared, "api_key", SecretStr("OTHER_KEY"))
    if fault == "foreign":
        prepared = PreparedAccountRead(prepared.context_id, prepared.query, prepared.api_key)
    if fault == "none":
        prepared = None
    with pytest.raises(LocalQuotaStopped):
        context.consume(prepared)
    with pytest.raises(LocalQuotaStopped, match="ONE_SHOT"):
        context.consume(prepared)


def test_signature_freshness_is_required_at_send_not_after_response_latency(case):
    context, state, clocks = case
    prepared = context.prepare()
    context.consume(prepared)
    state["seconds"] = 6
    context.verify_consumed(prepared)
    with pytest.raises(LocalQuotaStopped, match="FRESHNESS"):
        context.verify_consumed(prepared, require_fresh=True)


@pytest.mark.parametrize("object_kind", ["context", "prepared"])
@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle.dumps])
def test_private_context_and_capability_cannot_be_copied_or_serialized(
    case, object_kind, operation
):
    context, state, clocks = case
    value = context if object_kind == "context" else context.prepare()
    with pytest.raises(LocalQuotaStopped):
        operation(value)


@pytest.mark.parametrize(
    "name",
    [
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
        "BINANCE_LIVE_API_KEY",
        "BINANCE_LIVE_API_SECRET",
        "LIVE_TRADING_ENABLED",
        "TRADING_MODE",
    ],
)
def test_live_or_mixed_environment_denied_before_signing(case, name, monkeypatch):
    context, state, clocks = case
    monkeypatch.setenv(name, "LIVE" if name == "TRADING_MODE" else "true")
    with pytest.raises(LocalQuotaStopped):
        context.prepare()
