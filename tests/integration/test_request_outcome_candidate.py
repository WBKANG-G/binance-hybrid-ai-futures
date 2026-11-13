"""Verified PG/file evidence with synthetic bodies; no venue or Windows access."""

# ruff: noqa: F401,F811
import copy
import json

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo_order_quota import LocalQuotaStopped
from services.execution.demo_request_outcome_candidate import (
    RequestExpectation,
    preserve_response_artifact,
    review_request_outcome,
    review_response_artifact,
)
from tests.integration.test_demo_read_audit import audited, records
from tests.integration.test_response_evidence_candidate import evidenced, transport
from tests.integration.test_runtime_read_accounting import financial
from tests.integration.test_shared_weight_checkpoint import anchored, demo_system
from tests.unit.test_quota_startup_read_only import files


def expected(role="ENTRY", **kw):
    return RequestExpectation(
        **(
            dict(
                role=role,
                client_id="synthetic-request-1",
                side="BUY",
                order_type="STOP_MARKET"
                if role == "STOP_LOSS"
                else "MARKET"
                if role == "EXIT"
                else "LIMIT",
                quantity=None if role == "STOP_LOSS" else "0.0007",
            )
            | kw
        )
    )


def payload(role="ENTRY", **kw):
    if role == "STOP_LOSS":
        return (
            dict(
                symbol="BTCUSDT",
                side="BUY",
                positionSide="BOTH",
                clientAlgoId="synthetic-request-1",
                orderType="STOP_MARKET",
                closePosition=True,
                algoId=123,
                algoStatus="CANCELED",
            )
            | kw
        )
    return (
        dict(
            symbol="BTCUSDT",
            side="BUY",
            positionSide="BOTH",
            clientOrderId="synthetic-request-1",
            type="MARKET" if role == "EXIT" else "LIMIT",
            reduceOnly=role == "EXIT",
            orderId=123,
            origQty="0.0007",
            executedQty="0.0007",
            status="FILLED",
        )
        | kw
    )


def wire(role="ENTRY", *, numeric=False):
    query = (
        "clientAlgoId=synthetic-request-1"
        if role == "STOP_LOSS"
        else (
            "symbol=BTCUSDT&orderId=123"
            if numeric
            else "symbol=BTCUSDT&origClientOrderId=synthetic-request-1"
        )
    )
    return (
        "GET",
        "/fapi/v1/algoOrder" if role == "STOP_LOSS" else "/fapi/v1/order",
        SecretStr(query + "&timestamp=1&recvWindow=5000&signature=" + "a" * 64),
        SecretStr("PRIVATE_KEY"),
    )


def observe(case, value, role="ENTRY", *, status=200, numeric=False):
    body = value if isinstance(value, bytes) else json.dumps(value, separators=(",", ":")).encode()
    r = transport(case, lambda *args: RESTResponse(status, body, {"x-mbx-used-weight-1m": "100"}))
    if status == 200:
        r(*wire(role, numeric=numeric))
    else:
        with pytest.raises(LocalQuotaStopped):
            r(*wire(role, numeric=numeric))
    return body


def args(case, body, expectation=None, sequence=1):
    a, _, store, shared = case
    return (store, a[3], shared.scope, a[4], "DEMO-test", sequence, body, expectation or expected())


@pytest.mark.parametrize("role", ["ENTRY", "EXIT"])
def test_terminal_observation_is_as_of_and_never_an_archive_or_reconciliation(evidenced, role):
    body = observe(evidenced, payload(role), role)
    a = evidenced[0]
    before, disk, audit = financial(a), files(a[3]), records(a)
    report = review_request_outcome(*args(evidenced, body, expected(role)))
    assert report["terminal_order_observed"] and report["response_identity_matches"]
    assert report["fills_or_exposure_followup_required"]
    assert report["request_timestamp_ms"] == "1"
    assert not any(
        report[k]
        for k in (
            "remote_request_resolution_verified",
            "remote_account_identity_verified",
            "local_submission_binding_verified",
            "fresh_exchange_state_verified",
            "position_closed_verified",
            "archive_authorized",
            "no_send_verified",
            "reconciliation_performed",
            "orders_enabled",
            "execution_approved",
        )
    )
    assert financial(a) == before and files(a[3]) == disk and records(a) == audit
    assert "PRIVATE_KEY" not in str(report) and "synthetic-request-1" not in str(report)


