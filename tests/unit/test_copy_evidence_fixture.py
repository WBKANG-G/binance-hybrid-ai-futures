"""Test the cloud-only materialized pipeline package before the Docker lifecycle."""

import hashlib
import json
import re

from tests.fixtures.copy_evidence_runner import extend_package
from tests.unit.test_copy_clock_bundle import materialize


def test_extended_fixture_allowlist_hashes_and_worker_are_consistent(tmp_path):
    materialize(tmp_path)
    extend_package(tmp_path)
    manifest = json.loads((tmp_path / "TRANSFER_MANIFEST.json").read_text())
    launcher = (tmp_path / "RUN_COPY.ps1").read_text()
    required = re.search(r"\$required=@\(([^\n]+)\)", launcher)
    assert required is not None
    assert set(re.findall(r"'([^']+)'", required.group(1))) == set(manifest["files"])
    for path, pin in manifest["files"].items():
        assert hashlib.sha256((tmp_path / path).read_bytes()).hexdigest() == pin
    cfg = json.loads((tmp_path / "CONFIG.json").read_text())
    assert len(cfg["candidate_files"]) == 10
    for path, pin in cfg["candidate_files"].items():
        assert hashlib.sha256((tmp_path / "payload" / path).read_bytes()).hexdigest() == pin
    assert cfg["worker_report_keys"].count("synthetic_evidence_rehearsal") == 1
    compile((tmp_path / "copy_worker.py").read_text(), "<synthetic-pipeline-worker>", "exec")
    assert "candidate_files_verified -ne 10" in (tmp_path / "CLOCK_EXT.ps1").read_text()
