"""Exact input-byte binding and recomputation, without authenticity claims."""

import hashlib
import io
import json

import pytest

from scripts import review_runtime_confirmation_report as reader
from tests.unit.test_runtime_report_reader import report, seal_failed


def raw_report(*, failed=False):
    data = report()
    if failed:
        seal_failed(data, 1)
    return json.dumps(data).encode()


def encode(receipt):
    return json.dumps(receipt).encode()


def test_receipt_binds_exact_reviewed_bytes_and_is_deterministic():
    raw = raw_report()
    receipt = reader.review_bytes(raw)
    assert receipt == reader.review_bytes(raw)
    assert receipt["input_sha256"] == hashlib.sha256(raw).hexdigest()
    assert receipt["input_bytes"] == len(raw)
    assert receipt["reader_policy"] == reader.READER_POLICY and receipt["receipt_version"] == 1
    assert not receipt["receipt_authenticity_verified"] and not receipt["execution_approved"]


@pytest.mark.parametrize("failed", [False, True])
def test_matching_receipt_keeps_attention_and_never_authenticates(failed):
    raw = raw_report(failed=failed)
    result = reader.verify_receipt(raw, encode(reader.review_bytes(raw)))
    assert result["receipt_matches_input"] and result["attention_required"] is failed
    for field in [
        "input_authenticity_verified",
        "receipt_authenticity_verified",
        "current_exchange_state_verified",
        "financial_proof_reverified",
        "runtime_admission_changed",
        "execution_approved",
    ]:
        assert result[field] is False


@pytest.mark.parametrize("change", ["whitespace", "different_report"])
def test_mixed_or_changed_input_cannot_use_original_receipt(change):
    raw = raw_report()
    receipt = encode(reader.review_bytes(raw))
    other = raw + b"\n" if change == "whitespace" else raw_report(failed=True)
    with pytest.raises(reader.ConfirmationReportInvalid, match="RECEIPT_INPUT_MISMATCH"):
        reader.verify_receipt(other, receipt)


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("execution_approved", True, "RECEIPT_RESULT_MISMATCH"),
        ("attention_required", 0, "RECEIPT_RESULT_MISMATCH"),
        ("guarded_modeled_peak", 1.0, "RECEIPT_RESULT_MISMATCH"),
        ("input_sha256", "0" * 64, "RECEIPT_INPUT_MISMATCH"),
        ("input_sha256", "UPPERCASE", "RECEIPT_BINDING_INVALID"),
        ("input_bytes", True, "RECEIPT_BINDING_INVALID"),
        ("input_bytes", 1, "RECEIPT_INPUT_MISMATCH"),
        ("reader_policy", "UNKNOWN_POLICY", "RECEIPT_POLICY_MISMATCH"),
        ("receipt_version", True, "RECEIPT_VERSION_INVALID"),
        ("receipt_version", 2, "RECEIPT_VERSION_INVALID"),
    ],
)
def test_modified_result_or_binding_is_rejected(field, value, code):
    raw = raw_report()
    receipt = reader.review_bytes(raw)
    receipt[field] = value
    with pytest.raises(reader.ConfirmationReportInvalid, match=code):
        reader.verify_receipt(raw, encode(receipt))


def test_unknown_receipt_fields_cannot_hide_approval_claims():
    raw = raw_report()
    receipt = reader.review_bytes(raw)
    receipt["unrecognized"] = {"approval": True}
    with pytest.raises(reader.ConfirmationReportInvalid, match="RECEIPT_RESULT_MISMATCH"):
        reader.verify_receipt(raw, encode(receipt))


def test_receipt_reformatting_is_allowed_but_raw_input_reformatting_is_not():
    raw = raw_report()
    receipt = reader.review_bytes(raw)
    reformatted = json.dumps(dict(reversed(list(receipt.items()))), indent=4).encode()
    assert reader.verify_receipt(raw, reformatted)["receipt_matches_input"]
    with pytest.raises(reader.ConfirmationReportInvalid, match="RECEIPT_INPUT_MISMATCH"):
        reader.verify_receipt(json.dumps(json.loads(raw), indent=2).encode(), reformatted)