@pytest.mark.parametrize(
    "status,filled,terminal",
    [
        ("NEW", "0", False),
        ("PARTIALLY_FILLED", "0.0001", False),
        ("CANCELED", "0", True),
        ("CANCELED", "0.0001", True),
        ("EXPIRED", "0.0001", True),
        ("EXPIRED_IN_MATCH", "0", True),
        ("REJECTED", "0", True),
    ],
)
def test_regular_status_quantity_classification(evidenced, status, filled, terminal):
    body = observe(evidenced, payload(status=status, executedQty=filled))
    report = review_request_outcome(*args(evidenced, body))
    assert report["terminal_order_observed"] is terminal
    assert report["fills_or_exposure_followup_required"] is (filled != "0")
    assert not report["position_closed_verified"] and not report["no_send_verified"]


@pytest.mark.parametrize(
    "patch",
    [
        {"symbol": "ETHUSDT"},
        {"clientOrderId": "another"},
        {"side": "SELL"},
        {"type": "MARKET"},
        {"reduceOnly": True},
        {"positionSide": "LONG"},
        {"origQty": "0.0008"},
        {"executedQty": "0.0008"},
        {"executedQty": "0.0001"},
        {"status": "UNKNOWN"},
        {"status": "NEW"},
        {"status": "REJECTED"},
        {"status": "PARTIALLY_FILLED"},
        {"executedQty": "NaN"},
        {"executedQty": "1e-4"},
        {"executedQty": 0.0007},
        {"orderId": True},
        {"orderId": 0},
        {"orderId": "-1"},
        {"reduceOnly": "false"},
    ],
)
def test_mismatch_or_inconsistent_success_body_is_rejected(evidenced, patch):
    body = observe(evidenced, payload(**patch))
    with pytest.raises(LocalQuotaStopped):
        review_request_outcome(*args(evidenced, body))


@pytest.mark.parametrize(
    "status", ["NEW", "CANCELED", "TRIGGERED", "FINISHED", "EXPIRED", "REJECTED"]
)
def test_algo_observation_never_substitutes_for_underlying_order_execution(evidenced, status):
    body = observe(evidenced, payload("STOP_LOSS", algoStatus=status), "STOP_LOSS")
    report = review_request_outcome(*args(evidenced, body, expected("STOP_LOSS")))
    assert report["observation"] == "ALGO_STATUS_OBSERVED"
    assert report["underlying_execution_proof_required"]
    assert not report["terminal_order_observed"] and not report["archive_authorized"]


@pytest.mark.parametrize(
    "patch",
    [
        {"clientAlgoId": "other"},
        {"closePosition": "true"},
        {"orderType": "TAKE_PROFIT_MARKET"},
        {"algoId": False},
        {"algoStatus": "UNKNOWN"},
        {"algoStatus": []},
    ],
)
def test_algo_identity_and_supported_shape_are_required(evidenced, patch):
    body = observe(evidenced, payload("STOP_LOSS", **patch), "STOP_LOSS")
    with pytest.raises(LocalQuotaStopped):
        review_request_outcome(*args(evidenced, body, expected("STOP_LOSS")))


@pytest.mark.parametrize("code", [-2011, -2013, -1000])
def test_http_not_found_or_error_is_unresolved_even_when_body_digest_matches(evidenced, code):
    body = observe(evidenced, {"code": code, "msg": "private-error-detail"}, status=400)
    before = files(evidenced[0][3])
    report = review_request_outcome(*args(evidenced, body))
    assert report["observation"] == (
        "NOT_FOUND_UNRESOLVED" if code in {-2011, -2013} else "HTTP_ERROR_UNRESOLVED"
    )
    assert (
        not report["response_identity_matches"] and not report["remote_request_resolution_verified"]
    )
    assert not report["no_send_verified"] and "private-error-detail" not in str(report)
    assert files(evidenced[0][3]) == before


