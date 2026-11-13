"""RAM capabilities and real private file commits; only sealed synthetic responders."""

import copy
import json
import pickle
from uuid import uuid4

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.rest import RESTResponse, prepare_signed_query
from packages.persistence.demo_order_quota import LocalQuotaStopped
from services.execution import demo_wire_session_contract_candidate as module
from services.execution.demo_submission_link_candidate import IntentPin
from services.execution.demo_wire_session_contract_candidate import (
    CredentialSession,
    SyntheticResponder,
    SyntheticWireJournal,
    review_synthetic_journal,
)

KEY = SecretStr("SYNTHETIC_API_KEY_MUST_STAY_PRIVATE")
SECRET = SecretStr("SYNTHETIC_API_SECRET_MUST_STAY_PRIVATE")


def session(**kw):
    return CredentialSession(
        "DEMO-test", "DEMO-scope", KEY, SECRET, enable_synthetic_contract=True, **kw
    )


def params(role="ENTRY", identifier="synthetic-id-1", quantity="0.0007"):
    if role == "READ":
        return "/fapi/v1/order", dict(symbol="BTCUSDT", origClientOrderId=identifier)
    if role == "EXIT":
        return "/fapi/v1/order", dict(
            symbol="BTCUSDT",
            side="SELL",
            type="MARKET",
            quantity=quantity,
            reduceOnly="true",
            newClientOrderId=identifier,
        )
    if role == "STOP_LOSS":
        return "/fapi/v1/algoOrder", dict(
            symbol="BTCUSDT",
            side="SELL",
            type="STOP_MARKET",
            algoType="CONDITIONAL",
            triggerPrice="90",
            closePosition="true",
            clientAlgoId=identifier,
            workingType="MARK_PRICE",
            priceProtect="FALSE",
        )
    return "/fapi/v1/order", dict(
        symbol="BTCUSDT",
        side="BUY",
        type="LIMIT",
        quantity=quantity,
        price="100",
        timeInForce="GTC",
        newClientOrderId=identifier,
    )


def prepared(owner, role="ENTRY", identifier="synthetic-id-1", quantity="0.0007"):
    path, values = params(role, identifier, quantity)
    signed = prepare_signed_query(
        values, api_key=SecretStr(KEY.get_secret_value()), secret=SECRET, timestamp_ms=17
    )
    value = owner.prepare(
        "GET" if role == "READ" else "POST",
        path,
        signed.query,
        signed.api_key,
        intent=None if role == "READ" else IntentPin(uuid4(), "a" * 64),
    )
    return value


def create(root):
    root.chmod(0o700)
    return SyntheticWireJournal.create(
        root, "DEMO-test", "DEMO-scope", enable_synthetic_contract=True
    )


def responder(**kw):
    return SyntheticResponder(
        RESTResponse(200, b'{"synthetic":true}', {"private": "HEADER_MUST_NOT_PERSIST"}), **kw
    )


def run(journal, owner, capture, reply):
    return journal.exercise(owner, capture, reply, enable_synthetic_dispatch=True)


def records(root):
    return [json.loads(line) for line in (root / "wire-contract-v1.log").read_bytes().splitlines()]


def test_default_denial_before_touching_credentials_paths_or_capabilities():
    with pytest.raises(LocalQuotaStopped, match="SESSION_DISABLED"):
        CredentialSession(None, None, None, None)
    with pytest.raises(LocalQuotaStopped, match="JOURNAL_DISABLED"):
        SyntheticWireJournal.create(None, None, None)
    with pytest.raises(LocalQuotaStopped, match="DISPATCH_DISABLED"):
        SyntheticWireJournal().exercise(None, None, None)


