"""Import the setup candidate against the cached 89-file base, entirely in RAM."""

import base64
import hashlib
import json
import subprocess

import pytest

from tests.integration.test_cold_backup_docker_drill import HELPER_IMAGE
from tests.unit.test_cold_backup_executor import ROOT
from tests.unit.test_response_candidate_image_review import package


@pytest.mark.parametrize(
    "include_snapshot,include_copy", [(False, False), (True, False), (True, True)]
)
def test_bootstrap_import_and_explicit_guard_on_networkless_base_image(
    include_snapshot, include_copy
):
    files = package()
    config = json.loads(files["CONFIG.json"])
    config = {k: config[k] for k in ("baseline_files", "candidate_files")}
    path = "services/execution/demo_response_evidence_bootstrap.py"
    module = "services.execution.demo_response_evidence_bootstrap"
    bodies = {p: files["payload/" + p] for p in config["candidate_files"]}
    bodies[path] = (ROOT / path).read_bytes()
    config["candidate_files"][path] = hashlib.sha256(bodies[path]).hexdigest()
    extras = [(module, path)]
    if include_snapshot:
        path = "services/execution/demo_evidence_snapshot.py"
        bodies[path] = (ROOT / path).read_bytes()
        config["candidate_files"][path] = hashlib.sha256(bodies[path]).hexdigest()
        extras.append(("services.execution.demo_evidence_snapshot", path))
    if include_copy:
        path = "services/execution/demo_copy_transition_review.py"
        bodies[path] = (ROOT / path).read_bytes()
        config["candidate_files"][path] = hashlib.sha256(bodies[path]).hexdigest()
        extras.append(("services.execution.demo_copy_transition_review", path))
    worker = files["worker.py"].decode()
    marker = "\ndef collect("
    assert worker.count(marker) == 1
    worker = worker.replace(marker, "\nMODULES = MODULES + " + repr(tuple(extras)) + "\n" + marker)
    marker = "            checks = (\n"
    assert worker.count(marker) == 1
    worker = worker.replace(
        marker,
        marker
        + """                (
                    lambda: sys.modules["services.execution.demo_response_evidence_bootstrap"].prepare_response_evidence(None, None, None, None, None, None, apply=True),
                    "GET_SETUP_READERS_STOPPED_REQUIRED",
                ),
""",
    )
    if include_copy:
        worker = worker.replace(
            marker,
            marker
            + """                (
                    lambda: sys.modules["services.execution.demo_copy_transition_review"].review_copy_transition(None, None, None, None, None, None),
                    "COPY_TRANSITION_EXPLICIT_COPY_REVIEW_REQUIRED",
                ),
""",
        )
    sources = {p: base64.b64encode(body).decode() for p, body in bodies.items()}
    payload = "CONFIG=" + repr(config) + "\nSOURCES=" + repr(sources) + "\n" + worker
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "--pull=never",
            "--network",
            "none",
            "--read-only",
            "--restart",
            "no",
            "--memory",
            "512m",
            "--pids-limit",
            "32",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "10001:10001",
            "--entrypoint",
            "python",
            HELPER_IMAGE,
            "-I",
            "-B",
            "-",
        ],
        input=payload,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["baseline_files_verified"] == 89
    assert (
        report["candidate_files_verified"] == 3 + include_snapshot + include_copy
        and report["default_denials_verified"] == 6 + include_copy
    )
    assert (
        report["database_attempts"]
        == report["network_attempts"]
        == report["filesystem_write_attempts"]
        == 0
    )
    assert not report["candidate_installed"] and not report["execution_approved"]
