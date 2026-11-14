"""Real networkless/read-only image imports; synthetic failure payloads only."""

import base64
import hashlib
import json
import os
import subprocess
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_response_candidate_review_bundle as builder
from tests.integration.test_cold_backup_docker_drill import HELPER_IMAGE
from tests.unit.test_cold_backup_executor import PWSH, ROOT


def package():
    release = os.environ.get("CANDIDATE_IMAGE_REVIEW_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git

    def source(*args):
        if args[0] == "show" and args[1].startswith("WORKTREE:"):
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        if args[0] == "rev-parse":
            return b"WORKTREE\n"
        return real(*args)

    with patch.object(builder, "git", source):
        return builder.package_files("HEAD")


def materialize(root):
    root.mkdir(parents=True, exist_ok=True)
    for n, b in package().items():
        p = root / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b)


def test_only_two_candidate_files_are_packaged_and_default_is_not_install():
    files = package()
    assert len(files) == 13
    for n, h in json.loads(files["TRANSFER_MANIFEST.json"])["files"].items():
        assert hashlib.sha256(files[n]).hexdigest() == h
    payloads = [n for n in files if n.startswith("payload/")]
    assert sorted(payloads) == sorted("payload/" + n for n in builder.CANDIDATE)
    assert "payload/packages/persistence/demo.py" not in files
    core = files["REVIEW_CORE.ps1"].decode()
    assert "'--network','none'" in core and "'--read-only'" in core
    for bad in ("'kill'", "'rm'", "@('exec'", "'update'", "'--mount'"):
        assert bad not in core
    assert "'--label','com.docker.compose.project=demo-review-only'" in core
    assert "installation_ready=$false" in core


@pytest.mark.parametrize(
    "case,reason",
    [
        ("valid", None),
        ("baseline", "CANDIDATE_BASE_IMAGE_BASELINE_MISMATCH"),
        ("payload", "CANDIDATE_PAYLOAD_HASH_MISMATCH"),
        ("network", "CANDIDATE_REVIEW_UNAVAILABLE"),
        ("database", "CANDIDATE_REVIEW_UNAVAILABLE"),
        ("write", "CANDIDATE_REVIEW_UNAVAILABLE"),
    ],
)
def test_candidate_worker_on_real_image(case, reason):
    if subprocess.run(["docker", "image", "inspect", HELPER_IMAGE], capture_output=True).returncode:
        pytest.skip("Cached image unavailable")
    files = package()
    original = json.loads(files["CONFIG.json"])
    config = {n: original[n] for n in ("baseline_files", "candidate_files")}
    bodies = {n: files["payload/" + n] for n in builder.CANDIDATE}
    first = builder.CANDIDATE[0]
    if case == "baseline":
        config["baseline_files"][next(iter(config["baseline_files"]))] = "0" * 64
    if case == "payload":
        bodies[first] += b"\n# changed\n"
    if case in {"network", "database", "write"}:
        suffix = {
            "network": b"\nimport socket; socket.create_connection(('invalid.example',443))\n",
            "database": b"\nimport sqlalchemy; sqlalchemy.create_engine('postgresql://PRIVATE_SENTINEL')\n",
            "write": b"\nopen('/tmp/PRIVATE_SENTINEL','w')\n",
        }[case]
        bodies[first] += suffix
        config["candidate_files"][first] = hashlib.sha256(bodies[first]).hexdigest()
    sources = {n: base64.b64encode(b).decode() for n, b in bodies.items()}
    worker = base64.b64encode(files["worker.py"]).decode()
    payload = (
        "import base64,json\nCONFIG=json.loads("
        + repr(json.dumps(config))
        + ")\nSOURCES=json.loads("
        + repr(json.dumps(sources))
        + ")\nexec(compile(base64.b64decode("
        + repr(worker)
        + '),"<candidate-review>","exec"))\n'
    )
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
    assert not result.stderr and result.returncode == (2 if reason else 0), (
        result.stdout + result.stderr
    )
    assert "PRIVATE_SENTINEL" not in result.stdout
    report = json.loads(result.stdout)
    assert report["candidate_installed"] is False and report["orders_enabled"] is False
    if reason:
        assert report["failure_category"] == reason
    else:
        assert report["baseline_files_verified"] == 89 and report["default_denials_verified"] == 5
    if case in {"network", "database", "write"}:
        assert (
            report[
                {
                    "network": "network_attempts",
                    "database": "database_attempts",
                    "write": "filesystem_write_attempts",
                }[case]
            ]
            == 1
        )


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_transfer_tamper_stops_before_docker(tmp_path):
    materialize(tmp_path)
    pin = hashlib.sha256((tmp_path / "TRANSFER_MANIFEST.json").read_bytes()).hexdigest()
    (tmp_path / "worker.py").write_bytes(b"PRIVATE_SENTINEL")
    result = subprocess.run(
        [
            str(PWSH),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(tmp_path / "REVIEW.ps1"),
            "-ManifestSHA256",
            pin,
        ],
        capture_output=True,
        text=True,
        timeout=15,
        env=dict(
            os.environ,
            XDG_CACHE_HOME=str(tmp_path / "cache"),
            XDG_CONFIG_HOME=str(tmp_path / "config"),
            XDG_DATA_HOME=str(tmp_path / "data"),
        ),
    )
    assert result.returncode == 2 and not result.stderr and "PRIVATE_SENTINEL" not in result.stdout
    assert json.loads(result.stdout)["source_state_may_have_changed"] is False