@pytest.mark.parametrize("role", ["ENTRY", "EXIT", "STOP_LOSS", "READ"])
def test_exact_request_and_session_persist_before_synthetic_response(tmp_path, monkeypatch, role):
    journal, owner = create(tmp_path), session()
    capture = prepared(owner, role, quantity="0.0003" if role == "EXIT" else "0.0007")
    original = SyntheticResponder._respond
    seen = []

    def check(self):
        rows = records(tmp_path)
        assert [r["kind"] for r in rows] == ["ROOT", "SESSION", "PREPARE", "DISPATCH_BEGIN"]
        data = rows[2]["data"]
        assert data["session"] == owner.descriptor()["session"]
        assert data["role"] == role and data["local_signature_checked"] is True
        if role == "EXIT":
            assert data["params"]["quantity"] == "0.0003"
        seen.append(True)
        return original(self)

    monkeypatch.setattr(SyntheticResponder, "_respond", check)
    result = run(journal, owner, capture, responder())
    assert result.status == 200 and seen == [True]
    assert records(tmp_path)[-1]["kind"] == "OBSERVED"
    report = review_synthetic_journal(tmp_path)
    assert report["operations"] == 1 and report["pending_operations"] == 0
    assert not any(
        report[k]
        for k in (
            "real_wire_submission_verified",
            "remote_account_identity_verified",
            "database_commit_coupling_verified",
            "external_rollback_protection_verified",
            "mutation_authorization_verified",
            "execution_approved",
            "orders_enabled",
            "archive_authorized",
        )
    )


def test_post_and_query_in_same_live_owner_share_local_session_only(tmp_path):
    owner, journal = session(), create(tmp_path)
    run(journal, owner, prepared(owner), responder())
    run(journal, owner, prepared(owner, "READ"), responder())
    entries = [r["data"] for r in records(tmp_path) if r["kind"] == "PREPARE"]
    assert len(entries) == 2 and entries[0]["session"] == entries[1]["session"]
    assert review_synthetic_journal(tmp_path)["sessions"] == 1


def test_reset_with_identical_keys_never_inherits_session_identity(tmp_path):
    one, two, journal = session(), session(), create(tmp_path)
    assert one.descriptor()["session"] != two.descriptor()["session"]
    run(journal, one, prepared(one, "READ"), responder())
    one.close()
    run(journal, two, prepared(two, "READ"), responder())
    assert review_synthetic_journal(tmp_path)["sessions"] == 2


@pytest.mark.parametrize(
    "kind", ["wrong-key", "wrong-secret", "forged-signature", "signature-not-last"]
)
def test_signature_checks_reject_other_credential_material(kind):
    owner = session()
    path, values = params()
    signed = prepare_signed_query(
        values,
        api_key=SecretStr("OTHER_KEY") if kind == "wrong-key" else KEY,
        secret=SecretStr("OTHER_SECRET") if kind == "wrong-secret" else SECRET,
        timestamp_ms=17,
    )
    query = signed.query
    if kind == "forged-signature":
        query = SecretStr(
            query.get_secret_value().rsplit("&signature=", 1)[0] + "&signature=" + "f" * 64
        )
    elif kind == "signature-not-last":
        query = SecretStr(query.get_secret_value() + "&unused=bad")
    with pytest.raises(LocalQuotaStopped):
        owner.prepare("POST", path, query, signed.api_key, intent=IntentPin(uuid4(), "a" * 64))


@pytest.mark.parametrize(
    "kind", ["query", "key", "copied-capture", "other-session", "closed", "forked"]
)
def test_prepared_capability_is_single_owner_and_rechecked_at_boundary(tmp_path, monkeypatch, kind):
    journal, owner, reply = create(tmp_path), session(), responder()
    capture = prepared(owner)
    if kind == "query":
        capture._query._secret_value = capture._query.get_secret_value().replace("100", "101", 1)
    elif kind == "key":
        capture._key._secret_value = "CHANGED_KEY"
    elif kind == "copied-capture":
        capture = copy.copy(capture)
    elif kind == "other-session":
        owner = session()
    elif kind == "closed":
        owner.close()
    else:
        monkeypatch.setattr(module.os, "getpid", lambda: 999999999)
    with pytest.raises(LocalQuotaStopped):
        run(journal, owner, capture, reply)
    assert reply.calls == 0
    assert [r["kind"] for r in records(tmp_path)] == ["ROOT"]


