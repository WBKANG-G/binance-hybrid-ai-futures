"""V2 reviews reported distinct outcomes; it neither authenticates nor reruns proof."""

import copy
import json

import pytest

from scripts import review_runtime_confirmation_report as v1
from scripts import review_runtime_outcomes as v2
from tests.unit.test_runtime_report_reader import report, set_field


def fixture(split=False, partial=False):
    data = report()
    data["result"].update(tp_trigger_check=v2.TP_ACCEPTED, failure="NONE")
    if partial:
        data["result"].update(tp_trigger_check="NOT_ACCEPTED", failure="TP_FILL_NOT_OBSERVED")
    if split:
        data.update(
            mode="receiver_failure",
            receiver_fault={"deferred": True, "closed": True},
            runtime_budget_untrusted=False,
            runtime_budget_stopped=False,
            runtime_budget_reason="NONE",
        )
        data["result"].update(
            flat_confirmation="UNKNOWN",
            tp_trigger_check="NOT_ACCEPTED",
            failure="DEMO_SESSION_OR_QUERY_FAILED",
            history_and_baselines_preserved=True,
            position_state="CLOSED",
            private_session_diagnostic={"stage": "RECEIVE", "code": "SOCKET_CLOSED"},
        )
        data["confirmation_scheduler"].update(
            session_failed=True,
            reported_outcome="PROOF_VERIFIED_SESSION_FAILED",
            observation_resource_wait=dict(
                deferrals=1,
                start_deadline_seconds=120,
                deferral_limit=120,
                full_reconciliation_omitted=False,
                flat_watch_is_financial_proof=False,
                execution_approved=False,
            ),
        )
    return data


def raw_fixture(**kwargs):
    return json.dumps(fixture(**kwargs)).encode()


def test_v2_split_preserves_failed_trial_and_both_input_and_v1_refusal():
    data = fixture(split=True)
    before = copy.deepcopy(data)
    with pytest.raises(v1.ConfirmationReportInvalid, match="RESULT_PROOF_MISMATCH"):
        v1.review(data)
    checked = v2.review(data)
    assert data == before and checked["outcomes_separated"] is True
    assert checked["original_trial_flat_confirmation"] == "UNKNOWN"
    assert checked["later_returned_flat_confirmation"] == "VERIFIED_FLAT"
    assert checked["original_trial_reported_accepted"] is False
    assert checked["original_trial_reclassified"] is False
    assert checked["attention_required"] is True and not checked["execution_approved"]
    assert checked["disposition"] == "REVIEW_REQUIRED_FAILED_TRIAL_LATER_PROOF_REPORTED"
    assert "returned_flat_confirmation" not in checked and "reported_outcome" not in checked
    assert not checked["financial_proof_reverified"] and not checked["input_authenticity_verified"]


@pytest.mark.parametrize("partial,attention", [(False, False), (True, True)])
def test_matching_reports_keep_proof_separate_from_reported_tp_acceptance(partial, attention):
    checked = v2.review(fixture(partial=partial))
    assert checked["attention_required"] is attention
    assert checked["original_trial_reported_accepted"] is (not partial)
    assert checked["outcomes_separated"] is False and checked["execution_approved"] is False


