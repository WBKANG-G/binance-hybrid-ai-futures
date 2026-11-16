"""Hash-only local references: source files untouched, no raw values exported."""

import hashlib
import json
import os
import zipfile
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from scripts import build_windows_private_reference_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch
from tests.unit.test_private_receipt_review_v2 import create, data, snapshot

pytestmark = pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")


def package():
    release = os.getenv("PRIVATE_REFERENCE_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git
    ref = real("rev-parse", "HEAD").decode().strip()

    def source(*args):
        if args[0] == "show" and args[1].startswith(ref + ":"):
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with ExitStack() as stack:
        for name in ("private_reference", "private_receipt_review_v2", "private_receipt_review"):
            stack.enter_context(patch("scripts.build_windows_" + name + "_bundle.git", source))
        return builder.package_files("HEAD")


def setup(tmp_path):
    _, records = data()
    _, base, folder = create(tmp_path, records)
    for p, b in package().items():
        (tmp_path / p).write_bytes(b)
    return base, folder, records


def run(tmp_path, base, mode, extra=""):
    return launch(
        tmp_path,
        f"""
. '{tmp_path}/REVIEW_CORE.ps1'
. '{tmp_path}/REFERENCE_CORE.ps1'
function docker {{throw 'DOCKER_MUST_NOT_RUN'}}
{extra}
Invoke-PrivateReference '{base}' (Get-Content -Raw '{tmp_path}/CONFIG.json'|ConvertFrom-Json) {mode}|ConvertTo-Json -Depth 16 -Compress
""",
    )


def test_preflight_never_creates_destination(tmp_path):
    base, _, _ = setup(tmp_path)
    before = snapshot(base)
    r = run(
        tmp_path, base, "-CheckOnly", "function Reference-NewDirectory {throw 'MUST_NOT_CREATE'}"
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    v = json.loads(r.stdout)
    assert (
        v["private_reference"] == "SOURCE_RECEIPTS_VERIFIED_NO_REFERENCE_CREATED"
        and not v["reference_created"]
    )
    assert snapshot(base) == before


def test_local_hash_reference_roundtrip_does_not_export_raw_receipt_fields(tmp_path):
    base, folder, records = setup(tmp_path)
    records["COPY_PRIVATE_WEEK_V3_RECEIPT"]["arbitrary_private_value"] = (
        "SYNTHETIC_SENTINEL_NOT_TO_EXPORT"
    )
    (folder / "COPY_PRIVATE_WEEK_V3_RECEIPT.json").write_text(
        json.dumps(records["COPY_PRIVATE_WEEK_V3_RECEIPT"])
    )
    destination = tmp_path / "new private reference"
    before = snapshot(base)
    r = run(
        tmp_path,
        base,
        "-CreateReference",
        f"function Reference-NewDirectory {{ $null=[IO.Directory]::CreateDirectory('{destination}');return '{destination}' }}",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    v = json.loads(r.stdout)
    assert v["private_reference"] == "LOCAL_REFERENCE_VERIFIED_EXTERNAL_CUSTODY_PENDING"
    assert v["reference_file_count"] == 2 and v["reference_created"]
    assert not any(
        v[k]
        for k in (
            "source_files_changed",
            "source_receipt_contents_copied",
            "key_material_included",
            "docker_used",
            "services_started",
            "external_copy_performed",
            "independent_external_custody_verified",
            "execution_approved",
        )
    )
    assert sorted(p.name for p in destination.iterdir()) == [
        "REFERENCE_MANIFEST.json",
        "REFERENCE_RECEIPT.json",
    ]
    text = "".join(p.read_text() for p in destination.iterdir())
    assert "SYNTHETIC_SENTINEL_NOT_TO_EXPORT" not in text
    manifest = json.loads((destination / "REFERENCE_MANIFEST.json").read_bytes())
    receipt = json.loads((destination / "REFERENCE_RECEIPT.json").read_bytes())
    assert len(manifest["source_file_sha256"]) == 5
    for name, pin in manifest["source_file_sha256"].items():
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == pin
    assert (
        receipt["manifest_sha256"]
        == v["reference_manifest_sha256"]
        == hashlib.sha256((destination / "REFERENCE_MANIFEST.json").read_bytes()).hexdigest()
    )
    assert (
        v["reference_receipt_sha256"]
        == hashlib.sha256((destination / "REFERENCE_RECEIPT.json").read_bytes()).hexdigest()
    )
    assert snapshot(base) == before


@pytest.mark.parametrize("mode", ["", "-CheckOnly -CreateReference"])
def test_explicit_mode_required_before_read_or_create(tmp_path, mode):
    base, _, _ = setup(tmp_path)
    r = run(
        tmp_path,
        base,
        mode,
        "function Review-PrivateReceipts {throw 'SOURCE_READ_BEFORE_MODE'};function Reference-NewDirectory {throw 'CREATE_BEFORE_MODE'}",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    assert json.loads(r.stdout)["failure_category"] == "REFERENCE_EXPLICIT_MODE_REQUIRED"


def test_corrupt_source_denied_before_destination_creation(tmp_path):
    base, folder, _ = setup(tmp_path)
    (folder / "PRIVATE_RESOURCES.json").write_text("{}")
    before = snapshot(base)
    r = run(
        tmp_path,
        base,
        "-CreateReference",
        "function Reference-NewDirectory {throw 'CREATE_BEFORE_VERIFY'}",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    v = json.loads(r.stdout)
    assert (
        v["private_reference"] == "BLOCKED_NO_AUTOMATIC_RETRY"
        and not v["reference_creation_may_have_started"]
    )
    assert snapshot(base) == before


def test_source_change_between_review_and_lock_denied_before_create(tmp_path):
    base, folder, _ = setup(tmp_path)
    r = run(
        tmp_path,
        base,
        "-CreateReference",
        """
$script:realReview=${function:Review-PrivateReceipts}
function Review-PrivateReceipts([string]$Base,[object]$Config) {
 $v=& $script:realReview $Base $Config
 $v.file_sha256.'PRIVATE_RESOURCES.json'=('0'*64)
 return $v
}
function Reference-NewDirectory {throw 'CREATE_BEFORE_SOURCE_RECHECK'}
""",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    assert json.loads(r.stdout)["failure_category"] == "REFERENCE_SOURCE_CHANGED"


def test_create_new_never_overwrites_existing_target_file(tmp_path):
    base, _, _ = setup(tmp_path)
    destination = tmp_path / "existing reference"
    destination.mkdir()
    target = destination / "REFERENCE_MANIFEST.json"
    target.write_text("UNCHANGED_EXISTING_REFERENCE")
    before = snapshot(base)
    r = run(
        tmp_path,
        base,
        "-CreateReference",
        f"function Reference-NewDirectory {{return '{destination}'}}",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    v = json.loads(r.stdout)
    assert v["private_reference"] == "BLOCKED_NO_AUTOMATIC_RETRY" and not v["automatic_retry"]
    assert (
        target.read_text() == "UNCHANGED_EXISTING_REFERENCE"
        and not (destination / "REFERENCE_RECEIPT.json").exists()
    )
    assert snapshot(base) == before


def test_manifest_readback_failure_leaves_partial_without_retry(tmp_path):
    base, _, _ = setup(tmp_path)
    destination = tmp_path / "partial reference"
    before = snapshot(base)
    r = run(
        tmp_path,
        base,
        "-CreateReference",
        f"""
function Reference-NewDirectory {{$null=[IO.Directory]::CreateDirectory('{destination}');return '{destination}'}}
$script:originalWrite=${{function:Reference-WriteNewJson}}
function Reference-WriteNewJson([string]$Path,[object]$Value) {{& $script:originalWrite $Path ([pscustomobject]@{{wrong_record=$true}})}}
""",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    v = json.loads(r.stdout)
    assert v["failure_category"] == "REFERENCE_READBACK_MISMATCH"
    assert (
        v["retained_partial_reference_directory"] == str(destination) and not v["automatic_retry"]
    )
    assert (destination / "REFERENCE_MANIFEST.json").exists() and not (
        destination / "REFERENCE_RECEIPT.json"
    ).exists()
    assert snapshot(base) == before


@pytest.mark.parametrize("file", ["REFERENCE_CORE.ps1", "CONFIG.json", "REVIEW_CORE.ps1"])
def test_seal_tamper_prevents_any_operation(tmp_path, file):
    files = package()
    for p, b in files.items():
        (tmp_path / p).write_bytes(b)
    pin = hashlib.sha256(files["TRANSFER_MANIFEST.json"]).hexdigest()
    (tmp_path / file).write_text("SENTINEL")
    r = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_REFERENCE.ps1' -ManifestSHA256 '{pin}' -CreateReference\nexit $LASTEXITCODE",
    )
    assert r.returncode == 2 and not r.stderr and "SENTINEL" not in r.stdout
    assert json.loads(r.stdout)["failure_category"] == "REFERENCE_TRANSFER_INVALID"


def test_actual_sealed_check_launcher_is_read_only(tmp_path):
    base, _, _ = setup(tmp_path)
    before = snapshot(base)
    pin = hashlib.sha256((tmp_path / "TRANSFER_MANIFEST.json").read_bytes()).hexdigest()
    r = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_REFERENCE.ps1' -ManifestSHA256 '{pin}' -CheckOnly\nexit $LASTEXITCODE",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    assert "PRIVATE_REFERENCE_CHECK_PASSED_NO_WRITES_NO_DOCKER" in r.stdout
    assert snapshot(base) == before


def test_source_change_during_creation_leaves_only_partial_manifest(tmp_path):
    base, _, _ = setup(tmp_path)
    destination = tmp_path / "partial changed source"
    before = snapshot(base)
    r = run(
        tmp_path,
        base,
        "-CreateReference",
        f"""
$script:originalOpen=${{function:Receipt-Open}};$script:sourceStreams=@{{}}
function Receipt-Open([string]$Path) {{
 $item=& $script:originalOpen $Path
 if ($Path.StartsWith('{base}')) {{
  $item.handle.Dispose();$memory=New-Object IO.MemoryStream
  $memory.Write($item.body,0,$item.body.Length);$memory.Position=0;$item.handle=$memory
  $script:sourceStreams[[IO.Path]::GetFileName($Path)]=$memory
 }}
 return $item
}}
function Reference-NewDirectory {{$null=[IO.Directory]::CreateDirectory('{destination}');return '{destination}'}}
$script:originalWrite=${{function:Reference-WriteNewJson}}
function Reference-WriteNewJson([string]$Path,[object]$Value) {{
 & $script:originalWrite $Path $Value
 $stream=$script:sourceStreams['PRIVATE_RESOURCES.json'];$stream.Position=0;$stream.WriteByte(32)
}}
""",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    v = json.loads(r.stdout)
    assert v["failure_category"] == "REFERENCE_SOURCE_CHANGED" and not v["automatic_retry"]
    assert sorted(p.name for p in destination.iterdir()) == ["REFERENCE_MANIFEST.json"]
    assert snapshot(base) == before


def test_normal_linux_environment_cannot_silently_skip_windows_acl(tmp_path):
    if os.name == "nt":
        pytest.skip("Linux-only unsupported platform refusal")
    base, _, _ = setup(tmp_path)
    before = snapshot(base)
    r = run(tmp_path, base, "-CreateReference")
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    v = json.loads(r.stdout)
    assert v["failure_category"] == "REFERENCE_WINDOWS_ACL_REQUIRED"
    assert not v["reference_creation_may_have_started"] and snapshot(base) == before


def test_emitted_manifest_explains_unresolved_evidence_without_authority(tmp_path):
    base, _, _ = setup(tmp_path)
    destination = tmp_path / "reference facts"
    r = run(
        tmp_path,
        base,
        "-CreateReference",
        f"function Reference-NewDirectory {{$null=[IO.Directory]::CreateDirectory('{destination}');return '{destination}'}}",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    m = json.loads((destination / "REFERENCE_MANIFEST.json").read_bytes())
    assert m["recorded_facts"]["content_requirements_remaining"] == 8
    assert (
        m["recorded_facts"]["orders_preserved"] == 34
        and m["recorded_facts"]["unresolved_requests_preserved"] == 4
    )
    assert m["recorded_facts"]["signing_lease_limit_seconds"] == 75
    for flag in (
        "contains_original_backup_data",
        "contains_source_receipt_contents",
        "contains_key_material",
        "external_copy_performed",
        "independent_external_custody_verified",
        "current_docker_state_checked",
        "archive_response_bytes_replayed",
        "remote_account_identity_verified",
        "baseline_adoption_approved",
        "execution_approved",
        "orders_enabled",
        "live_trading_enabled",
    ):
        assert m[flag] is False


def test_read_only_reviewer_code_is_identical_to_successful_review_release():
    previous = (
        ROOT
        / "artifacts/windows-demo-private-receipt-review-v2-20261116/windows-demo-private-receipt-review-v2-20261116.zip"
    )
    with zipfile.ZipFile(previous) as z:
        assert package()["REVIEW_CORE.ps1"] == z.read(
            "windows-demo-private-receipt-review-v2-20261116/REVIEW_CORE.ps1"
        )


def test_no_external_processes_or_docker_in_reference_commands(tmp_path):
    for p, b in package().items():
        (tmp_path / p).write_bytes(b)
    r = launch(
        tmp_path,
        f"""
foreach ($file in @('{tmp_path}/RUN_REFERENCE.ps1','{tmp_path}/REFERENCE_CORE.ps1')) {{
 $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile($file,[ref]$tokens,[ref]$errors)
 if ($errors.Count -gt 0) {{throw 'PARSE_ERROR'}}
 foreach ($node in $ast.FindAll({{param($n) $n -is [Management.Automation.Language.CommandAst]}},$true)) {{
  if ($node.GetCommandName() -in @('docker','Start-Process','Invoke-Expression','Remove-Item','Move-Item','Copy-Item')) {{throw 'EXTERNAL_OR_DESTRUCTIVE_COMMAND'}}
 }}
}}
Write-Output 'NO_EXTERNAL_PROCESSES'
""",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    assert r.stdout.strip() == "NO_EXTERNAL_PROCESSES"


def test_sealed_create_launcher_end_to_end_with_only_windows_destination_substituted(tmp_path):
    base, _, _ = setup(tmp_path)
    before = snapshot(base)
    destination = tmp_path / "sealed reference output"
    core = tmp_path / "REFERENCE_CORE.ps1"
    core.write_text(
        core.read_text()
        + f"\n# TEST ONLY: Linux cannot exercise Windows NTFS ACL APIs.\nfunction Reference-NewDirectory {{$null=[IO.Directory]::CreateDirectory('{destination}');return '{destination}'}}\n"
    )
    manifest = json.loads((tmp_path / "TRANSFER_MANIFEST.json").read_bytes())
    manifest["files"]["REFERENCE_CORE.ps1"] = hashlib.sha256(core.read_bytes()).hexdigest()
    body = json.dumps(manifest).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(body)
    r = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_REFERENCE.ps1' -ManifestSHA256 '{hashlib.sha256(body).hexdigest()}' -CreateReference\nexit $LASTEXITCODE",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    assert "PRIVATE_REFERENCE_CREATED_LOCAL_ONLY_NO_DOCKER" in r.stdout
    assert len(list(destination.iterdir())) == 2 and snapshot(base) == before
    result = json.loads(next(line for line in r.stdout.splitlines() if line.startswith("{")))
    assert result["private_reference"] == "LOCAL_REFERENCE_VERIFIED_EXTERNAL_CUSTODY_PENDING"
    assert (
        not result["external_copy_performed"]
        and not result["independent_external_custody_verified"]
    )