def test_copied_credentials_do_not_change_when_callers_secret_objects_change():
    key, secret = SecretStr("COPY_KEY"), SecretStr("COPY_SECRET")
    owner = CredentialSession(
        "DEMO-test", "DEMO-scope", key, secret, enable_synthetic_contract=True
    )
    key._secret_value, secret._secret_value = "ALTERED", "ALTERED"
    path, values = params()
    signed = prepare_signed_query(
        values, api_key=SecretStr("COPY_KEY"), secret=SecretStr("COPY_SECRET"), timestamp_ms=17
    )
    assert owner.prepare(
        "POST", path, signed.query, signed.api_key, intent=IntentPin(uuid4(), "a" * 64)
    )


@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle.dumps])
def test_sessions_cannot_be_copied_or_serialized(operation):
    owner = session()
    with pytest.raises(LocalQuotaStopped):
        operation(owner)
    assert KEY.get_secret_value() not in repr(owner) and SECRET.get_secret_value() not in repr(
        owner
    )


def test_no_key_signature_body_or_headers_persist_in_log_or_report(tmp_path):
    owner, journal = session(), create(tmp_path)
    capture = prepared(owner)
    signature = capture._query.get_secret_value().rsplit("&signature=", 1)[1]
    reply = SyntheticResponder(
        RESTResponse(200, b"PRIVATE_BODY_MUST_NOT_PERSIST", {"private": "PRIVATE_HEADER"})
    )
    run(journal, owner, capture, reply)
    text = (
        (tmp_path / "wire-contract-v1.log").read_text()
        + str(review_synthetic_journal(tmp_path))
        + repr(capture)
    )
    for private in (
        KEY.get_secret_value(),
        SECRET.get_secret_value(),
        signature,
        "PRIVATE_BODY_MUST_NOT_PERSIST",
        "PRIVATE_HEADER",
    ):
        assert private not in text


def test_single_capture_and_mutation_id_cannot_be_reused_even_in_another_session(tmp_path):
    owner, journal, reply = session(), create(tmp_path), responder()
    capture = prepared(owner)
    run(journal, owner, capture, reply)
    with pytest.raises(LocalQuotaStopped, match="REUSED_OR_FORGED"):
        run(journal, owner, capture, reply)
    other = session()
    with pytest.raises(LocalQuotaStopped, match="APPEND_FAILED"):
        run(journal, other, prepared(other), reply)
    assert reply.calls == 1
    assert review_synthetic_journal(tmp_path)["operations"] == 1


@pytest.mark.parametrize("phase", ["SESSION", "PREPARE", "DISPATCH_BEGIN", "OBSERVED"])
def test_record_commit_failure_never_allows_automatic_retry(tmp_path, monkeypatch, phase):
    journal, owner, reply = create(tmp_path), session(), responder()
    capture = prepared(owner)
    original = module._append_line

    def fail(path, previous, line):
        if json.loads(line)["kind"] == phase:
            raise OSError("SYNTHETIC_COMMIT_FAILURE")
        original(path, previous, line)

    monkeypatch.setattr(module, "_append_line", fail)
    with pytest.raises(LocalQuotaStopped, match="APPEND_FAILED"):
        run(journal, owner, capture, reply)
    assert reply.calls == (phase == "OBSERVED")
    with pytest.raises(LocalQuotaStopped, match="WRITER_BLOCKED"):
        run(journal, owner, capture, reply)
    assert reply.calls == (phase == "OBSERVED")


