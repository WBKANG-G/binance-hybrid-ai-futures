import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import recover_keep_paused as loader


def test_embedded_bytes_equal_reviewed_sources_and_original_user_projection():
    files = loader.decode()
    assert set(files) == {
        "acknowledge_flat_recovery.py",
        "review_unresolved_details.py",
        "reference.json",
    }
    for n in files:
        source = "original-reference-fields.json" if n == "reference.json" else n
        assert files[n] == Path(__file__).with_name(source).read_bytes()
        assert hashlib.sha256(files[n]).hexdigest() == loader.EXPECTED[n]
    baseline = json.loads(files["reference.json"])
    assert baseline["audited_request_count"] == 78 and baseline["unresolved_count"] == 4


@pytest.mark.parametrize("code", [0, 2])
def test_loader_uses_private_files_invokes_once_and_never_sets_timeout(tmp_path, code):
    directory = tmp_path / "private"
    calls = []

    def invoke(args, **kwargs):
        calls.append(args)
        assert args == [sys.executable, "-", "--acknowledge-four-historical-unresolved"]
        assert set(kwargs) == {"stdin", "stderr", "check"}
        assert kwargs["stderr"] == subprocess.DEVNULL and kwargs["check"] is False
        assert kwargs["stdin"].read() == loader.decode()["acknowledge_flat_recovery.py"]
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in directory.iterdir())
        return subprocess.CompletedProcess(args, code)

    assert loader.run(directory=directory, invoke=invoke) == code
    assert len(calls) == 1 and not directory.exists()


def test_payload_tampering_blocks_before_any_directory_or_invocation(tmp_path):
    directory = tmp_path / "private"
    assert (
        loader.run(
            directory=directory,
            payload={"reference.json": "tampered"},
            invoke=lambda *_: pytest.fail(),
        )
        == 2
    )
    assert not directory.exists()


def test_existing_directory_preserved_and_not_executed(tmp_path):
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("preserve")
    assert loader.run(directory=tmp_path, invoke=lambda *_: pytest.fail()) == 2
    assert sentinel.read_text() == "preserve"


def test_matching_names_with_changed_source_still_block_before_execution(tmp_path):
    payload = {**loader.PAYLOAD, "reference.json": "tampered"}
    directory = tmp_path / "private"
    assert loader.run(directory=directory, payload=payload, invoke=lambda *_: pytest.fail()) == 2
    assert not directory.exists()


def test_subprocess_failure_redacts_details_and_reports_uncertainty(tmp_path, capsys):
    def fail(*args, **kwargs):
        raise RuntimeError("PRIVATE_EXCEPTION_EXAMPLE")

    directory = tmp_path / "private"
    assert loader.run(directory=directory, invoke=fail) == 2
    out = capsys.readouterr().out
    assert "PRIVATE_EXCEPTION_EXAMPLE" not in out
    assert json.loads(out.splitlines()[-1])["application_may_have_completed"] is True
    assert not directory.exists()


def test_explicit_acknowledgement_flag_required(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["loader"])
    monkeypatch.setattr(loader, "run", lambda: pytest.fail("no invocation without acknowledgement"))
    with pytest.raises(SystemExit) as error:
        loader.main()
    assert error.value.code == 2


def test_cleanup_failure_is_redacted_and_umask_restored(tmp_path, monkeypatch, capsys):
    def fail(*_):
        raise OSError("PRIVATE_PATH_EXAMPLE")

    monkeypatch.setattr(loader.shutil, "rmtree", fail)
    original_umask = os.umask(0o022)
    try:
        assert (
            loader.run(
                directory=tmp_path / "private",
                invoke=lambda *a, **k: subprocess.CompletedProcess(a, 0),
            )
            == 2
        )
        observed = os.umask(0o022)
        assert observed == 0o022
    finally:
        os.umask(original_umask)
    out = capsys.readouterr().out
    assert "PRIVATE_PATH_EXAMPLE" not in out
    assert (
        json.loads(out.splitlines()[-1])["recovery_loader"]
        == "TEMPORARY_FILE_CLEANUP_FAILED_NO_AUTOMATIC_RETRY"
    )