def test_regenerated_self_consistent_pair_does_not_establish_authenticity():
    data = report()
    data["unknown"] = {"unverified_claim": "SYNTHETIC_PRIVATE_MARKER"}
    raw = json.dumps(data).encode()
    result = reader.verify_receipt(raw, encode(reader.review_bytes(raw)))
    assert result["receipt_matches_input"] and not result["input_authenticity_verified"]
    assert "SYNTHETIC_PRIVATE_MARKER" not in json.dumps(result)


def test_old_semantic_review_output_is_not_accepted_as_a_receipt():
    raw = raw_report()
    old = reader.review(reader.load(raw))
    with pytest.raises(reader.ConfirmationReportInvalid, match="RECEIPT_VERSION_INVALID"):
        reader.verify_receipt(raw, encode(old))


@pytest.mark.parametrize(
    "receipt,code",
    [
        (b"[]", "RECEIPT_OBJECT_REQUIRED"),
        (b'{"x":1,"x":2}', "RECEIPT_JSON_DUPLICATE_KEY"),
        (b'{"x":NaN}', "RECEIPT_JSON_NONFINITE"),
        (b"{", "RECEIPT_JSON_INVALID"),
    ],
)
def test_receipt_uses_strict_parser(receipt, code):
    with pytest.raises(reader.ConfirmationReportInvalid, match=code):
        reader.verify_receipt(raw_report(), receipt)


def test_receipt_size_is_bounded_separately_from_input():
    with pytest.raises(reader.ConfirmationReportInvalid, match="RECEIPT_FILE_TOO_LARGE"):
        reader.verify_receipt(raw_report(), b" " * (reader.MAX_RECEIPT_BYTES + 1))


@pytest.mark.parametrize("failed,status", [(False, 0), (True, 3)])
def test_cli_issue_and_verify_preserve_review_status(tmp_path, capsys, failed, status):
    p = tmp_path / "report.json"
    p.write_bytes(raw_report(failed=failed))
    assert reader.main([str(p)]) == status
    issued = json.loads(capsys.readouterr().out)
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(issued))
    assert reader.main([str(p), "--verify-receipt", str(receipt)]) == status
    result = json.loads(capsys.readouterr().out)
    assert result["receipt_matches_input"] and not result["execution_approved"]


def test_cli_mismatch_and_missing_receipt_errors_do_not_echo_paths_or_fields(tmp_path, capsys):
    raw = raw_report()
    p = tmp_path / "SYNTHETIC_PRIVATE_SOURCE.json"
    p.write_bytes(raw + b"\n")
    receipt = tmp_path / "SYNTHETIC_PRIVATE_RECEIPT.json"
    receipt.write_bytes(encode(reader.review_bytes(raw)))
    assert reader.main([str(p), "--verify-receipt", str(receipt)]) == 2
    mismatch = capsys.readouterr().out
    assert json.loads(mismatch)["code"] == "RECEIPT_INPUT_MISMATCH"
    receipt.unlink()
    assert reader.main([str(p), "--verify-receipt", str(receipt)]) == 2
    missing = capsys.readouterr().out
    assert "SYNTHETIC_PRIVATE" not in mismatch + missing


def test_cli_binds_the_single_read_buffer_even_if_file_is_replaced_during_read(
    tmp_path, capsys, monkeypatch
):
    original_raw = raw_report()
    changed = raw_report(failed=True)
    path = tmp_path / "report.json"
    path.write_bytes(original_raw)
    original_open = reader.Path.open
    reads = []

    class ChangingStream(io.BytesIO):
        def read(self, *args):
            raw = super().read(*args)
            with original_open(path, "wb") as stream:
                stream.write(changed)
            return raw

    def open_once(self, *args, **kwargs):
        if self == path and args == ("rb",):
            reads.append(True)
            return ChangingStream(original_raw)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(reader.Path, "open", open_once)
    assert reader.main([str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(reads) == 1 and result["input_sha256"] == hashlib.sha256(original_raw).hexdigest()
    with pytest.raises(reader.ConfirmationReportInvalid, match="RECEIPT_INPUT_MISMATCH"):
        reader.verify_receipt(changed, encode(result))