def test_uncertain_synthetic_dispatch_stays_pending_and_blocks_all_later_work(tmp_path):
    journal, owner, reply = create(tmp_path), session(), responder(fail=True)
    capture = prepared(owner)
    with pytest.raises(LocalQuotaStopped, match="UNCERTAIN_NO_RETRY"):
        run(journal, owner, capture, reply)
    assert reply.calls == 1 and records(tmp_path)[-1]["kind"] == "UNKNOWN"
    report = review_synthetic_journal(tmp_path)
    assert report["pending_operations"] == 1 and report["uncertain_outcome"]
    with pytest.raises(LocalQuotaStopped, match="WRITER_BLOCKED"):
        run(journal, owner, prepared(owner, "READ"), reply)
    assert reply.calls == 1


def test_partial_log_write_is_not_repaired(tmp_path, monkeypatch):
    journal, owner, reply = create(tmp_path), session(), responder()

    def partial(path, previous, line):
        with path.open("ab") as stream:
            stream.write(line[:9])
        raise OSError("SYNTHETIC_PARTIAL_WRITE")

    monkeypatch.setattr(module, "_append_line", partial)
    with pytest.raises(LocalQuotaStopped):
        run(journal, owner, prepared(owner), reply)
    before = journal.path.read_bytes()
    with pytest.raises(LocalQuotaStopped):
        review_synthetic_journal(tmp_path)
    assert journal.path.read_bytes() == before and reply.calls == 0


def test_fsync_failure_after_a_complete_record_has_no_send_or_resume(tmp_path, monkeypatch):
    journal, owner, reply = create(tmp_path), session(), responder()

    def fail(fd):
        raise OSError("SYNTHETIC_FSYNC_FAILURE")

    monkeypatch.setattr(module.os, "fsync", fail)
    with pytest.raises(LocalQuotaStopped):
        run(journal, owner, prepared(owner), reply)
    assert reply.calls == 0
    with pytest.raises(LocalQuotaStopped, match="WRITER_BLOCKED"):
        run(journal, owner, prepared(owner, "READ"), reply)
    assert not review_synthetic_journal(tmp_path)["process_restart_resumption_supported"]


def test_external_tip_change_poisoning_survives_restoring_original_file(tmp_path):
    journal, owner, reply = create(tmp_path), session(), responder()
    original = journal.path.read_bytes()
    journal.path.write_bytes(original + b"{}\n")
    with pytest.raises(LocalQuotaStopped, match="TIP_MISMATCH"):
        run(journal, owner, prepared(owner), reply)
    journal.path.write_bytes(original)
    with pytest.raises(LocalQuotaStopped, match="WRITER_BLOCKED"):
        run(journal, owner, prepared(owner, "READ"), reply)
    assert reply.calls == 0


def test_limit_admission_leaves_room_for_response(tmp_path, monkeypatch):
    journal, owner = create(tmp_path), session()
    monkeypatch.setattr(module, "MAX_RECORDS", 5)
    run(journal, owner, prepared(owner), responder())
    other = responder()
    with pytest.raises(LocalQuotaStopped, match="RECORD_LIMIT"):
        run(journal, owner, prepared(owner, "READ"), other)
    assert other.calls == 0 and review_synthetic_journal(tmp_path)["pending_operations"] == 0


@pytest.mark.parametrize("status", [400, 429, 500])
def test_http_response_observation_grants_no_outcome_authority(tmp_path, status):
    journal, owner = create(tmp_path), session()
    result = run(
        journal,
        owner,
        prepared(owner),
        SyntheticResponder(RESTResponse(status, b'{"code":-2013}', {})),
    )
    assert result.status == status
    assert records(tmp_path)[-1]["data"]["response"]["status"] == status
    assert not review_synthetic_journal(tmp_path)["archive_authorized"]


