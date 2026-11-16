"""Sealed transfer, native launcher refusal and date/resource guards."""

import hashlib
import json
import os
import re
import subprocess
import zipfile
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from scripts import build_windows_private_week_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT


def package():
    release = os.getenv("PRIVATE_WEEK_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git
    revision = real("rev-parse", "HEAD").decode().strip()

    def source(*args):
        if args[0] == "show" and args[1].startswith(revision + ":"):
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with ExitStack() as stack:
        for name in (
            "private_week",
            "copy_evidence",
            "copy_clock",
            "copy_transition",
            "response_candidate_review",
        ):
            stack.enter_context(patch("scripts.build_windows_" + name + "_bundle.git", source))
        return builder.package_files("HEAD")


def materialize(root):
    root.mkdir(parents=True, exist_ok=True)
    for name, body in package().items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)


def native(root, code):
    script = root / "test-native.ps1"
    script.write_text("$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n" + code)
    return subprocess.run(
        [str(PWSH), "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True,
        text=True,
        timeout=25,
        env=dict(
            os.environ,
            XDG_CACHE_HOME=str(root / "cache"),
            XDG_CONFIG_HOME=str(root / "config"),
            XDG_DATA_HOME=str(root / "data"),
        ),
    )


def test_transfer_and_in_memory_candidate_integrity():
    files = package()
    cfg = json.loads(files["CONFIG.json"])
    manifest = json.loads(files["TRANSFER_MANIFEST.json"])["files"]
    required = re.search(r"\$required=@\(([^\n]+)\)", files["RUN_COPY.ps1"].decode())
    assert set(re.findall(r"'([^']+)'", required.group(1))) == set(manifest)
    assert len(cfg["baseline_files"]) == 89 and len(cfg["candidate_files"]) == 15
    assert cfg["review_day_kst"] == cfg["review_week_kst"] == "2026-11-16"
    for p, h in manifest.items():
        assert hashlib.sha256(files[p]).hexdigest() == h
    for p, h in cfg["candidate_files"].items():
        assert hashlib.sha256(files["payload/" + p]).hexdigest() == h
    assert cfg["prior_evidence_receipt_sha256"] == builder.PRIOR_EVIDENCE_PIN
    assert len(builder.PRIOR_EVIDENCE_PIN) == 64
    compile(files["private_worker.py"], "<private-worker>", "exec")
    assert "RUN_PRIVATE_WEEK.cmd" in files and "RUN_EVIDENCE_REHEARSAL.cmd" not in files
    assert b"ConvertTo-Json -Depth 16 -Compress" in files["COPY_CORE.ps1"]


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_native_all_scripts_parse(tmp_path):
    materialize(tmp_path)
    r = native(
        tmp_path,
        f"""
foreach ($file in Get-ChildItem -LiteralPath '{tmp_path}' -Filter '*.ps1') {{
 $tokens=$null;$errors=$null;$null=[Management.Automation.Language.Parser]::ParseFile($file.FullName,[ref]$tokens,[ref]$errors)
 if ($errors.Count -gt 0) {{throw ($errors | ConvertTo-Json -Depth 5)}}
}}
Write-Output 'PARSED'
""",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    assert r.stdout.strip() == "PARSED"


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("file", ["PRIVATE_EXT.ps1", "private_worker.py", "CONFIG.json"])
def test_tampered_transfer_stops_before_docker(tmp_path, file):
    materialize(tmp_path)
    pin = hashlib.sha256((tmp_path / "TRANSFER_MANIFEST.json").read_bytes()).hexdigest()
    (tmp_path / file).write_text("SENTINEL")
    r = native(
        tmp_path,
        f"& '{tmp_path}/RUN_COPY.ps1' -ManifestSHA256 '{pin}' -ExecuteReview\nexit $LASTEXITCODE",
    )
    assert r.returncode == 2 and not r.stderr and "SENTINEL" not in r.stdout
    assert not json.loads(r.stdout)["source_state_may_have_changed"]


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_date_mismatch_denied_before_discovery_or_creation(tmp_path):
    materialize(tmp_path)
    r = native(
        tmp_path,
        f"""
. '{tmp_path}/PRIVATE_EXT.ps1'
function Get-PrivateKstDate {{return '2026-11-17'}}
function Read-EvidenceSources {{throw 'SOURCE_READ_BEFORE_DATE'}}
try {{Read-PrivateSources $null $null $null;throw 'UNEXPECTED_SUCCESS'}} catch {{if ($_.Exception.Message -cne 'PRIVATE_REVIEW_DATE_MISMATCH') {{throw}}}}
Write-Output 'DATE_BLOCKED'
""",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    assert r.stdout.strip() == "DATE_BLOCKED"


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
def test_prior_evidence_receipt_and_resources_checked_before_new_copy(tmp_path, fault):
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
        candidate_files_verified=10,
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
        source_reference=builder.PRIOR_EVIDENCE_SOURCE,
        prior_copy_receipt_sha256="f" * 64,
        isolated_restore="COPY_RECOVERED_AND_ACCOUNT_READS_VERIFIED",
        backup_manifest_sha256="d" * 64,
        backup_receipt_sha256="e" * 64,
        clone_network="none",
        copy_evidence_result=clock,
        prior_clock_receipt_sha256="9" * 64,
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
                "com.docker.compose.project": "demo-copy-evidence" if i == 2 else "demo-copy-review"
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
    clock["synthetic_evidence_rehearsal"] = dict(
        synthetic_copy_evidence_rehearsal="PIPELINE_VERIFIED_SYNTHETIC_ONLY_KEEP_STOPPED",
        synthetic_sender_calls=1,
        exchange_http_attempts=0,
        audit_events_before=56,
        audit_events_after=58,
        content_review=dict(remaining_requirements=["test"] * 8),
    )
    receipt = tmp_path / "evidence.json"
    receipt.write_text(json.dumps(value))
    (tmp_path / "items.json").write_text(json.dumps(items))
    cfg = dict(
        prior_evidence_source_reference=builder.PRIOR_EVIDENCE_SOURCE,
        prior_evidence_receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(),
        prior_copy_receipt_sha256="f" * 64,
        manifest_sha256="d" * 64,
        receipt_sha256="e" * 64,
    )
    cfg["prior_clock_receipt_sha256"] = "9" * 64
    if fault == "hash":
        cfg["prior_evidence_receipt_sha256"] = "0" * 64
    (tmp_path / "cfg.json").write_text(json.dumps(cfg))
    network = "bridge" if fault == "network" else "none"
    exit_code = "1 false" if fault == "exit" else "0 false"
    script = tmp_path / "prior-test.ps1"
    script.write_text(f"""
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
. '{tmp_path / "PRIVATE_EXT.ps1"}'
function Get-PrivateKstDate {{return '2026-11-16'}}
$script:ClockPrior=[pscustomobject]@{{copy_transition_diagnostic=[pscustomobject]@{{snapshot_sha256=('a'*64);prior_audit_events=55}};retained_clone_volumes=@('old-db','old-runtime','old-socket')}}
function Read-EvidenceSources {{return (Get-Content -Raw '{tmp_path / "items.json"}'|ConvertFrom-Json)}}
function Find-PriorEvidenceReceipt {{return '{receipt}'}}
function Restore-Hash([string]$Path) {{return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}}
function Read-RestoreJson([string]$Path) {{return (Get-Content -Raw $Path|ConvertFrom-Json)}}
function Project([object]$Value) {{return $Value.labels.'com.docker.compose.project'}}
function Resolve-RestoreImageMatch([string]$A,[string]$B) {{return $A -ceq $B}}
function Backup-Docker([string[]]$Arguments) {{if (($Arguments -join ' ') -like '*NetworkMode*') {{return '{network}'}}; return '{exit_code}'}}
$state=[pscustomobject]@{{canonical_images=@('{images[0]}','{images[1]}');database_volume='original-db';runtime_volume='original-runtime'}}
try {{$null=Read-PrivateSources (Read-RestoreJson '{tmp_path / "cfg.json"}') ([pscustomobject]@{{output_volume='old-output'}}) $state;Write-Output 'OK'}}
catch {{Write-Output $_.Exception.Message;exit 2}}
finally {{if ($null -ne $script:PrivatePriorHandle) {{$script:PrivatePriorHandle.Dispose()}}}}
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
        assert result.stdout.strip().startswith("PRIVATE_PRIOR_")


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("stop_fault", [False, True])
def test_failed_reader_still_attempts_every_owned_stop_and_only_probes_after_stop(
    tmp_path, stop_fault
):
    materialize(tmp_path)
    r = native(
        tmp_path,
        f"""
. '{tmp_path}/PRIVATE_EXT.ps1'
$script:calls=@();$script:roles=@();$script:serial=0
function Write-BackupJson {{}}
function Assert-RestoreSourceSnapshot {{}}
function Backup-Docker([string[]]$Arguments) {{
 if ($Arguments[0] -ceq 'volume' -and $Arguments[1] -ceq 'ls') {{return}}
 if ($Arguments[0] -ceq 'volume') {{return $Arguments[-1]}}
 return $Arguments[-1]
}}
function Private-Create([string]$Role) {{
 $script:serial++;$id=([string]$script:serial)*64;$script:PrivateHelperIDs+=,$id
 $script:PrivateCreation[$id]=[pscustomobject]@{{Image='sha256:'+('a'*64)}}
 return $id
}}
function Private-RunWorker([string]$Id,[string]$Role) {{
 $script:roles+=,$Role
 if ($Role -ceq 'REGISTER') {{return [pscustomobject]@{{private_key_session='SYNTHETIC_REGISTERED_IN_NEW_TMPFS';real_keys_used=$false}}}}
 if ($Role -ceq 'READ') {{throw 'PRIVATE_WORKER_BLOCKED'}}
 if ($Role -ceq 'PROBE_EMPTY') {{return [pscustomobject]@{{private_key_session='EMPTY_AFTER_LAST_HOLDER_STOP';files_created=$false}}}}
 throw 'BAD_ROLE'
}}
function Private-Stop([string]$Id) {{
 $script:calls+=,$Id
 if (${str(stop_fault).lower()} -and $Id -ceq ('3'*64)) {{throw 'PRIVATE_TEST_STOP_FAILED'}}
}}
$state=[pscustomobject]@{{canonical_images=@('sha256:'+('a'*64))}}
try {{$null=Invoke-PrivateDiagnostic '' $null $state ('demo-copy-review-runtime-'+('a'*32)) ('demo-copy-review-socket-'+('a'*32)) @() @() '{tmp_path}';throw 'UNEXPECTED_SUCCESS'}}
catch {{if ($_.Exception.Message -cne 'PRIVATE_WORKER_BLOCKED') {{throw}}}}
if (($script:calls -join '|') -cne ((@(('3'*64),('2'*64),('1'*64)){"" if stop_fault else "+@(('2'*64))"}) -join '|')) {{throw 'STOP_SKIPPED'}}
if ($script:PrivateEmptyVerified -ne ${str(not stop_fault).lower()}) {{throw 'DESTRUCTION_FALSE_CLAIM'}}
if (($script:roles -ccontains 'PROBE_EMPTY') -ne ${str(not stop_fault).lower()}) {{throw 'PROBED_BEFORE_STOP'}}
Write-Output 'CLEANUP_CHECKED'
""",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    assert r.stdout.strip().endswith("CLEANUP_CHECKED")


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_resource_records_are_append_only_and_final_snapshot_is_separate(tmp_path):
    materialize(tmp_path)
    r = native(
        tmp_path,
        f"""
. '{tmp_path}/COMMON.ps1'
. '{tmp_path}/PRIVATE_EXT.ps1'
Private-Record '{tmp_path}'
$script:PrivateHelperIDs=@(('a'*64))
Private-Record '{tmp_path}'
$script:PrivateEmptyVerified=$true
Private-Record '{tmp_path}' -Final
""",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    first = json.loads((tmp_path / "PRIVATE_RESOURCES_0000.json").read_bytes())
    second = json.loads((tmp_path / "PRIVATE_RESOURCES_0001.json").read_bytes())
    final = json.loads((tmp_path / "PRIVATE_RESOURCES.json").read_bytes())
    assert first["private_helper_ids"] == [] and not first["private_key_session_destroyed"]
    assert (
        second["private_helper_ids"] == ["a" * 64] and not second["private_key_session_destroyed"]
    )
    assert final["private_helper_ids"] == ["a" * 64] and final["private_key_session_destroyed"]


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "fault",
    [
        "valid",
        "http",
        "order",
        "short_hold",
        "identity",
        "adoption",
        "scope_identity",
        "stored_week",
        "content_string",
    ],
)
def test_result_never_promotes_synthetic_evidence_to_authority(tmp_path, fault):
    materialize(tmp_path)
    cfg = json.loads((tmp_path / "CONFIG.json").read_bytes())
    actual = json.loads(
        (ROOT / "docs/reviews/20261116/PRIVATE_WEEK_DOCKER_VALIDATION.json").read_bytes()
    )
    week = actual["result"]
    pipeline = week["pipeline"]
    count = pipeline["audit_events_before"]
    value = dict.fromkeys(cfg["worker_report_keys"])
    value.update(
        copy_clock_rehearsal="CLOCK_REBASED_HOLD_VERIFIED_KEEP_STOPPED",
        before_snapshot_sha256="a" * 64,
        after_snapshot_sha256="b" * 64,
        protected_database_sha256="c" * 64,
        baseline_files_verified=89,
        candidate_files_verified=15,
        prior_audit_events=count - 1,
        audit_events_after=count,
        prior_revision=0,
        revision_after=1,
        cumulative_weight_before=0,
        cumulative_weight_after=0,
        startup_hold_seconds=70.001,
        exchange_http_attempts=0,
        order_writes=0,
        private_week_review=week,
        synthetic_evidence_rehearsal=pipeline,
        private_scope_policy=actual["private_scope_policy"],
    )
    for flag in (
        "clock_rebased",
        "startup_hold_verified",
        "prior_database_rows_preserved",
        "prior_files_preserved",
        "copy_read_sessions_read_only",
        "copy_write_session_explicit",
        "synthetic_key_file_unchanged",
    ):
        value[flag] = True
    for flag in (
        "financial_ledger_changed",
        "period_baselines_changed",
        "request_states_changed",
        "automatic_retry",
        "installation_ready",
        "execution_approved",
        "orders_enabled",
        "live_trading_enabled",
    ):
        value[flag] = False
    if fault == "http":
        value["exchange_http_attempts"] = 1
    if fault == "order":
        value["order_writes"] = 1
    if fault == "short_hold":
        value["startup_hold_seconds"] = 0
    if fault == "identity":
        week["remote_account_identity_verified"] = True
    if fault == "adoption":
        week["baseline_adoption_approved"] = True
    if fault == "scope_identity":
        value["private_scope_policy"]["remote_account_identity_verified"] = True
    if fault == "stored_week":
        week["stored_week_kst"] = "2026-11-16"
    if fault == "content_string":
        pipeline["content_review"] = "PSCustomObject string"
    (tmp_path / "value.json").write_text(json.dumps(value))
    r = native(
        tmp_path,
        f"""
. '{tmp_path}/COPY_CORE.ps1'
. '{tmp_path}/PRIVATE_EXT.ps1'
$script:ClockPrior=[pscustomobject]@{{copy_transition_diagnostic=[pscustomobject]@{{snapshot_sha256=('a'*64)}}}}
try {{Assert-PrivateWeekResult (Read-RestoreJson '{tmp_path}/value.json') (Read-RestoreJson '{tmp_path}/CONFIG.json');Write-Output 'OK'}} catch {{Write-Output $_.Exception.Message;exit 2}}
""",
    )
    assert r.returncode == (0 if fault == "valid" else 2) and not r.stderr, r.stdout + r.stderr
    if fault == "valid":
        assert r.stdout.strip() == "OK"
    else:
        assert r.stdout.strip().startswith("PRIVATE_")


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("original", [True, False])
def test_stop_never_signals_original_or_unowned_id(tmp_path, original):
    materialize(tmp_path)
    r = native(
        tmp_path,
        f"""
. '{tmp_path}/PRIVATE_EXT.ps1'
function Backup-Docker {{throw 'DOCKER_CALLED_BEFORE_OWNERSHIP_CHECK'}}
$old=@();if (${str(original).lower()}) {{$old=@([pscustomobject]@{{id=('a'*64)}})}}
try {{Private-Stop ('a'*64) $old ('sha256:'+('b'*64));throw 'UNEXPECTED_SUCCESS'}}
catch {{if ($_.Exception.Message -cne 'PRIVATE_HELPER_ID_INVALID') {{throw}}}}
Write-Output 'NO_SIGNAL'
""",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    assert r.stdout.strip() == "NO_SIGNAL"


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_owned_stop_uses_portable_signal_and_rechecks_state(tmp_path):
    materialize(tmp_path)
    r = native(
        tmp_path,
        f"""
. '{tmp_path}/PRIVATE_EXT.ps1'
$script:checks=0;$script:commands=@()
function Private-AssertManaged {{$script:checks++;return [pscustomobject]@{{State=[pscustomobject]@{{Running=($script:checks -eq 1);Restarting=$false}}}}}}
function Backup-Docker([string[]]$Arguments) {{$script:commands+=,($Arguments -join '|');return $Arguments[-1]}}
Private-Stop ('a'*64) @() ('sha256:'+('b'*64))
if ($script:checks -ne 2 -or $script:commands.Count -ne 1 -or $script:commands[0] -cne ('kill|--signal=SIGINT|'+('a'*64))) {{throw 'UNEXPECTED_SIGNAL_OR_MISSING_RECHECK'}}
Write-Output 'OWNED_STOP_CHECKED'
""",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    assert r.stdout.strip() == "OWNED_STOP_CHECKED"
