"""Explicit new-file publication must never truncate inputs or existing outputs."""

import json
import os
import stat

import pytest

from scripts import review_runtime_confirmation_report as reader
from tests.unit.test_runtime_review_receipt import raw_report


def paths(tmp_path):
    source = tmp_path / "source.json"
    raw = raw_report()
    source.write_bytes(raw)
    return source, tmp_path / "receipt.json", raw


def temporary_files(tmp_path):
    return list(tmp_path.glob(".runtime-review-*.tmp"))


@pytest.mark.parametrize("failed,status", [(False, 0), (True, 3)])
def test_explicit_save_matches_stdout_and_verifies_without_changing_input(
    tmp_path, capsys, failed, status
):
    source, destination, _ = paths(tmp_path)
    raw = raw_report(failed=failed)
    source.write_bytes(raw)
    assert reader.main([str(source), "--save-receipt", str(destination)]) == status
    stdout = capsys.readouterr().out
    assert destination.read_bytes() == stdout.encode() and source.read_bytes() == raw
    assert reader.verify_receipt(raw, destination.read_bytes())["receipt_matches_input"]
    assert not temporary_files(tmp_path)
    if os.name == "posix":
        assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_default_mode_still_writes_no_receipt_file(tmp_path, capsys):
    source, destination, raw = paths(tmp_path)
    assert reader.main([str(source)]) == 0
    assert not destination.exists() and source.read_bytes() == raw
    assert not temporary_files(tmp_path)


@pytest.mark.parametrize(
    "target", ["same", "normalized", "existing", "hardlink", "symlink", "dangling", "directory"]
)
def test_input_and_existing_directory_entries_are_never_replaced(tmp_path, capsys, target):
    source, destination, raw = paths(tmp_path)
    expected = None
    if target == "same":
        destination = source
    elif target == "normalized":
        (tmp_path / "sub").mkdir()
        destination = tmp_path / "sub" / ".." / "source.json"
    elif target == "existing":
        expected = b"SYNTHETIC_EXISTING_DATA"
        destination.write_bytes(expected)
    elif target == "hardlink":
        os.link(source, destination)
    elif target == "symlink":
        destination.symlink_to(source)
    elif target == "dangling":
        destination.symlink_to(tmp_path / "missing-target")
    else:
        destination.mkdir()
    assert reader.main([str(source), "--save-receipt", str(destination)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["review"] == "RECEIPT_SAVE_FAILED" and not result["execution_approved"]
    assert source.read_bytes() == raw and not temporary_files(tmp_path)
    if target == "existing":
        assert destination.read_bytes() == expected
    elif target in {"symlink", "dangling"}:
        assert destination.is_symlink()
        if target == "dangling":
            assert not (tmp_path / "missing-target").exists()
    elif target == "directory":
        assert destination.is_dir()


def test_destination_created_at_publication_wins_without_being_overwritten(
    tmp_path, capsys, monkeypatch
):
    source, destination, raw = paths(tmp_path)
    original = reader.os.link

    def compete(temporary, output):
        destination.write_bytes(b"SYNTHETIC_RACE_WINNER")
        return original(temporary, output)

    monkeypatch.setattr(reader.os, "link", compete)
    assert reader.main([str(source), "--save-receipt", str(destination)]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "OUTPUT_ALREADY_EXISTS"
    assert destination.read_bytes() == b"SYNTHETIC_RACE_WINNER" and source.read_bytes() == raw
    assert not temporary_files(tmp_path)


@pytest.mark.parametrize("stage", ["create", "write", "sync", "publish"])
def test_failures_before_publication_leave_no_final_output(tmp_path, capsys, monkeypatch, stage):
    source, destination, raw = paths(tmp_path)

    def fail(*args, **kwargs):
        raise OSError("SYNTHETIC_PRIVATE_FAILURE_DETAIL")

    if stage == "create":
        monkeypatch.setattr(reader.tempfile, "mkstemp", fail)
    elif stage == "sync":
        monkeypatch.setattr(reader.os, "fsync", fail)
    elif stage == "publish":
        monkeypatch.setattr(reader.os, "link", fail)
    else:
        original = reader.os.fdopen

        class PartialWrite:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def write(self, data):
                self.stream.write(data[: len(data) // 2])
                raise OSError("SYNTHETIC_PRIVATE_FAILURE_DETAIL")

        monkeypatch.setattr(reader.os, "fdopen", lambda *args: PartialWrite(original(*args)))
    assert reader.main([str(source), "--save-receipt", str(destination)]) == 2
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["code"] == "OUTPUT_SAVE_FAILED" and not result["storage_publication_confirmed"]
    assert not destination.exists() and not temporary_files(tmp_path)
    assert source.read_bytes() == raw and "SYNTHETIC_PRIVATE" not in output


def test_uncertain_publication_never_removes_a_complete_final_file(tmp_path, capsys, monkeypatch):
    source, destination, raw = paths(tmp_path)
    original = reader.os.link

    def link_then_fail(*args):
        original(*args)
        raise OSError("synthetic acknowledgement failure")

    monkeypatch.setattr(reader.os, "link", link_then_fail)
    assert reader.main([str(source), "--save-receipt", str(destination)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["storage_publication_attempted"] and not result["storage_publication_confirmed"]
    assert reader.verify_receipt(raw, destination.read_bytes())["receipt_matches_input"]
    assert source.read_bytes() == raw and not temporary_files(tmp_path)


def test_cleanup_failure_preserves_published_output_and_reports_uncertainty(
    tmp_path, capsys, monkeypatch
):
    source, destination, raw = paths(tmp_path)
    original = reader.os.unlink

    def fail_cleanup(path, *args, **kwargs):
        if str(path).endswith(".tmp"):
            raise OSError("synthetic cleanup failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(reader.os, "unlink", fail_cleanup)
    assert reader.main([str(source), "--save-receipt", str(destination)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["code"] == "OUTPUT_TEMP_CLEANUP_FAILED"
    assert result["storage_publication_confirmed"] and result["temporary_cleanup_incomplete"]
    assert reader.verify_receipt(raw, destination.read_bytes())["receipt_matches_input"]
    assert source.read_bytes() == raw and len(temporary_files(tmp_path)) == 1


def test_missing_parent_is_not_created_implicitly(tmp_path, capsys):
    source, _, raw = paths(tmp_path)
    destination = tmp_path / "missing" / "receipt.json"
    assert reader.main([str(source), "--save-receipt", str(destination)]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "OUTPUT_SAVE_FAILED"
    assert not destination.parent.exists() and source.read_bytes() == raw


def test_invalid_input_creates_no_temporary_or_final_output(tmp_path, capsys):
    source, destination, _ = paths(tmp_path)
    source.write_bytes(b"{")
    assert reader.main([str(source), "--save-receipt", str(destination)]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "JSON_INVALID"
    assert (
        source.read_bytes() == b"{" and not destination.exists() and not temporary_files(tmp_path)
    )


def test_save_and_verify_modes_are_mutually_exclusive(tmp_path, capsys):
    source, destination, raw = paths(tmp_path)
    assert (
        reader.main(
            [str(source), "--save-receipt", str(destination), "--verify-receipt", str(destination)]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["code"] == "ARGUMENTS_INVALID"
    assert source.read_bytes() == raw and not destination.exists()
