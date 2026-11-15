"""Sealed Windows synthetic runner: explicit pin and prior-clock stopped scope."""

import hashlib
import json
import os
import re
import subprocess
import zipfile
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from scripts import build_windows_copy_evidence_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT


def package():
    release = os.getenv("COPY_EVIDENCE_RELEASE_ZIP")
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

    with ExitStack() as stack:
        for name in (
            "build_windows_copy_evidence_bundle",
            "build_windows_copy_clock_bundle",
            "build_windows_copy_transition_bundle",
            "build_windows_response_candidate_review_bundle",
        ):
            stack.enter_context(patch("scripts." + name + ".git", source))
        return builder.package_files("HEAD", "a" * 64)  # Synthetic test input only.


def materialize(root):
    root.mkdir(parents=True, exist_ok=True)
    for name, body in package().items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)


def test_transfer_allowlist_payloads_and_progress_parser_match():
    files = package()
    cfg = json.loads(files["CONFIG.json"])
    manifest = json.loads(files["TRANSFER_MANIFEST.json"])
    assert len(cfg["candidate_files"]) == 10 and len(cfg["baseline_files"]) == 89
    required = re.search(r"\$required=@\(([^\n]+)\)", files["RUN_COPY.ps1"].decode())
    assert set(re.findall(r"'([^']+)'", required.group(1))) == set(manifest["files"])
    for path, pin in manifest["files"].items():
        assert hashlib.sha256(files[path]).hexdigest() == pin
    for path, pin in cfg["candidate_files"].items():
        assert hashlib.sha256(files["payload/" + path]).hexdigest() == pin
    assert cfg["prior_clock_source_reference"] == builder.PRIOR_CLOCK_SOURCE
    assert re.fullmatch("[0-9a-f]{64}", cfg["prior_clock_receipt_sha256"])
    assert b"COPY_EVIDENCE_STAGE=" in files["CLOCK_EXT.ps1"]
    assert b"'demo-copy-evidence'" in files["CLOCK_EXT.ps1"]
    assert b"com.docker.compose.project=demo-copy-evidence" in files["CLOCK_EXT.ps1"]
    assert b"com.docker.compose.project=demo-copy-clock" not in files["CLOCK_EXT.ps1"]
    assert b"COPY_EVIDENCE_RECEIPT.json" in files["COPY_CORE.ps1"]
    assert b"Read-EvidenceSources" in files["COPY_CORE.ps1"]
    assert b"EvidencePriorHandle.Dispose()" in files["COPY_CORE.ps1"]
    assert "RUN_EVIDENCE_REHEARSAL.cmd" in files and "RUN_CLOCK_REHEARSAL.cmd" not in files
    compile(files["copy_worker.py"], "<copy-evidence-worker>", "exec")


