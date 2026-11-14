"""Pinned new-copy clock package; no original/previous copy writes."""

import hashlib
import json
import os
import subprocess
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_copy_clock_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT


def package():
    release = os.getenv("COPY_CLOCK_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git
    revision = real("rev-parse", "HEAD").decode().strip()

    def source(*args):
        return (
            (ROOT / args[1].split(":", 1)[1]).read_bytes()
            if args[0] == "show" and args[1].startswith(revision + ":")
            else real(*args)
        )

    with (
        patch.object(builder, "git", source),
        patch("scripts.build_windows_copy_transition_bundle.git", source),
        patch("scripts.build_windows_response_candidate_review_bundle.git", source),
    ):
        return builder.package_files("HEAD")


def materialize(root):
    root.mkdir(parents=True, exist_ok=True)
    for n, b in package().items():
        p = root / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b)


def test_all_payloads_and_latest_diagnostic_are_pinned():
    files = package()
    cfg = json.loads(files["CONFIG.json"])
    assert (
        cfg["prior_copy_receipt_sha256"] == builder.PRIOR_RECEIPT
        and cfg["prior_copy_source_reference"] == builder.PRIOR_SOURCE
    )
    assert len(cfg["candidate_files"]) == 5 and len(cfg["baseline_files"]) == 89
    for n, h in json.loads(files["TRANSFER_MANIFEST.json"])["files"].items():
        assert hashlib.sha256(files[n]).hexdigest() == h
    assert b"review-runtime,volume-nocopy" in files["CLOCK_EXT.ps1"]
    assert b"review-socket,readonly,volume-nocopy" in files["CLOCK_EXT.ps1"]
    assert b"Assert-ClockHelper" in files["CLOCK_EXT.ps1"]
    assert b"Write-Host $line" in files["CLOCK_EXT.ps1"]
    assert b"CLOCK_PRIOR_HASH_MISMATCH" in files["COPY_CORE.ps1"]
    assert b"COPY_CLOCK_RECEIPT.json" in files["COPY_CORE.ps1"]
    assert b"CLOCK_EXT.ps1" in files["RUN_COPY.ps1"]
    for bad in (b"'rm'", b"'update'", b"--signal=SIGKILL", b"/run/demo-keys"):
        assert bad not in files["CLOCK_EXT.ps1"]


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_transfer_tamper_stops_before_any_new_copy(tmp_path):
    materialize(tmp_path)
    pin = hashlib.sha256((tmp_path / "TRANSFER_MANIFEST.json").read_bytes()).hexdigest()
    (tmp_path / "CLOCK_EXT.ps1").write_bytes(b"PRIVATE_SENTINEL")
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
        text=True,
        capture_output=True,
        timeout=20,
        env=dict(
            os.environ,
            XDG_CACHE_HOME=str(tmp_path / "cache"),
            XDG_CONFIG_HOME=str(tmp_path / "config"),
            XDG_DATA_HOME=str(tmp_path / "data"),
        ),
    )
    assert result.returncode == 2 and not result.stderr and "PRIVATE_SENTINEL" not in result.stdout
    assert json.loads(result.stdout)["source_state_may_have_changed"] is False


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "fault,expected",
    [
        ("valid", None),
        ("hash", "CLOCK_PRIOR_HASH_MISMATCH"),
        ("source", "CLOCK_PRIOR_RECEIPT_INVALID"),
        ("v2", "CLOCK_PRIOR_DIAGNOSTIC_INVALID"),
        ("pending", "CLOCK_PRIOR_DIAGNOSTIC_INVALID"),
        ("clock_valid", "CLOCK_PRIOR_DIAGNOSTIC_INVALID"),
        ("running", "CLOCK_PRIOR_HELPER_NOT_STOPPED"),
        ("runtime_rw", "CLOCK_PRIOR_RESOURCES_INVALID"),
        ("network", "CLOCK_PRIOR_RESOURCES_INVALID"),
        ("exit", "CLOCK_PRIOR_RESOURCES_INVALID"),
        ("image", "CLOCK_PRIOR_RESOURCES_INVALID"),
    ],
)
def test_prior_copy_receipt_and_retained_resources_are_verified_before_rehearsal(
    tmp_path, fault, expected
):
    materialize(tmp_path)
    token = "a" * 32
    volumes = [
        "demo-copy-review-" + role + "-" + token for role in ("database", "runtime", "socket")
    ]
    ids = [c * 64 for c in "123"]
    images = ["sha256:" + "b" * 64, "sha256:" + "c" * 64]

    def mount(dest, name, rw, kind="volume"):
        return dict(Destination=dest, Type=kind, Name=name, RW=rw)

    specs = [
        [
            mount("/bundle", "", False, "bind"),
            mount("/clone/database", volumes[0], True),
            mount("/clone/runtime", volumes[1], True),
            mount("/review-socket", volumes[2], True),
        ],
        [mount("/var/lib/postgresql", volumes[0], True), mount("/review-socket", volumes[2], True)],
        [mount("/review-runtime", volumes[1], False), mount("/review-socket", volumes[2], False)],
    ]
    items = [
        dict(
            id=ids[i],
            image=images[1 if i == 1 else 0],
            labels={"com.docker.compose.project": "demo-copy-review"},
            mounts=specs[i],
            running=False,
            paused=False,
            dead=False,
            restarting=False,
            restart=dict(Name="no", MaximumRetryCount=0),
        )
        for i in range(3)
    ]
    value = dict(
        source_reference=builder.PRIOR_SOURCE,
        isolated_restore="COPY_RECOVERED_AND_ACCOUNT_READS_VERIFIED",
        backup_manifest_sha256="d" * 64,
        backup_receipt_sha256="e" * 64,
        clone_network="none",
        retained_helper_ids=ids,
        retained_clone_volumes=volumes,
        clone_container_id=ids[1],
        copy_transition_diagnostic=dict(
            copy_transition_review="ASSESSMENT_COMPLETE_KEEP_STOPPED",
            snapshot_sha256="f" * 64,
            v2_bound=False,
            clock_matches=False,
            pending_read_attempts=0,
            pending_evidence_requests=0,
            historical_unresolved_requests=4,
            stored_day_kst="2026-11-13",
            stored_week_kst="2026-11-09",
        ),
    )
    for flag in (
        "copy_mounts_and_network_verified",
        "input_hashes_reverified",
        "clone_stopped",
        "clone_clean_exit_verified",
        "archive_bytes_and_metadata_verified_before_clone_recovery",
    ):
        value[flag] = True
    for flag in (
        "original_services_started",
        "original_volume_mounted_in_restore_helpers",
        "financial_write_attempted",
        "installation_ready",
        "automatic_retry",
        "execution_approved",
        "orders_enabled",
        "live_trading_enabled",
    ):
        value[flag] = False
    if fault == "source":
        value["source_reference"] = "0" * 40
    if fault == "v2":
        value["copy_transition_diagnostic"]["v2_bound"] = True
    if fault == "pending":
        value["copy_transition_diagnostic"]["pending_read_attempts"] = 1
    if fault == "clock_valid":
        value["copy_transition_diagnostic"]["clock_matches"] = True
    if fault == "running":
        items[2]["running"] = True
    if fault == "runtime_rw":
        items[2]["mounts"][0]["RW"] = True
    if fault == "image":
        items[1]["image"] = images[0]
    receipt = tmp_path / "prior.json"
    receipt.write_text(json.dumps(value))
    (tmp_path / "items.json").write_text(json.dumps(items))
    cfg = dict(
        prior_copy_source_reference=builder.PRIOR_SOURCE,
        prior_copy_receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(),
        manifest_sha256="d" * 64,
        receipt_sha256="e" * 64,
    )
    if fault == "hash":
        cfg["prior_copy_receipt_sha256"] = "0" * 64
    (tmp_path / "cfg.json").write_text(json.dumps(cfg))
    network = "bridge" if fault == "network" else "none"
    exit_status = "1 false" if fault == "exit" else "0 false"
    script = tmp_path / "prior-test.ps1"
    script.write_text(f"""
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
. '{tmp_path / "CLOCK_EXT.ps1"}'
function Read-CopySources {{return (Get-Content -Raw '{tmp_path / "items.json"}' | ConvertFrom-Json)}}
function Find-PriorCopyReceipt {{return '{receipt}'}}
function Restore-Hash([string]$Path) {{return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}}
function Read-RestoreJson([string]$Path) {{return (Get-Content -Raw $Path | ConvertFrom-Json)}}
function Project([object]$Value) {{return $Value.labels.'com.docker.compose.project'}}
function Resolve-RestoreImageMatch([string]$A,[string]$B) {{return $A -ceq $B}}
function Backup-Docker([string[]]$Arguments) {{if (($Arguments -join ' ') -like '*NetworkMode*') {{return '{network}'}}; return '{exit_status}'}}
$cfg=Read-RestoreJson '{tmp_path / "cfg.json"}'
$state=[pscustomobject]@{{canonical_images=@('{images[0]}','{images[1]}');database_volume='original-db';runtime_volume='original-runtime'}}
try {{ $null=Read-ClockSources $cfg ([pscustomobject]@{{output_volume='old-output'}}) $state; Write-Output 'OK' }}
catch {{Write-Output $_.Exception.Message;exit 2}}
finally {{if ($null -ne $script:ClockPriorHandle) {{$script:ClockPriorHandle.Dispose()}}}}
""")
    result = subprocess.run(
        [str(PWSH), "-NoProfile", "-NonInteractive", "-File", str(script)],
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
    assert result.returncode == (2 if expected else 0) and not result.stderr, (
        result.stdout + result.stderr
    )
    assert result.stdout.strip() == (expected or "OK")