@pytest.mark.parametrize(
    "path,value",
    [
        (["mode"], "unknown"),
        (["receiver_fault"], None),
        (["receiver_fault", "closed"], False),
        (["receiver_fault", "deferred"], 1),
        (["confirmation_scheduler", "session_failed"], False),
        (["confirmation_scheduler", "quota_seal_failed"], True),
        (["confirmation_scheduler", "bridge_degraded"], True),
        (["confirmation_scheduler", "reported_outcome"], "PROOF_VERIFIED_REVIEW_ONLY"),
        (["result", "tp_trigger_check"], v2.TP_ACCEPTED),
        (["result", "failure"], "NONE"),
        (["result", "history_and_baselines_preserved"], False),
        (["result", "position_state"], "OPEN_PROTECTED"),
        (["runtime_budget_untrusted"], True),
        (["runtime_budget_stopped"], True),
        (["runtime_budget_reason"], "OTHER"),
        (["result", "private_session_diagnostic", "code"], "OTHER"),
        (["result", "private_session_diagnostic", "stage"], "PING"),
        (["confirmation_scheduler", "observation_resource_wait"], None),
        (["confirmation_scheduler", "observation_resource_wait", "start_deadline_seconds"], 120.0),
        (["confirmation_scheduler", "observation_resource_wait", "deferral_limit"], 119),
        (["confirmation_scheduler", "observation_resource_wait", "deferrals"], 0),
        (["confirmation_scheduler", "observation_resource_wait", "deferrals"], True),
        (
            ["confirmation_scheduler", "observation_resource_wait", "full_reconciliation_omitted"],
            True,
        ),
        (
            [
                "confirmation_scheduler",
                "observation_resource_wait",
                "flat_watch_is_financial_proof",
            ],
            True,
        ),
        (["confirmation_scheduler", "observation_resource_wait", "execution_approved"], True),
        (["unsealed_flow_count"], 1),
        (["confirmation_scheduler", "completed_confirmation_attempts"], 0),
        (["confirmation_scheduler", "execution_approved"], True),
        (["result", "final_paused"], False),
        (["result", "orders_enabled"], True),
        (["result", "live_trading_enabled"], True),
        (["trace", 0, "modeled_request_weight"], 2),
        (["synthetic_only"], False),
        (["closed_runtime"], False),
    ],
)
def test_dual_outcome_never_bypasses_scope_or_existing_v1_invariants(path, value):
    data = fixture(split=True)
    set_field(data, path, value)
    with pytest.raises(v1.ConfirmationReportInvalid):
        v2.review(data)


def test_reverse_mismatch_cannot_erase_an_unconfirmed_later_proof():
    data = fixture()
    data["confirmation_scheduler"].update(
        returned_flat_confirmation="UNKNOWN",
        stop_reason="CALLER_CANCELLED",
        reported_outcome="CALLER_CANCELLED_PROOF_UNCONFIRMED",
        completed_confirmation_attempts=0,
    )
    with pytest.raises(v1.ConfirmationReportInvalid, match="DIRECTION"):
        v2.review(data)


def test_trace_inconsistency_remains_a_primary_review_reason():
    data = fixture(split=True)
    data["trace"][0]["used_weight_1m_model"] = 0
    checked = v2.review(data)
    assert checked["disposition"] == "REVIEW_REQUIRED_TRACE_INCONSISTENT"
    assert checked["outcomes_separated"] and checked["attention_required"]


def test_seal_failure_is_not_cleared_by_the_new_policy():
    from tests.unit.test_runtime_report_reader import seal_failed

    data = fixture()
    seal_failed(data, 1)
    checked = v2.review(data)
    assert checked["disposition"] == "REVIEW_REQUIRED_QUOTA_SEAL_FAILED"
    assert checked["original_trial_reported_accepted"] is True
    assert checked["attention_required"] and not checked["execution_approved"]


def test_unknown_data_and_private_tokens_are_not_echoed():
    data = fixture(split=True)
    data["private"] = {"token": "SYNTHETIC_DO_NOT_ECHO"}
    data["result"]["private_session_diagnostic"]["url"] = "SYNTHETIC_DO_NOT_ECHO"
    assert "SYNTHETIC_DO_NOT_ECHO" not in json.dumps(v2.review(data))


