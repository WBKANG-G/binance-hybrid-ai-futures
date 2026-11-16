"""Read-only five-file private receipt review, without Docker or state changes."""

import copy
import hashlib
import json
import os
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_private_receipt_review_v2_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import fixture_data as old_data
from tests.unit.test_evidence_receipt_review import launch

pytestmark = pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")


def package():
    release = os.getenv("PRIVATE_RECEIPT_REVIEW_V2_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git
    ref = real("rev-parse", "HEAD").decode().strip()

    def read(*args):
        if args[0] == "show" and args[1].startswith(ref + ":"):
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with (
        patch.object(builder, "git", read),
        patch("scripts.build_windows_private_receipt_review_bundle.git", read),
    ):
        return builder.package_files("HEAD")


def data():
    cfg = json.loads(package()["CONFIG.json"])
    _, root, clock, pipeline = old_data()
    root.pop("copy_evidence_result")
    clock.update(
        baseline_files_verified=89,
        candidate_files_verified=16,
        before_snapshot_sha256="a" * 64,
        after_snapshot_sha256="b" * 64,
        protected_database_sha256="d" * 64,
        copy_clock_rehearsal="CLOCK_REBASED_HOLD_VERIFIED_KEEP_STOPPED",
        startup_hold_seconds=70.001,
        exchange_http_attempts=0,
        order_writes=0,
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
        clock[flag] = True
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
        clock[flag] = False
    doc = json.loads(
        (ROOT / "docs/reviews/20261116/PRIVATE_WEEK_DOCKER_VALIDATION.json").read_bytes()
    )
    week = copy.deepcopy(doc["result"])
    week["pipeline"] = pipeline
    pipeline.update(
        before_snapshot_sha256="b" * 64,
        after_snapshot_sha256="c" * 64,
        private_lease_opened_at_capture=True,
        private_tmpfs_credentials_used=True,
        private_preparation_seconds=101.004,
    )
    pipeline["content_review"]["response_body_sha256"] = "e" * 64
    clock.update(private_week_review=week, private_scope_policy=doc["private_scope_policy"])
    ids = [c * 64 for c in "abcde"]
    private = ids[2:]
    token = "1" * 32
    volumes = ["demo-copy-review-" + p + "-" + token for p in ("database", "runtime", "socket")]
    keyvolume = "demo-read-private-keys-" + "2" * 32
    root.update(
        source_reference=cfg["expected_source_reference"],
        private_week_result=clock,
        prior_evidence_receipt_sha256=cfg["prior_evidence_receipt_sha256"],
        late_lease_result_diagnostic=dict(
            reason="NONE",
            late_lease_flag=True,
            preparation_seconds_type="System.Decimal",
            preparation_seconds=101.004,
            preparation_limit_seconds=300,
            signing_lease_limit_seconds=75,
        ),
        retained_helper_ids=ids,
        private_helper_ids=private,
        clone_container_id=ids[1],
        retained_clone_volumes=volumes,
        private_key_volume=keyvolume,
        account=dict(
            paused=True,
            orders=34,
            unresolved_requests=4,
            transaction_read_only="on",
            daily_key="2026-11-13",
            weekly_key="2026-11-09",
            account_state_sha256="a" * 64,
        ),
    )
    for flag in (
        "private_key_session_destroyed",
        "prior_private_resources_stopped_verified",
        "prior_private_v2_stopped_verified",
    ):
        root[flag] = True
    for flag in ("full_financial_proof_verified", "offline_handoff_performed"):
        root[flag] = False
    root.update(actual_http_attempts=0, order_writes=0)
    resources = dict(
        private_helper_ids=private,
        private_key_volume=keyvolume,
        synthetic_keys_only=True,
        private_key_session_destroyed=True,
        automatic_retry=False,
    )
    restore = dict(
        backup_receipt_sha256=cfg["receipt_sha256"],
        manifest_sha256=cfg["manifest_sha256"],
        owned_helper_ids=ids[:1],
        clone_volumes=volumes,
        automatic_resume=False,
    )
    clone = dict(clone_container_id=ids[1], clone_volumes=volumes, original_services_started=False)
    return cfg, dict(
        COPY_PRIVATE_WEEK_V3_RECEIPT=root,
        PRIVATE_WEEK_RESULT=clock,
        PRIVATE_RESOURCES=resources,
        RESTORE_RESOURCES=restore,
        CLONE_CREATED=clone,
    )


def create(tmp_path, records):
    files = package()
    for p, b in files.items():
        (tmp_path / p).write_bytes(b)
    cfg = json.loads(files["CONFIG.json"])
    base = tmp_path / "data" / "WBKANG-DEMO-Backups"
    base.mkdir(parents=True)
    folder = base / (cfg["expected_directory_prefix"] + "1" * 32)
    folder.mkdir()
    for name, obj in records.items():
        (folder / (name + ".json")).write_text(json.dumps(obj))
    return files, base, folder


def snapshot(base):
    return {
        str(p.relative_to(base)): (
            p.lstat().st_mtime_ns,
            hashlib.sha256(p.read_bytes()).hexdigest(),
        )
        for p in base.rglob("*")
        if p.is_file()
    }


@pytest.mark.parametrize(
    "fault",
    [
        "valid",
        "source",
        "evidence_pin",
        "content_string",
        "requirements_string",
        "requirement_missing",
        "authority",
        "counter_string",
        "private_pair_claim",
        "early_lease",
        "duration_string",
        "duration_limit",
        "duration_mismatch",
        "scope_network",
        "key_not_destroyed",
        "clonedb_running",
        "original_started",
        "account_unresolved",
        "account_period",
        "week_period",
        "week_adoption",
        "clock_rebase",
        "clone_alias",
        "key_volume",
        "private_resources",
        "restore_resources",
        "clone_resources",
        "companion",
        "week_pipeline",
        "missing",
        "symlink",
        "oversized",
        "invalid_json",
    ],
)
def test_receipt_and_companions_are_strict_and_never_modified(tmp_path, fault):
    cfg, records = data()
    root = records["COPY_PRIVATE_WEEK_V3_RECEIPT"]
    clock = records["PRIVATE_WEEK_RESULT"]
    p = clock["synthetic_evidence_rehearsal"]
    week = clock["private_week_review"]
    if fault == "source":
        root["source_reference"] = "0" * 40
    if fault == "evidence_pin":
        root["prior_evidence_receipt_sha256"] = "0" * 64
    if fault == "content_string":
        p["content_review"] = "System.Object"
    if fault == "requirements_string":
        p["content_review"]["remaining_requirements"] = "System.Object[]"
    if fault == "requirement_missing":
        p["content_review"]["remaining_requirements"].pop()
    if fault == "authority":
        p["content_review"]["remote_account_identity_verified"] = True
    if fault == "counter_string":
        p["audit_events_after"] = "58"
    if fault == "private_pair_claim":
        p["exchange_credentials_accessed"] = True
    if fault == "early_lease":
        p["private_lease_opened_at_capture"] = False
    if fault == "duration_string":
        p["private_preparation_seconds"] = "101.004"
    if fault == "duration_limit":
        p["private_preparation_seconds"] = 300
    if fault == "duration_mismatch":
        p["private_preparation_seconds"] = 102
    if fault == "scope_network":
        clock["private_scope_policy"]["network_enabled"] = True
    if fault == "key_not_destroyed":
        root["private_key_session_destroyed"] = False
    if fault == "clonedb_running":
        root["clone_stopped"] = False
    if fault == "original_started":
        root["original_services_started"] = True
    if fault == "account_unresolved":
        root["account"]["unresolved_requests"] = 0
    if fault == "account_period":
        root["account"]["daily_key"] = "2026-11-16"
    if fault == "week_period":
        week["stored_week_kst"] = "2026-11-16"
    if fault == "week_adoption":
        week["baseline_adoption_approved"] = True
    if fault == "clock_rebase":
        clock["clock_rebased"] = False
    if fault == "clone_alias":
        root["clone_container_id"] = root["private_helper_ids"][0]
    if fault == "key_volume":
        root["private_key_volume"] = "original-key-volume"
    if fault == "private_resources":
        records["PRIVATE_RESOURCES"]["synthetic_keys_only"] = False
    if fault == "restore_resources":
        records["RESTORE_RESOURCES"]["owned_helper_ids"] = ["f" * 64]
    if fault == "clone_resources":
        records["CLONE_CREATED"]["original_services_started"] = True
    if fault == "week_pipeline":
        week["pipeline"] = copy.deepcopy(p)
        week["pipeline"]["audit_events_after"] = 59
    files, base, folder = create(tmp_path, records)
    if fault == "companion":
        (folder / "PRIVATE_WEEK_RESULT.json").write_text("{}")
    if fault == "missing":
        (folder / "PRIVATE_RESOURCES.json").unlink()
    if fault == "symlink":
        (folder / "PRIVATE_RESOURCES.json").rename(tmp_path / "outside.json")
        (folder / "PRIVATE_RESOURCES.json").symlink_to(tmp_path / "outside.json")
    if fault == "oversized":
        (folder / "PRIVATE_RESOURCES.json").write_bytes(b" " * (1048576 + 1))
    if fault == "invalid_json":
        (folder / "PRIVATE_RESOURCES.json").write_text("{")
    before = snapshot(base)
    r = launch(
        tmp_path,
        f"""
. '{tmp_path}/REVIEW_CORE.ps1'
function docker {{throw 'DOCKER_MUST_NOT_RUN'}}
try {{Review-PrivateReceipts '{base}' (Get-Content -Raw '{tmp_path}/CONFIG.json'|ConvertFrom-Json)|ConvertTo-Json -Depth 16 -Compress}} catch {{Write-Output $_.Exception.Message;exit 2}}
""",
    )
    assert r.returncode == (0 if fault == "valid" else 2) and not r.stderr, r.stdout + r.stderr
    assert snapshot(base) == before
    if fault == "valid":
        v = json.loads(r.stdout)
        assert v["companion_records_checked"] == 4 and len(v["file_sha256"]) == 5
        assert (
            v["receipt_sha256"]
            == hashlib.sha256(
                (folder / "COPY_PRIVATE_WEEK_V3_RECEIPT.json").read_bytes()
            ).hexdigest()
        )
        assert v["private_preparation_seconds"] == 101.004
        assert not any(
            v[k]
            for k in (
                "docker_used",
                "files_changed",
                "services_started",
                "receipt_hash_compared_to_external_pin",
                "current_docker_state_checked",
                "archive_response_bytes_replayed",
                "execution_approved",
            )
        )


def test_actual_launcher_reads_only_and_emits_success(tmp_path):
    _, records = data()
    files, base, _ = create(tmp_path, records)
    before = snapshot(base)
    pin = hashlib.sha256(files["TRANSFER_MANIFEST.json"]).hexdigest()
    r = launch(tmp_path, f"& '{tmp_path}/REVIEW.ps1' -ManifestSHA256 '{pin}'\nexit $LASTEXITCODE")
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    assert "PRIVATE_WEEK_V3_RECEIPTS_VERIFIED_NO_WRITES_NO_DOCKER" in r.stdout
    assert snapshot(base) == before


@pytest.mark.parametrize("fault", ["core", "config", "extra_manifest"])
def test_transfer_tamper_denied_before_inspecting_receipts(tmp_path, fault):
    files = package()
    for p, b in files.items():
        (tmp_path / p).write_bytes(b)
    pin = hashlib.sha256(files["TRANSFER_MANIFEST.json"]).hexdigest()
    if fault == "core":
        (tmp_path / "REVIEW_CORE.ps1").write_text("SENTINEL")
    if fault == "config":
        (tmp_path / "CONFIG.json").write_text("{}")
    if fault == "extra_manifest":
        manifest = json.loads(files["TRANSFER_MANIFEST.json"])
        manifest["files"]["EXTRA.ps1"] = "a" * 64
        body = json.dumps(manifest).encode()
        (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(body)
        pin = hashlib.sha256(body).hexdigest()
    r = launch(tmp_path, f"& '{tmp_path}/REVIEW.ps1' -ManifestSHA256 '{pin}'\nexit $LASTEXITCODE")
    assert r.returncode == 2 and not r.stderr and "SENTINEL" not in r.stdout
    assert json.loads(r.stdout)["failure_category"] == "RECEIPT_TRANSFER_INVALID"


@pytest.mark.parametrize(
    "expression,valid",
    [
        ("[decimal]101.004", True),
        ("[double]101.004", True),
        ("'101.004'", False),
        ("$true", False),
        ("$null", False),
        ("[double]::NaN", False),
        ("[double]::PositiveInfinity", False),
    ],
)
def test_numeric_types_without_coercing_strings(tmp_path, expression, valid):
    (tmp_path / "REVIEW_CORE.ps1").write_bytes(package()["REVIEW_CORE.ps1"])
    r = launch(
        tmp_path,
        f". '{tmp_path}/REVIEW_CORE.ps1'\nReceipt-Numeric ({expression})|ConvertTo-Json -Compress",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    assert json.loads(r.stdout) is valid


def test_companion_object_property_order_is_not_identity(tmp_path):
    _, records = data()
    _, base, folder = create(tmp_path, records)
    clock = records["PRIVATE_WEEK_RESULT"]
    (folder / "PRIVATE_WEEK_RESULT.json").write_text(
        json.dumps(dict(reversed(list(clock.items()))))
    )
    before = snapshot(base)
    r = launch(
        tmp_path,
        f". '{tmp_path}/REVIEW_CORE.ps1'\nReview-PrivateReceipts '{base}' (Get-Content -Raw '{tmp_path}/CONFIG.json'|ConvertFrom-Json)|ConvertTo-Json -Depth 16 -Compress",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    assert snapshot(base) == before


@pytest.mark.parametrize("change", ["append", "truncate", "same_length"])
def test_hash_recheck_detects_changed_open_stream_without_touching_fixture_files(tmp_path, change):
    _, records = data()
    _, base, _ = create(tmp_path, records)
    before = snapshot(base)
    actions = {
        "append": "$stream.Position=$stream.Length;$stream.WriteByte(32)",
        "truncate": "$stream.SetLength($stream.Length-1)",
        "same_length": "$stream.Position=0;$stream.WriteByte(32)",
    }
    r = launch(
        tmp_path,
        f"""
. '{tmp_path}/REVIEW_CORE.ps1'
$script:openOriginal=${{function:Receipt-Open}};$script:contentOriginal=${{function:Receipt-CheckContent}}
$script:streams=@{{}}
function Receipt-Open([string]$Path) {{
 $item=& $script:openOriginal $Path
 $item.handle.Dispose()
 $memory=New-Object IO.MemoryStream
 $memory.Write($item.body,0,$item.body.Length);$memory.Position=0
 $item.handle=$memory;$script:streams[[IO.Path]::GetFileName($Path)]=$memory
 return $item
}}
function Receipt-CheckContent([object]$Value) {{
 & $script:contentOriginal $Value
 $stream=$script:streams['PRIVATE_RESOURCES.json']
 {actions[change]}
}}
try {{Review-PrivateReceipts '{base}' (Get-Content -Raw '{tmp_path}/CONFIG.json'|ConvertFrom-Json)|Out-Null;throw 'UNEXPECTED_SUCCESS'}} catch {{Write-Output $_.Exception.Message;exit 2}}
""",
    )
    assert r.returncode == 2 and not r.stderr, r.stdout + r.stderr
    assert r.stdout.strip() == "RECEIPT_CHANGED_DURING_REVIEW"
    assert snapshot(base) == before


def test_shipped_scripts_have_no_mutating_or_external_commands(tmp_path):
    for p, b in package().items():
        (tmp_path / p).write_bytes(b)
    r = launch(
        tmp_path,
        f"""
$names=@()
foreach ($file in @('{tmp_path}/REVIEW.ps1','{tmp_path}/REVIEW_CORE.ps1')) {{
 $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile($file,[ref]$tokens,[ref]$errors)
 if ($errors.Count -gt 0) {{throw 'PARSE_ERROR'}}
 foreach ($command in $ast.FindAll({{param($node) $node -is [Management.Automation.Language.CommandAst]}},$true)) {{
  $name=$command.GetCommandName();if ($null -ne $name) {{$names+=,$name}}
 }}
 foreach ($member in $ast.FindAll({{param($node) $node -is [Management.Automation.Language.InvokeMemberExpressionAst]}},$true)) {{
  if ($member.Member.Value -in @('Write','WriteAllText','WriteAllBytes','CreateDirectory','SetLength','Start')) {{throw 'MUTATING_METHOD'}}
 }}
}}
foreach ($name in $names) {{if ($name -in @('docker','Start-Process','Invoke-Expression','Set-Content','Add-Content','Out-File','New-Item','Remove-Item','Copy-Item','Move-Item','Set-Item')) {{throw 'MUTATING_COMMAND'}}}}
Write-Output 'READ_ONLY_AST_CHECKED'
""",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    assert r.stdout.strip() == "READ_ONLY_AST_CHECKED"


@pytest.mark.parametrize(
    "case",
    [
        "different_uuid",
        "second_same_source",
        "foreign_source",
        "no_success",
        "wrong_source",
        "invalid_directory",
        "directory_symlink",
    ],
)
def test_unique_source_discovery_never_guesses_latest_or_first(tmp_path, case):
    _, records = data()
    _, base, folder = create(tmp_path, records)
    if case == "different_uuid":
        token = "ab" * 16
        new = base / ("copy-private-week-v3-20261116-" + token)
        folder.rename(new)
        folder = new
        volumes = ["demo-copy-review-" + p + "-" + token for p in ("database", "runtime", "socket")]
        records["COPY_PRIVATE_WEEK_V3_RECEIPT"]["retained_clone_volumes"] = volumes
        records["RESTORE_RESOURCES"]["clone_volumes"] = volumes
        records["CLONE_CREATED"]["clone_volumes"] = volumes
        for name, obj in records.items():
            (folder / (name + ".json")).write_text(json.dumps(obj))
    if case in {"second_same_source", "foreign_source"}:
        other = base / ("copy-private-week-v3-20261116-" + "2" * 32)
        other.mkdir()
        value = copy.deepcopy(records["COPY_PRIVATE_WEEK_V3_RECEIPT"])
        if case == "foreign_source":
            value["source_reference"] = "0" * 40
        (other / "COPY_PRIVATE_WEEK_V3_RECEIPT.json").write_text(json.dumps(value))
    if case == "no_success":
        (folder / "COPY_PRIVATE_WEEK_V3_RECEIPT.json").unlink()
    if case == "wrong_source":
        records["COPY_PRIVATE_WEEK_V3_RECEIPT"]["source_reference"] = "0" * 40
        (folder / "COPY_PRIVATE_WEEK_V3_RECEIPT.json").write_text(
            json.dumps(records["COPY_PRIVATE_WEEK_V3_RECEIPT"])
        )
    if case == "invalid_directory":
        folder.rename(base / "copy-private-week-v3-20261116-invalid")
    if case == "directory_symlink":
        outside = tmp_path / "outside"
        folder.rename(outside)
        folder.symlink_to(outside, target_is_directory=True)
    before = snapshot(base)
    r = launch(
        tmp_path,
        f"""
. '{tmp_path}/REVIEW_CORE.ps1'
function docker {{throw 'DOCKER_MUST_NOT_RUN'}}
try {{Review-PrivateReceipts '{base}' (Get-Content -Raw '{tmp_path}/CONFIG.json'|ConvertFrom-Json)|ConvertTo-Json -Depth 16 -Compress}} catch {{Write-Output $_.Exception.Message;exit 2}}
""",
    )
    valid = case in {"different_uuid", "foreign_source"}
    assert r.returncode == (0 if valid else 2) and not r.stderr, r.stdout + r.stderr
    assert snapshot(base) == before
    if valid:
        result = json.loads(r.stdout)
        assert result["selected_directory_name"] == folder.name
        assert result["selection_method"] == "UNIQUE_EXPECTED_PRODUCER_SUCCESS_RECEIPT"
        assert not result["receipt_hash_compared_to_external_pin"]