def test_arbitrary_callable_and_subclass_cannot_replace_synthetic_responder(tmp_path):
    journal, owner = create(tmp_path), session()

    class FakeResponder(SyntheticResponder):
        pass

    for reply in (lambda *a: RESTResponse(200, b"", {}), FakeResponder(RESTResponse(200, b"", {}))):
        with pytest.raises(LocalQuotaStopped, match="SYNTHETIC_CAPABILITY_REQUIRED"):
            run(journal, owner, prepared(owner), reply)
    assert len(records(tmp_path)) == 1


def test_readonly_review_after_owner_close_never_reopens_writer(tmp_path):
    journal, owner = create(tmp_path), session()
    run(journal, owner, prepared(owner), responder())
    before = journal.path.read_bytes()
    owner.close()
    assert review_synthetic_journal(tmp_path)["operations"] == 1
    assert journal.path.read_bytes() == before
    with pytest.raises(FileExistsError):
        create(tmp_path)


@pytest.mark.parametrize("operation", [pickle.dumps, copy.deepcopy])
def test_prepared_wire_with_private_key_cannot_be_serialized(operation):
    owner = session()
    capture = prepared(owner)
    with pytest.raises(LocalQuotaStopped, match="PREPARED_NOT_SERIALIZABLE"):
        operation(capture)


def test_signature_and_key_are_rechecked_after_synthetic_boundary(tmp_path, monkeypatch):
    journal, owner, reply = create(tmp_path), session(), responder()
    capture = prepared(owner)
    original = SyntheticResponder._respond

    def mutate(self):
        result = original(self)
        capture._key._secret_value = "REPLACED_DURING_BOUNDARY"
        return result

    monkeypatch.setattr(SyntheticResponder, "_respond", mutate)
    with pytest.raises(LocalQuotaStopped, match="UNCERTAIN_NO_RETRY"):
        run(journal, owner, capture, reply)
    assert reply.calls == 1 and records(tmp_path)[-1]["kind"] == "UNKNOWN"


def test_valid_prefix_rollback_has_no_external_authentication_claim_and_blocks_live_writer(
    tmp_path,
):
    journal, owner = create(tmp_path), session()
    root_only = journal.path.read_bytes()
    run(journal, owner, prepared(owner), responder())
    journal.path.write_bytes(root_only)
    report = review_synthetic_journal(tmp_path)
    assert report["hash_chain_structure_verified"] and report["operations"] == 0
    assert not report["external_rollback_protection_verified"]
    with pytest.raises(LocalQuotaStopped, match="TIP_MISMATCH"):
        run(journal, owner, prepared(owner, "READ"), responder())


@pytest.mark.parametrize("kind", ["tamper", "phase", "live-root", "secret-field", "cross-scope"])
def test_tampered_or_unsupported_contract_records_are_rejected(tmp_path, kind):
    journal, owner = create(tmp_path), session()
    run(journal, owner, prepared(owner), responder())
    values = records(tmp_path)
    if kind == "tamper":
        values[2]["data"]["params"]["quantity"] = "0.0008"
    elif kind == "phase":
        values[3], values[4] = values[4], values[3]
    elif kind == "live-root":
        values[0]["data"]["environment"] = "LIVE"
    elif kind == "secret-field":
        values[2]["data"]["api_key"] = "MUST_BE_REJECTED"
    else:
        values[1]["data"]["scope"] = "DEMO-other"
    if kind != "tamper":
        previous = "0" * 64
        for index, value in enumerate(values):
            value["sequence"], value["previous_hash"] = index, previous
            value["hash"] = module.digest({k: v for k, v in value.items() if k != "hash"})
            previous = value["hash"]
    journal.path.write_bytes(b"".join(module._line(v) for v in values))
    with pytest.raises(LocalQuotaStopped):
        review_synthetic_journal(tmp_path)


