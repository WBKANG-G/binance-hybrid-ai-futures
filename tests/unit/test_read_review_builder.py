"""Reproducible pinned archives and Windows command-length checks."""

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from scripts import build_demo_read_review_bundle as builder


def mock_git(monkeypatch, loader):
    def git(*args):
        if args[0] == "rev-parse":
            return b"a" * 40
        path = args[1].split(":", 1)[1]
        if path.endswith("demo_read_candidate_loader.py"):
            return loader
        if path.endswith("INSTALLED_SOURCE_20261111.json"):
            return json.dumps({"example.py": "b" * 64}).encode()
        return b"# fixture\n"

    monkeypatch.setattr(builder, "git", git)
    monkeypatch.setattr(builder, "source", lambda ref: {"scripts/example.py": b"# fixture\n"})


def test_bundle_is_reproducible_with_pinned_outer_and_inner_manifests(tmp_path, monkeypatch):
    mock_git(monkeypatch, Path("scripts/demo_read_candidate_loader.py").read_bytes())
    one, two = builder.build("ref", tmp_path / "one"), builder.build("ref", tmp_path / "two")
    assert one.read_bytes() == two.read_bytes()
    with zipfile.ZipFile(one) as outer:
        prefix = "windows-demo-read-deployment-review-20261112/"
        body = outer.read(prefix + "TRANSFER_MANIFEST.json")
        manifest = json.loads(body)
        for name, digest in manifest["files"].items():
            assert hashlib.sha256(outer.read(prefix + name)).hexdigest() == digest
        assert hashlib.sha256(body).hexdigest().encode() in outer.read(prefix + "RUN_REVIEW.cmd")
        command = outer.read(prefix + "RUN_CANDIDATE.cmd")
        assert b"docker exec -i %1 python -I" in command
        assert max(map(len, command.splitlines())) <= 8000
        payload = outer.read(prefix + "candidate.zip")
        assert hashlib.sha256(payload).hexdigest().encode() in command
        with zipfile.ZipFile(io.BytesIO(payload)) as inner:
            record = json.loads(inner.read("CANDIDATE_MANIFEST.json"))
            assert record["candidate_reference"] == "a" * 40
            assert record["installed_files"] == {"example.py": "b" * 64}


def test_oversized_loader_is_refused_before_creating_bundle(tmp_path, monkeypatch):
    mock_git(monkeypatch, b"x" * 10_000)
    with pytest.raises(ValueError, match="Windows command limit"):
        builder.build("ref", tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_recovery_bundle_has_explicit_entrypoint_and_wait_flag(tmp_path, monkeypatch):
    mock_git(monkeypatch, Path("scripts/demo_read_candidate_loader.py").read_bytes())
    result = builder.build("ref", tmp_path, recover=True)
    with zipfile.ZipFile(result) as z:
        prefix = "windows-demo-unused-clock-review-20261112/"
        assert b"-WaitStartupHold" in z.read(prefix + "RUN_RECOVER.cmd")
        assert prefix + "RUN_REVIEW.cmd" not in z.namelist()
        with zipfile.ZipFile(io.BytesIO(z.read(prefix + "candidate.zip"))) as inner:
            assert (
                json.loads(inner.read("CANDIDATE_MANIFEST.json"))["operation"]
                == "recover-unused-clock"
            )