@pytest.mark.parametrize(
    "body",
    [b"[]", b"null", b"{", b"\xff", b'{"x":NaN}', b'{"x":1,"x":2}', b'{"nested":{"x":1,"x":2}}'],
)
def test_malformed_duplicate_or_nonfinite_json_cannot_be_used(evidenced, body):
    observe(evidenced, body)
    with pytest.raises(LocalQuotaStopped):
        review_request_outcome(*args(evidenced, body))


def test_numeric_query_requires_expected_venue_id_and_response_client_id(evidenced):
    body = observe(evidenced, payload(), numeric=True)
    with pytest.raises(LocalQuotaStopped, match="QUERY_ID_MISMATCH"):
        review_request_outcome(*args(evidenced, body))
    assert review_request_outcome(*args(evidenced, body, expected(venue_order_id="123")))[
        "terminal_order_observed"
    ]
    with pytest.raises(LocalQuotaStopped):
        review_request_outcome(*args(evidenced, body, expected(venue_order_id="124")))


def test_wrong_body_reservation_sequence_or_role_is_rejected(evidenced):
    body = observe(evidenced, payload())
    with pytest.raises(LocalQuotaStopped, match="BODY_MISMATCH"):
        review_request_outcome(*args(evidenced, body + b" "))
    with pytest.raises(LocalQuotaStopped, match="OBSERVED_FINISH_REQUIRED"):
        review_request_outcome(*args(evidenced, body, sequence=0))
    with pytest.raises(LocalQuotaStopped, match="OBSERVED_FINISH_REQUIRED"):
        review_request_outcome(*args(evidenced, body, sequence=3))
    with pytest.raises(LocalQuotaStopped, match="ROUTE_ROLE_MISMATCH"):
        review_request_outcome(*args(evidenced, body, expected("STOP_LOSS")))


def test_private_artifact_import_is_explicit_idempotent_and_readonly_review_rechecks_chain(
    evidenced, tmp_path_factory
):
    body = observe(evidenced, payload())
    root = tmp_path_factory.mktemp("private-artifacts")
    root.chmod(0o700)
    a = evidenced[0]
    before, disk, old = financial(a), files(a[3]), records(a)
    with pytest.raises(LocalQuotaStopped, match="EXPLICIT_ARTIFACT_IMPORT"):
        preserve_response_artifact(None, None, None, None, None, None, None, None, None)
    report = preserve_response_artifact(root, *args(evidenced, body), apply=True)
    path = root / (report["artifact_reference"] + ".response-body")
    assert path.read_bytes() == body and path.stat().st_mode & 0o777 == 0o600
    again = preserve_response_artifact(root, *args(evidenced, body), apply=True)
    assert not again["artifact_created"] and not again["filesystem_changed"]
    call = (root, report["artifact_reference"], *args(evidenced, body)[:-2], expected())
    checked = review_response_artifact(*call)
    assert checked["response_body_artifact_stored"] and not checked["filesystem_changed"]
    assert financial(a) == before and files(a[3]) == disk and records(a) == old
    witness = evidenced[2].witness(a[3], evidenced[3].scope)
    witness.write_bytes(witness.read_bytes() + b"{}\n")
    with pytest.raises(LocalQuotaStopped, match="WITNESS_MISMATCH"):
        review_response_artifact(*call)


@pytest.mark.parametrize("kind", ["tamper", "symlink", "public-file", "public-directory"])
def test_unsafe_artifacts_are_never_repaired(evidenced, tmp_path, tmp_path_factory, kind):
    body = observe(evidenced, payload())
    root = tmp_path_factory.mktemp("artifacts")
    root.chmod(0o700)
    report = preserve_response_artifact(root, *args(evidenced, body), apply=True)
    path = root / (report["artifact_reference"] + ".response-body")
    if kind == "tamper":
        path.write_bytes(b"{}")
    elif kind == "symlink":
        path.unlink()
        path.symlink_to(tmp_path / "missing")
    elif kind == "public-file":
        path.chmod(0o644)
    else:
        root.chmod(0o755)
    before = path.lstat()
    with pytest.raises((LocalQuotaStopped, OSError)):
        preserve_response_artifact(root, *args(evidenced, body), apply=True)
    assert path.lstat() == before