@pytest.mark.parametrize("kind", ["no-pin", "market-entry", "tp-algo", "listen-key", "delete"])
def test_mutations_outside_narrow_offline_contract_are_rejected(kind):
    owner = session()
    path, values = params("STOP_LOSS" if kind == "tp-algo" else "ENTRY")
    method = "POST"
    if kind == "market-entry":
        values["type"] = "MARKET"
    elif kind == "tp-algo":
        values["type"] = "TAKE_PROFIT_MARKET"
    elif kind == "listen-key":
        path, values = "/fapi/v1/listenKey", {}
    elif kind == "delete":
        method, values = "DELETE", {"symbol": "BTCUSDT", "origClientOrderId": "one"}
    signed = prepare_signed_query(values, api_key=KEY, secret=SECRET, timestamp_ms=17)
    with pytest.raises(LocalQuotaStopped):
        owner.prepare(
            method,
            path,
            signed.query,
            signed.api_key,
            intent=None if kind == "no-pin" else IntentPin(uuid4(), "a" * 64),
        )


def test_memory_capture_count_is_bounded():
    owner = session()
    for i in range(64):
        prepared(owner, "READ", identifier=f"read-{i}")
    with pytest.raises(LocalQuotaStopped, match="PENDING_CAPTURE_LIMIT"):
        prepared(owner, "READ", identifier="one-more")


def test_session_scope_mismatch_precedes_journal_or_simulation_changes(tmp_path):
    journal = create(tmp_path)
    owner = CredentialSession(
        "DEMO-other", "DEMO-scope", KEY, SECRET, enable_synthetic_contract=True
    )
    reply = responder()
    with pytest.raises(LocalQuotaStopped, match="SCOPE_MISMATCH"):
        run(journal, owner, prepared(owner), reply)
    assert reply.calls == 0 and len(records(tmp_path)) == 1


def test_concurrent_use_of_one_capture_calls_synthetic_responder_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    journal, owner, reply = create(tmp_path), session(), responder()
    capture = prepared(owner)

    def attempt():
        try:
            run(journal, owner, capture, reply)
            return "observed"
        except LocalQuotaStopped:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(results) == ["denied", "observed"]
    assert reply.calls == 1 and review_synthetic_journal(tmp_path)["operations"] == 1


@pytest.mark.parametrize(
    "response", [RESTResponse(True, b"{}", {}), RESTResponse(200, b"x" * 4194305, {})]
)
def test_invalid_response_after_dispatch_is_unknown_and_not_returned(tmp_path, response):
    journal, owner = create(tmp_path), session()
    reply = SyntheticResponder(response)
    with pytest.raises(LocalQuotaStopped, match="UNCERTAIN_NO_RETRY"):
        run(journal, owner, prepared(owner), reply)
    assert reply.calls == 1 and review_synthetic_journal(tmp_path)["uncertain_outcome"]


@pytest.mark.parametrize("kind", ["symlink", "public-mode"])
def test_unsafe_log_paths_are_rejected_and_writer_poisoned(tmp_path, kind):
    journal, owner, reply = create(tmp_path), session(), responder()
    if kind == "symlink":
        journal.path.unlink()
        journal.path.symlink_to(tmp_path / "missing")
    else:
        journal.path.chmod(0o644)
    with pytest.raises(LocalQuotaStopped, match="TIP_MISMATCH"):
        run(journal, owner, prepared(owner), reply)
    with pytest.raises(LocalQuotaStopped):
        review_synthetic_journal(tmp_path)
    assert reply.calls == 0


@pytest.mark.parametrize("elapsed", [43200, 43201, -1])
def test_expired_or_backwards_owner_clock_rejects_prepared_capture_before_simulation(
    tmp_path, monkeypatch, elapsed
):
    journal, owner, reply = create(tmp_path), session(), responder()
    capture = prepared(owner)
    monkeypatch.setattr(module.time, "monotonic", lambda: owner._created + elapsed)
    with pytest.raises(LocalQuotaStopped, match="EXPIRED"):
        run(journal, owner, capture, reply)
    assert reply.calls == 0 and len(records(tmp_path)) == 1
