"""Installer ZIP pins candidate, six-file payload, Docker context and PowerShell."""

import hashlib
import io
import json
import zipfile
from pathlib import Path

from scripts import build_demo_read_install_bundle as builder


def test_build_pins_six_new_files_and_never_copies_existing_sources(tmp_path, monkeypatch):
    loader = Path("scripts/demo_read_candidate_loader.py").read_bytes()

    def git(*args):
        if args[0] == "rev-parse":
            return b"a" * 40
        path = args[1].split(":", 1)[1]
        if path.endswith("demo_read_candidate_loader.py"):
            return loader
        if path.endswith("INSTALLED_SOURCE_20261111.json"):
            return json.dumps({"existing.py": "b" * 64}).encode()
        return b"# fixture\n"

    monkeypatch.setattr(builder, "git", git)
    files = {n: ("# " + n).encode() for n in builder.INSTALL_FILES}
    monkeypatch.setattr(builder, "source", lambda ref: files.copy())
    first = builder.build("ref", tmp_path / "one")
    second = builder.build("ref", tmp_path / "two")
    assert first.read_bytes() == second.read_bytes()
    prefix = "windows-demo-read-code-install-20261112/"
    with zipfile.ZipFile(first) as z:
        manifest = json.loads(z.read(prefix + "TRANSFER_MANIFEST.json"))
        assert ".dockerignore" in manifest["files"]
        for name, digest in manifest["files"].items():
            assert hashlib.sha256(z.read(prefix + name)).hexdigest() == digest
        assert len([n for n in z.namelist() if n.startswith(prefix + "payload/")]) == 6
        assert b"--chown=10001:10001" in z.read(prefix + "Dockerfile")
        command = z.read(prefix + "RUN_GUARD.cmd")
        assert b"%2 %3" in command and max(map(len, command.splitlines())) < 8000
        with zipfile.ZipFile(io.BytesIO(z.read(prefix + "candidate.zip"))) as inner:
            data = json.loads(inner.read("CANDIDATE_MANIFEST.json"))
            assert set(data["install_files"]) == set(builder.INSTALL_FILES)
            assert data["operation"] == "review"
