"""Offline receipt-linked plan: native PowerShell, no external process or database."""

import hashlib
import json
import os
import zipfile
from unittest.mock import patch
from uuid import UUID

import pytest

from scripts import build_windows_write_plan_bundle as builder
from tests.unit.test_cold_backup_executor import ROOT
from tests.unit.test_evidence_receipt_review import launch


def package():
    release = os.getenv("WRITE_PLAN_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git
    ref = real("rev-parse", "HEAD").decode().strip()

    def read(*args):
        if args[0] == "show" and args[1].startswith(ref + ":"):
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with patch.object(builder, "git", read):
        return builder.package_files("HEAD")


def evidence():
    r = dict(
        original_inspection="ORIGINAL_SQL_READ_ONLY_OBSERVED_AND_TEMP_SERVER_CLEANLY_STOPPED",
        actual_http_attempts=0,
        order_writes=0,
        temporary_original_server_id="b" * 64,
        account_state_pg_text_sha256="139d14f4234fd2d2a9dfa50425f135cf28b1d9576f9f4b19508fe3261dd07450",
    )
    for k in (
        "current_original_sql_queried",
        "current_original_fence_verified",
        "current_original_owner_observed",
        "temporary_original_server_stopped",
    ):
        r[k] = True
    for k in (
        "original_ledger_write_attempted",
        "original_service_containers_started",
        "original_owner_lease_changed_by_tool",
        "original_fence_changed_by_tool",
        "baseline_adopted",
        "request_states_changed",
        "original_apply_ready",
        "apply_token_created",
        "execution_approved",
        "orders_enabled",
        "live_trading_enabled",
    ):
        r[k] = False
    r["ownership"] = dict(
        fence=110,
        owner_present=False,
        owner_sha256=None,
        lease_present=True,
        lease_state="EXPIRED",
        ownership_row_sha256="c" * 64,
        lease_clock_scope="ISOLATED_ORIGINAL_DATABASE_STATEMENT_TIMESTAMP",
        original_owner_verified=True,
        row_lock_acquired=False,
        lease_until_utc="2026-11-12T00:40:54.427273Z",
    )
    p = dict(
        before_state_pg_text_sha256=r["account_state_pg_text_sha256"],
        candidate_financial_pg_text_sha256="d" * 64,
        changed_top_level_fields=["daily_key", "weekly_key"],
        proposed_fields=dict(
            daily_key="2026-11-24",
            weekly_key="2026-11-23",
            daily_base="4998.74183608",
            weekly_base="4998.74183608",
            reconciled=False,
            stream_ready=False,
        ),
    )
    for k in (
        "protected_jsonb_preserved",
        "orders_requests_operations_preserved",
        "paused_preserved",
        "candidate_paused",
    ):
        p[k] = True
    for k in ("audit_record_created", "commit_payload_verified", "apply_token_created"):
        p[k] = False
    r["period_candidate"] = p
    return {
        "ORIGINAL_INSPECTION_RECEIPT.json": r,
        "ORIGINAL_SERVER_RESOURCE.json": dict(
            container_id="b" * 64,
            original_database_volume="hybrid-windows_postgres_data",
            network="none",
            original_runtime_mounted=False,
            logical_writes_authorized=False,
        ),
        "SOURCE_REFRESH_RECEIPT.json": dict(
            source_refresh="STOPPED_ORIGINAL_BYTES_AND_METADATA_MATCH_PINNED_BACKUP",
            passes=2,
            manifest_sha256="2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d",
            source_data_written=False,
        ),
    }


def setup(tmp_path, data):
    f = package()
    for n, b in f.items():
        (tmp_path / n).write_bytes(b)
    # Only replace NTFS ACL plumbing for Linux; keep production evidence/plan code intact.
    (tmp_path / "PRIVATE_STORAGE.ps1").write_text("""
function Reference-AssertPrivateDirectory([string]$Path){if(-not [IO.Directory]::Exists($Path)){throw 'REFERENCE_DIRECTORY_MISSING'}}
function Reference-NewDirectory([string]$Token){$p=Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) ('WBKANG-DEMO-WritePlans/write-plan-'+$Token);$null=[IO.Directory]::CreateDirectory($p);return $p}
""")
    m = json.loads(f["TRANSFER_MANIFEST.json"])
    m["files"]["PRIVATE_STORAGE.ps1"] = hashlib.sha256(
        (tmp_path / "PRIVATE_STORAGE.ps1").read_bytes()
    ).hexdigest()
    raw = json.dumps(m).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(raw)
    source = (
        tmp_path
        / "data/WBKANG-DEMO-Backups/original-inspection-20261124-26b94531ce44b9cf5b5816fdf1d81447"
    )
    source.mkdir(parents=True)
    for n, v in data.items():
        (source / n).write_text(json.dumps(v))
    return hashlib.sha256(raw).hexdigest(), source


def run(tmp_path, digest, mode):
    return launch(
        tmp_path,
        f"function docker {{throw 'TEST_EXTERNAL_CALL'}}\nfunction python {{throw 'TEST_EXTERNAL_CALL'}}\n& '{tmp_path}/RUN_PREPARE.ps1' -ManifestSHA256 '{digest}' -{mode}\nexit $LASTEXITCODE",
    )


@pytest.mark.parametrize("mode", ["CheckOnly", "Prepare"])
def test_offline_plan_no_db_no_authority_preserves_evidence(tmp_path, mode):
    digest, source = setup(tmp_path, evidence())
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    v = run(tmp_path, digest, mode)
    assert v.returncode == 0, v.stdout + v.stderr
    assert not v.stderr and "TEST_EXTERNAL_CALL" not in v.stdout
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before
    plans = list((tmp_path / "data").rglob("UNAPPROVED_WRITE_PLAN.json"))
    if mode == "CheckOnly":
        assert not plans
        return
    assert len(plans) == 1
    plan = json.loads(plans[0].read_bytes())
    screen = json.loads(v.stdout.splitlines()[1])
    assert UUID(plan["operation_id"]).version == 4
    assert screen["plan_file_sha256"] == hashlib.sha256(plans[0].read_bytes()).hexdigest()
    assert plan["expected_fence"] == 110 and plan["proposed_fence"] == 111
    assert plan["owner_and_lease_action"] == "PRESERVE_EXACTLY_NO_TAKEOVER_OR_CLEANUP"
    assert plan["expected_lease_until_utc"] == "2026-11-12T00:40:54.427273Z"
    assert all(
        plan["source_files_sha256"][n] == hashlib.sha256(b).hexdigest() for n, b in before.items()
    )
    assert not any(
        plan[k]
        for k in (
            "original_apply_ready",
            "original_ledger_write_authorized",
            "commit_payload_verified",
            "apply_token_created",
            "baseline_adopted",
            "orders_enabled",
            "live_trading_enabled",
        )
    )


@pytest.mark.parametrize(
    "fault",
    [
        "state",
        "fence",
        "owner",
        "lease",
        "lease-date",
        "resource",
        "period",
        "amount",
        "protected",
        "token",
        "comparison",
        "missing",
        "transfer",
    ],
)
def test_invalid_sources_fail_without_plan_or_external_access(tmp_path, fault):
    data = evidence()
    r = data["ORIGINAL_INSPECTION_RECEIPT.json"]
    p = r["period_candidate"]
    o = r["ownership"]
    if fault == "state":
        r["account_state_pg_text_sha256"] = "a" * 64
    if fault == "fence":
        o["fence"] = 111
    if fault == "owner":
        o["owner_present"] = True
    if fault == "lease":
        o["lease_state"] = "ACTIVE"
    if fault == "lease-date":
        o["lease_until_utc"] = "2026-11-13T00:40:54.427273Z"
    if fault == "resource":
        data["ORIGINAL_SERVER_RESOURCE.json"]["container_id"] = "a" * 64
    if fault == "period":
        p["proposed_fields"]["daily_key"] = "2026-11-25"
    if fault == "amount":
        p["proposed_fields"]["daily_base"] = "5000"
    if fault == "protected":
        p["protected_jsonb_preserved"] = False
    if fault == "token":
        p["apply_token_created"] = True
    if fault == "comparison":
        data["SOURCE_REFRESH_RECEIPT.json"]["source_data_written"] = True
    if fault == "missing":
        del data["ORIGINAL_SERVER_RESOURCE.json"]
    digest, _ = setup(tmp_path, data)
    if fault == "transfer":
        (tmp_path / "PLAN_CORE.ps1").write_text('throw "TAMPERED"')
    v = run(tmp_path, digest, "Prepare")
    assert v.returncode == 2 and not v.stderr, v.stdout + v.stderr
    assert "BLOCKED_NO_ORIGINAL_CHANGE" in v.stdout and "TEST_EXTERNAL_CALL" not in v.stdout
    assert not list((tmp_path / "data").rglob("UNAPPROVED_WRITE_PLAN.json"))


def test_manifest_and_runtime_are_offline_and_original_writer_is_disabled():
    f = package()
    m = json.loads(f["TRANSFER_MANIFEST.json"])
    assert all(hashlib.sha256(f[n]).hexdigest() == h for n, h in m["files"].items())
    assert all(
        word not in f[n].decode().lower()
        for n in ("RUN_PREPARE.ps1", "PLAN_CORE.ps1")
        for word in (
            "start-process",
            "invoke-restmethod",
            "invoke-webrequest",
            "docker.exe",
            "psycopg",
        )
    )
    assert (
        "ORIGINAL_PERIOD_WRITE_APPROVAL_AND_HOST_BOUNDARY_REQUIRED"
        in f["transaction_candidate.py"].decode()
    )


def test_held_source_hash_change_is_detected(tmp_path):
    f = package()
    for n in ("READ_CORE.ps1", "PLAN_CORE.ps1"):
        (tmp_path / n).write_bytes(f[n])
    source = tmp_path / "source.json"
    source.write_text("{}")
    v = launch(
        tmp_path,
        f"""
. '{tmp_path}/READ_CORE.ps1'
. '{tmp_path}/PLAN_CORE.ps1'
$s=[IO.File]::Open('{source}',[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
$streams=New-Object Collections.ArrayList
$null=$streams.Add([pscustomobject]@{{stream=$s;sha256=(Inspect-StreamHash $s);name='source.json'}})
try {{$s.Position=0;$s.WriteByte(91);$s.Flush($true);Plan-Recheck $streams;throw 'UNDETECTED'}}catch {{if($_.Exception.Message -cne 'PLAN_SOURCE_HASH_CHANGED'){{throw}};Write-Output 'HASH_CHANGE_DETECTED'}}finally{{$s.Dispose()}}
""",
    )
    assert v.returncode == 0 and not v.stderr, v.stdout + v.stderr
    assert "HASH_CHANGE_DETECTED" in v.stdout
