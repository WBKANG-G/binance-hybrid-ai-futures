"""Offline input consistency and redaction; fixtures cannot establish a financial proof."""

import copy
import json

import pytest

from scripts import review_runtime_confirmation_report as reader
from scripts.review_tp_minute_budget import review as trace_review


def report():
    return dict(
        synthetic_only=True,
        closed_runtime=True,
        startup_holds_accelerated=True,
        observation_seconds=0.8,
        unsealed_flow_count=0,
        confirmation_scheduler=dict(
            confirmation_started=True,
            confirmation_call_active=False,
            confirmation_closed=True,
            repeated_calls_refused=1,
            closed_calls_refused=0,
            start_scope="ONE_CALL_PER_OWNED_COORDINATOR",
            reported_outcome="PROOF_VERIFIED_REVIEW_ONLY",
            returned_flat_confirmation="VERIFIED_FLAT",
            quota_seal_failed=False,
            session_failed=False,
            bridge_degraded=False,
            completed_confirmation_attempts=1,
            quota_seal_verified_from_records=False,
            financial_database_rechecked=False,
            next_action="KEEP_PAUSED_REVIEW_ONLY",
            binding_rejections=0,
            start_deadline_seconds=120,
            completed_attempt_limit=4,
            deferral_limit=120,
            deferrals=0,
            stop_reason="ORIGINAL_PROOF_VERIFIED",
            in_flight_work_force_canceled=False,
            whole_function_wall_time_limit_proven=False,
            execution_approved=False,
        ),
        result=dict(
            flat_confirmation="VERIFIED_FLAT",
            final_paused=True,
            orders_enabled=False,
            live_trading_enabled=False,
            new_entry_post_count=0,
            operator_exit_post_attempts=0,
        ),
        trace=[
            dict(
                seq=1,
                elapsed_ms=0,
                minute=0,
                method="GET",
                path="/fapi/v1/time",
                modeled_request_weight=1,
                used_weight_1m_model=1,
                status=200,
            )
        ],
    )


def set_field(data, path, value):
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


@pytest.mark.parametrize(
    "path,value,code",
    [
        (["confirmation_scheduler"], None, "CONFIRMATION_FIELDS_REQUIRED"),
        (["confirmation_scheduler", "quota_seal_failed"], None, "BOOLEAN_FIELD_REQUIRED"),
        (["confirmation_scheduler", "completed_confirmation_attempts"], True, "COUNT_INVALID"),
        (
            ["confirmation_scheduler", "completed_confirmation_attempts"],
            5,
            "ATTEMPT_LIMIT_MISMATCH",
        ),
        (["confirmation_scheduler", "deferrals"], 121, "ATTEMPT_LIMIT_MISMATCH"),
        (["confirmation_scheduler", "completed_attempt_limit"], 3, "POLICY_MISMATCH"),
        (
            ["confirmation_scheduler", "execution_approved"],
            True,
            "UNSUPPORTED_APPROVAL_OR_PROOF_CLAIM",
        ),
        (
            ["confirmation_scheduler", "financial_database_rechecked"],
            True,
            "UNSUPPORTED_APPROVAL_OR_PROOF_CLAIM",
        ),
        (
            ["confirmation_scheduler", "quota_seal_verified_from_records"],
            True,
            "UNSUPPORTED_APPROVAL_OR_PROOF_CLAIM",
        ),
        (["closed_runtime"], False, "CLOSED_REPORT_REQUIRED"),
        (["confirmation_scheduler", "confirmation_call_active"], True, "FINALIZATION_INCOMPLETE"),
        (["confirmation_scheduler", "confirmation_closed"], False, "FINALIZATION_INCOMPLETE"),
        (["confirmation_scheduler", "reported_outcome"], "FAKE_SUCCESS", "OUTCOME_MISMATCH"),
        (
            ["confirmation_scheduler", "returned_flat_confirmation"],
            "UNKNOWN",
            "PROOF_REASON_MISMATCH",
        ),
        (["result", "flat_confirmation"], "UNKNOWN", "RESULT_PROOF_MISMATCH"),
        (["confirmation_scheduler", "quota_seal_failed"], True, "SEAL_FAILURE_FLAGS_MISMATCH"),
        (["result", "final_paused"], False, "PAUSED_RESULT_REQUIRED"),
        (["result", "orders_enabled"], True, "DISABLED_RESULT_REQUIRED"),
        (["result", "live_trading_enabled"], True, "DISABLED_RESULT_REQUIRED"),
        (["unsealed_flow_count"], True, "COUNT_INVALID"),
        (["result", "new_entry_post_count"], 1, "ORDER_POST_COUNT_MISMATCH"),
        (["confirmation_scheduler", "stop_reason"], {}, "STOP_REASON_INVALID"),
        (["confirmation_scheduler", "returned_flat_confirmation"], {}, "PROOF_STATUS_INVALID"),
        (
            ["confirmation_scheduler", "completed_confirmation_attempts"],
            0,
            "PROOF_ATTEMPT_MISMATCH",
        ),
        (["observation_seconds"], True, "OBSERVATION_INVALID"),
        pytest.param(
            ["observation_seconds"], 10**1000, "OBSERVATION_INVALID", id="oversized-observation"
        ),
        (["startup_holds_accelerated"], None, "STARTUP_SCOPE_REQUIRED"),
        (["synthetic_only"], False, "SYNTHETIC_REPORT_REQUIRED"),
        (["trace", 0, "modeled_request_weight"], 99, "TRACE_WEIGHT_MODEL_MISMATCH"),
    ],
)
def test_contradiction_or_missing_scope_is_rejected(path, value, code):
    data = report()
    set_field(data, path, value)
    with pytest.raises(reader.ConfirmationReportInvalid, match="^" + code + "$"):
        reader.review(data)