def test_body_cannot_be_transplanted_to_another_account_or_expected_request(evidenced):
    body = observe(evidenced, payload())
    values = list(args(evidenced, body))
    values[4] = "DEMO-another"
    with pytest.raises(LocalQuotaStopped, match="BINDING_INVALID"):
        review_request_outcome(*values)
    with pytest.raises(LocalQuotaStopped, match="QUERY_ID_MISMATCH"):
        review_request_outcome(*args(evidenced, body, expected(client_id="another")))
    with pytest.raises(LocalQuotaStopped, match="RESPONSE_VENUE_ID_MISMATCH"):
        review_request_outcome(*args(evidenced, body, expected(venue_order_id="124")))


def test_artifact_reference_cannot_be_renamed_to_another_evidence_binding(
    evidenced, tmp_path_factory
):
    body = observe(evidenced, payload())
    root = tmp_path_factory.mktemp("renamed-artifact")
    report = preserve_response_artifact(root, *args(evidenced, body), apply=True)
    wrong = "a" * 64
    (root / (wrong + ".response-body")).write_bytes(body)
    (root / (wrong + ".response-body")).chmod(0o600)
    with pytest.raises(LocalQuotaStopped, match="ARTIFACT_BINDING_MISMATCH"):
        review_response_artifact(root, wrong, *args(evidenced, body)[:-2], expected())
    with pytest.raises(LocalQuotaStopped, match="ARTIFACT_REFERENCE_INVALID"):
        review_response_artifact(root, "../escape", *args(evidenced, body)[:-2], expected())
    assert report["artifact_reference"] != wrong


def test_artifacts_must_be_separate_from_runtime_files(evidenced):
    body = observe(evidenced, payload())
    root = evidenced[0][3]
    before = files(root)
    with pytest.raises(LocalQuotaStopped, match="SEPARATE_ARTIFACT_DIRECTORY"):
        preserve_response_artifact(root, *args(evidenced, body), apply=True)
    assert files(root) == before


def test_partial_artifact_write_is_not_automatically_completed(
    evidenced, tmp_path_factory, monkeypatch
):
    import services.execution.demo_request_outcome_candidate as module

    body = observe(evidenced, payload())
    root = tmp_path_factory.mktemp("partial-artifact")

    def partial(path, raw):
        path.write_bytes(raw[:7])
        path.chmod(0o600)
        raise OSError("SYNTHETIC_PARTIAL_IMPORT")

    monkeypatch.setattr(module, "_write_new", partial)
    with pytest.raises(OSError, match="SYNTHETIC_PARTIAL_IMPORT"):
        preserve_response_artifact(root, *args(evidenced, body), apply=True)
    paths = list(root.iterdir())
    assert len(paths) == 1 and paths[0].read_bytes() == body[:7]
    with pytest.raises(LocalQuotaStopped, match="EXISTING_ARTIFACT_MISMATCH"):
        preserve_response_artifact(root, *args(evidenced, body), apply=True)
    assert paths[0].read_bytes() == body[:7]


def test_artifact_directory_fsync_failure_does_not_return_success(
    evidenced, tmp_path_factory, monkeypatch
):
    import services.execution.demo_request_outcome_candidate as module

    body = observe(evidenced, payload())
    root = tmp_path_factory.mktemp("fsync-artifact")

    def fail(path):
        raise OSError("SYNTHETIC_DIRECTORY_FSYNC_FAILURE")

    monkeypatch.setattr(module, "_sync_directory", fail)
    with pytest.raises(OSError, match="SYNTHETIC_DIRECTORY_FSYNC_FAILURE"):
        preserve_response_artifact(root, *args(evidenced, body), apply=True)
    assert len(list(root.iterdir())) == 1
    assert list(root.iterdir())[0].read_bytes() == body


def test_file_fsync_failure_does_not_return_artifact_success(
    evidenced, tmp_path_factory, monkeypatch
):
    import services.execution.demo_request_outcome_candidate as module

    body = observe(evidenced, payload())
    root = tmp_path_factory.mktemp("file-fsync-artifact")

    def fail(fd):
        raise OSError("SYNTHETIC_FILE_FSYNC_FAILURE")

    monkeypatch.setattr(module.os, "fsync", fail)
    with pytest.raises(OSError, match="SYNTHETIC_FILE_FSYNC_FAILURE"):
        preserve_response_artifact(root, *args(evidenced, body), apply=True)
    assert len(list(root.iterdir())) == 1
