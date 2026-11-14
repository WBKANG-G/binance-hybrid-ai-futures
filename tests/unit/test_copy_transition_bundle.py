"""Sealed copy-only runner; no automatic clock/day changes or source starts."""

import hashlib
import json
import os
import subprocess
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_copy_transition_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT


def package():
    release = os.getenv("COPY_TRANSITION_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git

    revision = real("rev-parse", "HEAD").decode().strip()

    def source(*args):
        if args[0] == "show" and args[1].startswith(revision + ":"):
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    # Nested image-package builder uses its own git binding, so patch both.
    with (
        patch.object(builder, "git", source),
        patch("scripts.build_windows_response_candidate_review_bundle.git", source),
    ):
        return builder.package_files("HEAD")


def materialize(root):
    root.mkdir(parents=True, exist_ok=True)
    for p, b in package().items():
        f = root / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b)


def test_sealed_bundle_pins_both_receipts_and_only_three_memory_candidates():
    files = package()
    config = json.loads(files["CONFIG.json"])
    assert len(config["baseline_files"]) == 89 and len(config["candidate_files"]) == 3
    assert (
        config["restore_receipt_sha256"]
        == "26395438e08500da24252eac7b11dfe76b22e0d660c86bb1c0d236b6b9e1da08"
    )
    for p, h in json.loads(files["TRANSFER_MANIFEST.json"])["files"].items():
        assert hashlib.sha256(files[p]).hexdigest() == h
    core = files["COPY_CORE.ps1"].decode()
    ext = files["COPY_EXT.ps1"].decode()
    for forbidden in [
        "'rm'",
        "'update'",
        "--signal=SIGKILL",
        "recover_completed_read_clock(",
        "prepare_response_evidence(",
    ]:
        assert forbidden not in core + ext + files["copy_worker.py"].decode()
    assert "'--network','none'" in core + ext
    assert "'--user','10001:10001'" in ext
    assert "target=/review-runtime,readonly,volume-nocopy" in ext
    assert "target=/review-socket,readonly,volume-nocopy" in ext
    assert "source='+$state.runtime_volume" not in core + ext
    assert "'--mount'" not in files["SOURCE_SCOPE.ps1"].decode()
    assert "SetAccessControl($parent" not in files["COMMON.ps1"].decode()
    assert "D:\\" not in core + ext