def seal_failed(data, unsealed):
    d = data["confirmation_scheduler"]
    d.update(
        quota_seal_failed=True,
        session_failed=True,
        bridge_degraded=True,
        reported_outcome="PROOF_VERIFIED_QUOTA_SEAL_FAILED",
    )
    data["unsealed_flow_count"] = unsealed


@pytest.mark.parametrize("unsealed", [0, 1])
def test_seal_failure_is_not_cleared_by_a_zero_unsealed_counter(unsealed):
    data = report()
    seal_failed(data, unsealed)
    result = reader.review(data)
    assert (
        result["attention_required"]
        and result["disposition"] == "REVIEW_REQUIRED_QUOTA_SEAL_FAILED"
    )
    assert (
        result["returned_flat_confirmation"] == "VERIFIED_FLAT" and not result["execution_approved"]
    )


def test_unsealed_reservation_is_not_a_clean_runtime_even_without_failure_flag():
    data = report()
    data["unsealed_flow_count"] = 1
    assert reader.review(data)["disposition"] == "REVIEW_REQUIRED_UNSEALED_FLOWS"


@pytest.mark.parametrize(
    "kind", ["session", "bridge", "cancel", "not_started", "unresolved", "counter", "headroom"]
)
def test_consistent_incomplete_or_uncertain_reports_require_review(kind):
    data = report()
    d = data["confirmation_scheduler"]
    if kind == "session":
        d.update(session_failed=True, reported_outcome="PROOF_VERIFIED_SESSION_FAILED")
        expected = "RUNTIME_FAILURE"
    elif kind == "bridge":
        d.update(bridge_degraded=True, reported_outcome="PROOF_VERIFIED_RUNTIME_DEGRADED")
        expected = "RUNTIME_FAILURE"
    elif kind in {"cancel", "not_started"}:
        d.update(returned_flat_confirmation="UNKNOWN", completed_confirmation_attempts=0)
        data["result"]["flat_confirmation"] = "UNKNOWN"
        if kind == "cancel":
            d.update(
                stop_reason="CALLER_CANCELLED",
                reported_outcome="CALLER_CANCELLED_PROOF_UNCONFIRMED",
            )
        else:
            d.update(
                confirmation_started=False,
                stop_reason="NOT_STARTED",
                reported_outcome="NOT_STARTED",
            )
        expected = "PROOF_UNCONFIRMED"
    elif kind == "unresolved":
        data["trace"][0]["status"] = None
        expected = "TRACE_UNRESOLVED"
    elif kind == "counter":
        data["trace"][0]["used_weight_1m_model"] = 0
        expected = "TRACE_INCONSISTENT"
    else:
        data["trace"] = [
            dict(
                seq=i,
                elapsed_ms=0,
                minute=0,
                method="GET",
                path="/fapi/v1/openOrders",
                modeled_request_weight=40,
                used_weight_1m_model=40 * i,
                status=200,
            )
            for i in range(1, 151)
        ]
        expected = "TRACE_HEADROOM"
    result = reader.review(data)
    assert result["disposition"] == "REVIEW_REQUIRED_" + expected
    assert result["attention_required"] and not result["execution_approved"]


