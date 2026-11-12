"""Real isolated Python subprocess, binary stdin, cleanup and malformed archives."""

import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

LOADER = Path("scripts/demo_read_candidate_loader.py").read_text()


def archive(
    name="scripts/review_demo_read_deployment.py",
    body=b"import pathlib\nprint(__file__)\nraise SystemExit(0)\n",
    mode=0o100600,
):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        info = zipfile.ZipInfo(name)
        info.external_attr = mode << 16
        z.writestr(info, body)
    return out.getvalue()


def run(tmp_path, body, *, expected=None):
    return subprocess.run(
        [sys.executable, "-I", "-c", LOADER, expected or hashlib.sha256(body).hexdigest()],
        input=body,
        capture_output=True,
        env=dict(os.environ, TMPDIR=str(tmp_path)),
        timeout=15,
    )


def test_real_binary_stdin_execution_cleans_temporary_source(tmp_path):
    result = run(tmp_path, archive())
    assert result.returncode == 0 and result.stderr == b""
    extracted = Path(result.stdout.decode().strip())
    assert extracted.is_relative_to(tmp_path) and not extracted.exists()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "name,mode",
    [
        ("../escape.py", 0o100600),
        ("/escape.py", 0o100600),
        ("back\\slash.py", 0o100600),
        ("C:escape.py", 0o100600),
        ("link.py", 0o120600),
    ],
)
def test_unsafe_archive_paths_or_links_are_rejected_and_cleaned(tmp_path, name, mode):
    result = run(tmp_path, archive(name, mode=mode))
    assert result.returncode == 1 and result.stderr == b""
    assert json.loads(result.stdout)["stop_reason"] == "CANDIDATE_LOADER_REFUSED"
    assert not list(tmp_path.iterdir())


def test_archive_hash_failure_does_not_extract(tmp_path):
    result = run(tmp_path, archive(), expected="0" * 64)
    assert result.returncode == 1 and not list(tmp_path.iterdir())


def test_duplicate_archive_entries_are_refused(tmp_path):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("duplicate", "1")
        with pytest.warns(UserWarning):
            z.writestr("duplicate", "2")
    assert run(tmp_path, out.getvalue()).returncode == 1


def test_candidate_failure_status_is_preserved_and_temp_source_removed(tmp_path):
    result = run(tmp_path, archive(body=b"raise SystemExit(3)\n"))
    assert result.returncode == 3 and not list(tmp_path.iterdir())