def test_v1_and_v2_receipts_are_not_interchangeable():
    raw = raw_fixture()
    one = json.dumps(v1.review_bytes(raw)).encode()
    two = json.dumps(v2.review_bytes(raw)).encode()
    with pytest.raises(v1.ConfirmationReportInvalid, match="VERSION"):
        v2.verify_receipt(raw, one)
    with pytest.raises(v1.ConfirmationReportInvalid, match="VERSION"):
        v1.verify_receipt(raw, two)
    assert v2.verify_receipt(raw, two)["receipt_matches_input"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_approved", True),
        ("execution_approved", 0),
        ("original_trial_reported_accepted", True),
        ("later_returned_flat_confirmation", "UNKNOWN"),
        ("reader_policy", v1.READER_POLICY),
        ("receipt_version", 2.0),
        ("receipt_version", 1),
        ("input_bytes", 0),
        ("extra", False),
    ],
)
def test_receipt_modification_cannot_change_policy_or_upgrade_failed_trial(field, value):
    raw = raw_fixture(split=True)
    checked = v2.review_bytes(raw)
    checked[field] = value
    with pytest.raises(v1.ConfirmationReportInvalid):
        v2.verify_receipt(raw, json.dumps(checked).encode())


def test_receipt_is_bound_to_raw_bytes_and_accepts_only_its_own_formatting_changes():
    raw = raw_fixture(split=True)
    checked = v2.review_bytes(raw)
    verified = v2.verify_receipt(raw, json.dumps(checked, indent=2, sort_keys=True).encode())
    assert verified["attention_required"] and not verified["original_trial_reported_accepted"]
    assert not verified["execution_approved"]
    with pytest.raises(v1.ConfirmationReportInvalid, match="INPUT_MISMATCH"):
        v2.verify_receipt(raw + b"\n", json.dumps(checked).encode())


@pytest.mark.parametrize(
    "split,partial,status", [(False, False, 0), (False, True, 3), (True, False, 3)]
)
def test_cli_save_and_readonly_verify_preserve_input_and_attention(
    tmp_path, capsys, split, partial, status
):
    raw = raw_fixture(split=split, partial=partial)
    source = tmp_path / "report.json"
    target = tmp_path / "receipt.json"
    source.write_bytes(raw)
    assert v2.main([str(source), "--save-receipt", str(target)]) == status
    assert capsys.readouterr().out.encode() == target.read_bytes()
    before = target.read_bytes()
    assert v2.main([str(source), "--verify-receipt", str(target)]) == status
    assert json.loads(capsys.readouterr().out)["receipt_matches_input"]
    assert target.read_bytes() == before and source.read_bytes() == raw
    assert v2.main([str(source), "--save-receipt", str(target)]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "OUTPUT_ALREADY_EXISTS"
    assert target.read_bytes() == before


def test_cli_same_input_and_bad_input_cannot_be_overwritten(tmp_path, capsys):
    source = tmp_path / "report.json"
    source.write_bytes(raw_fixture(split=True))
    raw = source.read_bytes()
    assert v2.main([str(source), "--save-receipt", str(source)]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "OUTPUT_IS_INPUT"
    assert source.read_bytes() == raw
    source.write_bytes(b"{")
    target = tmp_path / "no-receipt.json"
    assert v2.main([str(source), "--save-receipt", str(target)]) == 2
    assert not target.exists() and source.read_bytes() == b"{"


def test_v2_atomic_storage_failure_preserves_original_and_reports_uncertain_publication(
    tmp_path, capsys, monkeypatch
):
    source = tmp_path / "report.json"
    raw = raw_fixture(split=True)
    source.write_bytes(raw)
    target = tmp_path / "receipt.json"
    link = v1.os.link

    def publish_then_fail(*args):
        link(*args)
        raise OSError("SYNTHETIC_PRIVATE_FAILURE")

    monkeypatch.setattr(v1.os, "link", publish_then_fail)
    assert v2.main([str(source), "--save-receipt", str(target)]) == 2
    text = capsys.readouterr().out
    checked = json.loads(text)
    assert checked["storage_publication_attempted"] and not checked["storage_publication_confirmed"]
    assert (
        source.read_bytes() == raw
        and v2.verify_receipt(raw, target.read_bytes())["receipt_matches_input"]
    )
    assert "SYNTHETIC_PRIVATE" not in text and not list(tmp_path.glob(".runtime-review-*.tmp"))