def test_clean_input_is_only_consistency_and_never_authenticated_current_state():
    data = report()
    data["unknown"] = {"private_field": "SYNTHETIC_DO_NOT_ECHO"}
    before = copy.deepcopy(data)
    result = reader.review(data)
    assert result["disposition"] == "CONSISTENT_REPORT_ONLY_NO_APPROVAL"
    assert data == before and "SYNTHETIC_DO_NOT_ECHO" not in json.dumps(result)
    for field in [
        "input_authenticity_verified",
        "financial_proof_reverified",
        "current_exchange_state_verified",
        "order_payload_scopes_verified",
        "execution_approved",
    ]:
        assert result[field] is False


def test_minute_review_is_intentionally_not_confirmation_review():
    data = report()
    data.pop("confirmation_scheduler")
    assert trace_review(data)["conclusion"] == "MEASURED_REQUESTS_FIT_ASSUMPTIONS_ONLY"
    with pytest.raises(reader.ConfirmationReportInvalid, match="CONFIRMATION_FIELDS_REQUIRED"):
        reader.review(data)


@pytest.mark.parametrize(
    "raw,code",
    [
        (b'{"a":1,"a":2}', "JSON_DUPLICATE_KEY"),
        (b'{"nested":{"a":1,"a":2}}', "JSON_DUPLICATE_KEY"),
        (b'{"a":NaN}', "JSON_NONFINITE"),
        (b'{"a":Infinity}', "JSON_NONFINITE"),
        (b"{", "JSON_INVALID"),
        (b"\xff", "JSON_INVALID"),
    ],
)
def test_strict_loader_rejects_ambiguous_json(raw, code):
    with pytest.raises(reader.ConfirmationReportInvalid, match=code):
        reader.load(raw)


def test_loader_size_and_depth_are_bounded(monkeypatch):
    with pytest.raises(reader.ConfirmationReportInvalid, match="JSON_DEPTH_LIMIT"):
        reader.load(b"[" * 2000 + b"]" * 2000)
    monkeypatch.setattr(reader, "MAX_BYTES", 4)
    with pytest.raises(reader.ConfirmationReportInvalid, match="FILE_TOO_LARGE"):
        reader.load(b" " * 5)


@pytest.mark.parametrize("kind,status", [("clean", 0), ("seal", 3), ("invalid", 2)])
def test_cli_distinguishes_consistency_attention_and_invalid(tmp_path, capsys, kind, status):
    data = report()
    if kind == "seal":
        seal_failed(data, 1)
    elif kind == "invalid":
        data.pop("confirmation_scheduler")
    path = tmp_path / "report.json"
    path.write_text(json.dumps(data))
    assert reader.main([str(path)]) == status
    output = json.loads(capsys.readouterr().out)
    assert (
        output["execution_approved"] is False and output["next_action"] == "KEEP_PAUSED_REVIEW_ONLY"
    )


def test_missing_file_error_does_not_echo_path(tmp_path, capsys):
    path = tmp_path / "SYNTHETIC_PRIVATE_PATH.json"
    assert reader.main([str(path)]) == 2
    output = capsys.readouterr().out
    assert "SYNTHETIC_PRIVATE_PATH" not in output and json.loads(output)["code"] == "INPUT_INVALID"


def test_depth_guard_ignores_escaped_quotes_and_brackets_in_strings():
    payload = {"value": "[" * 100 + '\\"' + "]" * 100}
    assert reader.load(json.dumps(payload).encode()) == payload


def test_numeric_overflow_is_nonfinite_even_in_unknown_fields():
    with pytest.raises(reader.ConfirmationReportInvalid, match="JSON_NONFINITE"):
        reader.load(b'{"ignored":1e9999}')


def test_invalid_cli_arguments_are_sanitized(capsys):
    assert reader.main(["--unexpected", "SYNTHETIC_PRIVATE_ARG"]) == 2
    output = capsys.readouterr()
    assert "SYNTHETIC_PRIVATE_ARG" not in output.out + output.err
    assert json.loads(output.out)["code"] == "ARGUMENTS_INVALID"