@pytest.mark.parametrize("pin", ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64])
def test_invalid_prior_pin_denied_before_any_source_read(pin, monkeypatch):
    monkeypatch.setattr(
        builder, "git", lambda *args: pytest.fail("source read before pin validation")
    )
    with pytest.raises(ValueError, match="EXPLICIT_PRIOR_CLOCK_PIN_REQUIRED"):
        builder.package_files("HEAD", pin)


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_tampered_new_extension_denied_before_docker_or_copy(tmp_path):
    materialize(tmp_path)
    pin = hashlib.sha256((tmp_path / "TRANSFER_MANIFEST.json").read_bytes()).hexdigest()
    (tmp_path / "EVIDENCE_EXT.ps1").write_text("PRIVATE_SENTINEL")
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
    assert result.returncode == 2 and not result.stderr and "PRIVATE_SENTINEL" not in result.stdout
    assert json.loads(result.stdout)["source_state_may_have_changed"] is False


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "fault",
    [
        "valid",
        "hash",
        "source",
        "v2",
        "pending",
        "running",
        "runtime_readonly",
        "network",
        "exit",
        "image",
        "duplicate_id",
        "volume_alias",
        "audit",
        "financial",
        "lineage",
        "boot",
        "after_pin",
    ],
)
def test_prior_clock_receipt_and_resources_checked_before_new_copy(tmp_path, fault):
    materialize(tmp_path)
    ids = [c * 64 for c in "123"]
    images = ["sha256:" + "b" * 64, "sha256:" + "c" * 64]
    volumes = [
        "demo-copy-review-" + role + "-" + "a" * 32 for role in ("database", "runtime", "socket")
    ]
    clock = dict(
        copy_clock_rehearsal="CLOCK_REBASED_HOLD_VERIFIED_KEEP_STOPPED",
        before_snapshot_sha256="a" * 64,
        after_snapshot_sha256="b" * 64,
        prior_audit_events=55,
        audit_events_after=56,
        prior_revision=58,
        revision_after=59,
        cumulative_weight_before=284,
        cumulative_weight_after=284,
        baseline_files_verified=89,
        candidate_files_verified=5,
        startup_hold_seconds=70.001,
        exchange_http_attempts=0,
        order_writes=0,
        diagnostic_after=dict(
            clock_matches=True,
            boot_matches=True,
            prior_audit_events=56,
            snapshot_sha256="b" * 64,
            v2_bound=False,
            pending_read_attempts=0,
            pending_evidence_requests=0,
            historical_unresolved_requests=4,
            stored_day_kst="2026-11-13",
            stored_week_kst="2026-11-09",
        ),
    )
    for name in (
        "clock_rebased",
        "startup_hold_verified",
        "prior_database_rows_preserved",
        "prior_files_preserved",
        "copy_read_sessions_read_only",
        "copy_write_session_explicit",
    ):
        clock[name] = True
    for name in (
        "financial_ledger_changed",
        "period_baselines_changed",
        "request_states_changed",
        "automatic_retry",
        "installation_ready",
        "execution_approved",
        "orders_enabled",
        "live_trading_enabled",
    ):
        clock[name] = False
    value = dict(
        source_reference=builder.PRIOR_CLOCK_SOURCE,
        prior_copy_receipt_sha256="f" * 64,
        isolated_restore="COPY_RECOVERED_AND_ACCOUNT_READS_VERIFIED",
        backup_manifest_sha256="d" * 64,
        backup_receipt_sha256="e" * 64,
        clone_network="none",
        copy_clock_result=clock,
        retained_helper_ids=ids.copy(),
        retained_clone_volumes=volumes.copy(),
        clone_container_id=ids[1],
    )
    for name in (
        "copy_mounts_and_network_verified",
        "input_hashes_reverified",
        "clone_stopped",
        "clone_clean_exit_verified",
        "archive_bytes_and_metadata_verified_before_clone_recovery",
    ):
        value[name] = True
    for name in (
        "original_services_started",
        "original_volume_mounted_in_restore_helpers",
        "financial_write_attempted",
        "installation_ready",
        "automatic_retry",
        "execution_approved",
        "orders_enabled",
        "live_trading_enabled",
    ):
        value[name] = False
    specs = [
        [
            ("/bundle", "bind", "", False),
            ("/clone/database", "volume", volumes[0], True),
            ("/clone/runtime", "volume", volumes[1], True),
            ("/review-socket", "volume", volumes[2], True),
        ],
        [
            ("/var/lib/postgresql", "volume", volumes[0], True),
            ("/review-socket", "volume", volumes[2], True),
        ],
        [
            ("/review-runtime", "volume", volumes[1], True),
            ("/review-socket", "volume", volumes[2], False),
        ],
    ]
    items = [
        dict(
            id=ids[i],
            running=False,
            paused=False,
            dead=False,
            restarting=False,
            restart=dict(Name="no", MaximumRetryCount=0),
            image=images[1 if i == 1 else 0],
            labels={
                "com.docker.compose.project": "demo-copy-clock" if i == 2 else "demo-copy-review"
            },
            mounts=[dict(Destination=d, Type=t, Name=n, RW=w) for d, t, n, w in specs[i]],
        )
        for i in range(3)
    ]
    if fault == "source":
        value["source_reference"] = "0" * 40
    if fault == "v2":
        clock["diagnostic_after"]["v2_bound"] = True
    if fault == "pending":
        clock["diagnostic_after"]["pending_read_attempts"] = 1
    if fault == "running":
        items[2]["running"] = True
    if fault == "runtime_readonly":
        items[2]["mounts"][0]["RW"] = False
    if fault == "image":
        items[1]["image"] = images[0]
    if fault == "duplicate_id":
        value["retained_helper_ids"][2] = ids[1]
    if fault == "volume_alias":
        value["retained_clone_volumes"][0] = "original-db"
    if fault == "audit":
        clock["audit_events_after"] = 55
    if fault == "lineage":
        clock["prior_audit_events"] = 1
        clock["audit_events_after"] = 2
    if fault == "boot":
        clock["diagnostic_after"]["boot_matches"] = False
    if fault == "after_pin":
        clock["diagnostic_after"]["snapshot_sha256"] = "0" * 64
    if fault == "financial":
        clock["financial_ledger_changed"] = True
    receipt = tmp_path / "clock.json"
    receipt.write_text(json.dumps(value))
    (tmp_path / "items.json").write_text(json.dumps(items))
    cfg = dict(
        prior_clock_source_reference=builder.PRIOR_CLOCK_SOURCE,
        prior_clock_receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(),
        prior_copy_receipt_sha256="f" * 64,
        manifest_sha256="d" * 64,
        receipt_sha256="e" * 64,
    )
    if fault == "hash":
        cfg["prior_clock_receipt_sha256"] = "0" * 64
    (tmp_path / "cfg.json").write_text(json.dumps(cfg))
    network = "bridge" if fault == "network" else "none"
    exit_code = "1 false" if fault == "exit" else "0 false"
    script = tmp_path / "prior-test.ps1"
    script.write_text(f"""
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
. '{tmp_path / "EVIDENCE_EXT.ps1"}'
$script:ClockPrior=[pscustomobject]@{{copy_transition_diagnostic=[pscustomobject]@{{snapshot_sha256=('a'*64);prior_audit_events=55}};retained_clone_volumes=@('old-db','old-runtime','old-socket')}}
function Read-ClockSources {{return (Get-Content -Raw '{tmp_path / "items.json"}'|ConvertFrom-Json)}}
function Find-PriorClockReceipt {{return '{receipt}'}}
function Restore-Hash([string]$Path) {{return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}}
function Read-RestoreJson([string]$Path) {{return (Get-Content -Raw $Path|ConvertFrom-Json)}}
function Project([object]$Value) {{return $Value.labels.'com.docker.compose.project'}}
function Resolve-RestoreImageMatch([string]$A,[string]$B) {{return $A -ceq $B}}
function Backup-Docker([string[]]$Arguments) {{if (($Arguments -join ' ') -like '*NetworkMode*') {{return '{network}'}}; return '{exit_code}'}}
$state=[pscustomobject]@{{canonical_images=@('{images[0]}','{images[1]}');database_volume='original-db';runtime_volume='original-runtime'}}
try {{$null=Read-EvidenceSources (Read-RestoreJson '{tmp_path / "cfg.json"}') ([pscustomobject]@{{output_volume='old-output'}}) $state;Write-Output 'OK'}}
catch {{Write-Output $_.Exception.Message;exit 2}}
finally {{if ($null -ne $script:EvidencePriorHandle) {{$script:EvidencePriorHandle.Dispose()}}}}
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
    assert result.returncode == (0 if fault == "valid" else 2) and not result.stderr, (
        result.stdout + result.stderr
    )
    assert (result.stdout.strip() == "OK") == (fault == "valid")
    if fault != "valid":
        assert result.stdout.strip().startswith("EVIDENCE_PRIOR_")