def test_restore_template_drift_is_refused():
    with pytest.raises(ValueError, match="TEMPLATE_CHANGED"):
        builder.derive_core("PRIVATE_SENTINEL")


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_transfer_tamper_blocks_before_docker(tmp_path):
    materialize(tmp_path)
    pin = hashlib.sha256((tmp_path / "TRANSFER_MANIFEST.json").read_bytes()).hexdigest()
    (tmp_path / "copy_worker.py").write_bytes(b"PRIVATE_SENTINEL")
    result = subprocess.run(
        [
            str(PWSH),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(tmp_path / "RUN_COPY.ps1"),
            "-ManifestSHA256",
            pin,
            "-ExecuteReview",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        env=dict(
            os.environ,
            XDG_CACHE_HOME=str(tmp_path / "cache"),
            XDG_CONFIG_HOME=str(tmp_path / "config"),
            XDG_DATA_HOME=str(tmp_path / "data"),
        ),
    )
    assert result.returncode == 2 and not result.stderr
    value = json.loads(result.stdout)
    assert (
        value["source_state_may_have_changed"] is False and "PRIVATE_SENTINEL" not in result.stdout
    )


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "case,expected",
    [
        ("valid", None),
        ("original_id", "RESTORE_CLONE_ID_INVALID"),
        ("source_volume", "RESTORE_CLONE_MOUNT_INVALID"),
        ("extra_mount", "RESTORE_CLONE_MOUNT_INVALID"),
        ("old_project", "RESTORE_CLONE_MOUNT_INVALID"),
        ("network", "RESTORE_CLONE_NETWORK_INVALID"),
        ("socket_rw", "RESTORE_CLONE_MOUNT_INVALID"),
    ],
)
def test_only_new_clone_and_socket_mounts_can_be_stopped(tmp_path, case, expected):
    materialize(tmp_path)
    clone = dict(
        id="b" * 64,
        restart=dict(Name="no"),
        labels={"com.docker.compose.project": "demo-copy-review"},
        mounts=[
            dict(Type="volume", Name="new-database", Destination="/var/lib/postgresql", RW=True),
            dict(Type="volume", Name="new-socket", Destination="/review-socket", RW=True),
        ],
    )
    network = "bridge" if case == "network" else "none"
    if case == "source_volume":
        clone["mounts"][0]["Name"] = "original-database"
    if case == "extra_mount":
        clone["mounts"].append(
            dict(Type="volume", Name="key-session", Destination="/run/demo-keys", RW=False)
        )
    if case == "old_project":
        clone["labels"]["com.docker.compose.project"] = "hybrid-windows"
    if case == "socket_rw":
        clone["mounts"][1]["RW"] = False
    ident = "a" * 64 if case == "original_id" else "b" * 64
    (tmp_path / "clone.json").write_text(json.dumps(clone))
    script = tmp_path / "mount-test.ps1"
    script.write_text(f"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. '{tmp_path / "COPY_EXT.ps1"}'
$script:CopySocket='new-socket'
function Read-Container {{return (Get-Content -Raw '{tmp_path / "clone.json"}' | ConvertFrom-Json)}}
function Project([object]$Value) {{return $Value.labels.'com.docker.compose.project'}}
function Backup-Docker {{return '{network}'}}
try {{ Assert-NewClone '{ident}' 'new-database' @([pscustomobject]@{{id=('a'*64)}}); Write-Output 'OK' }}
catch {{Write-Output $_.Exception.Message;exit 2}}
""")
    result = subprocess.run(
        [str(PWSH), "-NoProfile", "-NonInteractive", "-File", str(script)],
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
    assert result.returncode == (2 if expected else 0) and not result.stderr
    assert result.stdout.strip() == (expected or "OK")


@pytest.mark.parametrize(
    "case,expected",
    [
        ("baseline", "COPY_BASELINE_MISMATCH"),
        ("payload", "COPY_CANDIDATE_MISMATCH"),
        ("config", "COPY_CONFIG_INVALID"),
        ("structured", "SHARED_CHECKPOINT_MISMATCH"),
    ],
)
def test_worker_refuses_unverified_input_on_real_networkless_image(case, expected):
    import base64

    from tests.integration.test_cold_backup_docker_drill import HELPER_IMAGE

    files = package()
    cfg = json.loads(files["CONFIG.json"])
    cfg = {k: cfg[k] for k in ("baseline_files", "candidate_files")}
    sources = {p: base64.b64encode(files["payload/" + p]).decode() for p in builder.CANDIDATE}
    if case == "baseline":
        cfg["baseline_files"][next(iter(cfg["baseline_files"]))] = "0" * 64
    if case == "payload":
        sources[builder.CANDIDATE[0]] = base64.b64encode(b"PRIVATE_SENTINEL").decode()
    if case == "config":
        cfg["candidate_files"] = {}
    if case == "structured":
        p = builder.CANDIDATE[0]
        body = (
            files["payload/" + p]
            + b"\nfrom packages.persistence.demo_shared_weight import SharedWeightStopped\nraise SharedWeightStopped('SHARED_CHECKPOINT_MISMATCH')\n"
        )
        sources[p] = base64.b64encode(body).decode()
        cfg["candidate_files"][p] = hashlib.sha256(body).hexdigest()
    payload = (
        "CONFIG="
        + repr(cfg)
        + "\nSOURCES="
        + repr(sources)
        + "\n"
        + files["copy_worker.py"].decode()
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
            "--user",
            "10001:10001",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--entrypoint",
            "python",
            HELPER_IMAGE,
            "-I",
            "-B",
            "-",
        ],
        input=payload,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 2 and not result.stderr
    report = json.loads(result.stdout)
    assert report["failure_category"] == expected and "PRIVATE_SENTINEL" not in result.stdout
    assert not report["execution_approved"] and not report["automatic_retry"]
