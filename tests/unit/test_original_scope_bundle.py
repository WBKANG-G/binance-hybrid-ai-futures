"""Read-only launcher lifecycle and no-start enforcement with native PowerShell."""

import hashlib
import io
import json
import os
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_original_scope_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch


def package():
    release = os.getenv("ORIGINAL_SCOPE_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git

    def read(*args):
        if args[0] == "show" and args[1].split(":", 1)[-1] in {
            "tools/windows_demo_original_scope/RUN_SCOPE.ps1",
            "tools/windows_demo_original_scope/README_KO.md",
            "tools/windows_demo_original_scope/DOCKER_READ.ps1",
            "docs/reviews/20261124/SOURCE_ACCEPTANCE_RECEIPT.json",
        }:
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with patch.object(builder, "git", read):
        return builder.package_files("HEAD")


def test_package_preserves_existing_scope_admission_but_removes_creation_and_stop_paths():
    f = package()
    old = builder.financial_files(builder.PIN)
    for n in (
        "SUPPORT.ps1",
        "SOURCE_SCOPE.ps1",
        "COMPARE_ADMISSION.ps1",
        "FINANCIAL_EXT.ps1",
    ):
        assert f[n] == old[n]
    assert (
        f["CONTEXT.ps1"].split(b"function Set-TopologyProbe", 1)[1]
        == old["CONTEXT.ps1"].split(b"function Set-TopologyProbe", 1)[1]
    )
    assert (
        f["COMMON.ps1"].split(b"function Write-BackupJson", 1)[1]
        == old["COMMON.ps1"].split(b"function Write-BackupJson", 1)[1]
    )
    assert (
        b"Invoke-ScopeDockerRead" in f["CONTEXT.ps1"]
        and b"Invoke-ScopeDockerRead" in f["COMMON.ps1"]
    )
    assert b"function Invoke-IsolatedRestore" not in f["COPY_CORE.ps1"]
    assert b"function Stop-OwnedClone" not in f["COPY_CORE.ps1"]
    assert "restore_worker.py" not in f and "account_check.sql" not in f
    m = json.loads(f["TRANSFER_MANIFEST.json"])
    assert all(hashlib.sha256(f[n]).hexdigest() == h for n, h in m["files"].items())


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "fault",
    ["none", "hash", "running", "scope", "compare", "property", "method", "command", "hash-final"],
)
def test_native_launcher_only_reads_and_rechecks_inputs(tmp_path, fault):
    f = package()
    for n, b in f.items():
        (tmp_path / n).write_bytes(b)
    backup = tmp_path / "backup"
    backup.mkdir()
    for n in (
        "BACKUP_RECEIPT.json",
        "BACKUP_STATE.json",
        "manifest.json",
        "database.tar",
        "runtime.tar",
        "images.tar",
    ):
        (backup / n).write_text("{}")
    ext = tmp_path / "FINANCIAL_EXT.ps1"
    ext.write_text(
        ext.read_text()
        + f"""
$script:ScopeHashCount=0
function Find-RetainedBackup {{param($Config) return '{backup}'}}
function Assert-InputHashes {{param($Directory,$Config,$Receipt) $script:ScopeHashCount++;if('{fault}' -ceq 'hash' -or ('{fault}' -ceq 'hash-final' -and $script:ScopeHashCount -eq 3)){{throw 'RESTORE_INPUT_HASH_MISMATCH'}}}}
function Read-CopySources {{param($Config,$Receipt,$State) if('{fault}' -ceq 'compare'){{throw 'COMPARE_HELPER_SCOPE_INVALID'}};if('{fault}' -ceq 'property'){{$x=[pscustomobject]@{{}};$null=$x.PRIVATE_SECRET_SENTINEL}};if('{fault}' -ceq 'method'){{$null=[string]::PRIVATE_SECRET_SENTINEL()}};if('{fault}' -ceq 'command'){{PRIVATE_SECRET_SENTINEL}};if('{fault}' -ceq 'running'){{throw 'RESTORE_ORIGINAL_NOT_STOPPED'}};if('{fault}' -ceq 'scope'){{throw 'HANDOFF_HELPER_CONFIGURATION_CHANGED'}};$null=Backup-Docker @('inspect','readonly');return ,@([pscustomobject]@{{id=('b'*64)}})}}
function Assert-RestoreSourceSnapshot {{param($Original,$Helpers,$Frozen) if($Frozen -or $Helpers.Count -ne 0 -or $script:ScopeHashCount -ne 2){{throw 'TEST_SCOPE_ORDER_INVALID'}};$null=Backup-Docker @('inspect','readonly')}}
function Backup-Docker {{param($Arguments,$Maximum) if($Arguments[0] -cne 'inspect'){{throw 'TEST_MUTATION_FORBIDDEN'}};return 'SAFE'}}
"""
    )
    m = json.loads(f["TRANSFER_MANIFEST.json"])
    m["files"]["FINANCIAL_EXT.ps1"] = hashlib.sha256(ext.read_bytes()).hexdigest()
    raw = json.dumps(m).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(raw)
    before = {n: p.read_bytes() for n, p in ((p.name, p) for p in backup.iterdir())}
    value = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_SCOPE.ps1' -ManifestSHA256 '{hashlib.sha256(raw).hexdigest()}'\nexit $LASTEXITCODE",
    )
    assert not value.stderr, value.stdout + value.stderr
    assert value.returncode == (0 if fault == "none" else 2), value.stdout
    assert before == {p.name: p.read_bytes() for p in backup.iterdir()}
    if fault == "none":
        assert "ORIGINAL_SCOPE_CHECK_COMPLETE_NO_START_NO_WRITES_NO_ORDERS" in value.stdout
        row = json.loads(value.stdout.splitlines()[1])
        assert row["source_acceptance_recorded"] and row["conditional_reuse_approved"]
        assert (
            not row["original_apply_ready"]
            and not row["source_bytes_compared"]
            and row["containers_created"] == 0
        )
    else:
        assert "NO_START_NO_WRITES_NO_ORDERS" not in value.stdout
        result = json.loads(value.stdout.splitlines()[1])
        assert "PRIVATE_SECRET_SENTINEL" not in value.stdout
        assert str(tmp_path) not in value.stdout
        assert result["failure_stage"] == (
            "VERIFY_BACKUP_INITIAL"
            if fault == "hash"
            else "VERIFY_BACKUP_FINAL"
            if fault == "hash-final"
            else "CHECK_ORIGINAL_SCOPE"
        )
        assert result["failure_script"] in {"RUN_SCOPE.ps1", "FINANCIAL_EXT.ps1"}
        assert result["failure_line"] > 0
        if fault == "compare":
            assert result["failure_category"] == "COMPARE_HELPER_SCOPE_INVALID"
        if fault in {"property", "method", "command"}:
            assert result["exception_type"] != "UNKNOWN"


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("name", ["SOURCE_ACCEPTANCE.json", "CONFIG.json", "RUN_SCOPE.ps1"])
def test_tamper_refused_before_any_scope_lookup(tmp_path, name):
    f = package()
    for n, b in f.items():
        (tmp_path / n).write_bytes(b)
    if name == "RUN_SCOPE.ps1":
        # A runner cannot self-check after it is replaced; retain the runner and
        # tamper the manifest it pins instead.
        (tmp_path / "TRANSFER_MANIFEST.json").write_text("{}")
    else:
        (tmp_path / name).write_text("PRIVATE_SENTINEL")
    value = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_SCOPE.ps1' -ManifestSHA256 '{hashlib.sha256(f['TRANSFER_MANIFEST.json']).hexdigest()}'\nexit $LASTEXITCODE",
    )
    assert value.returncode == 2 and not value.stderr
    assert "PRIVATE_SENTINEL" not in value.stdout and "SCOPE_TRANSFER_INVALID" in value.stdout


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("version", ["v1", "v2"])
def test_real_reader_and_comparison_guard_error_was_masked_in_v1(tmp_path, version):
    f = package()
    if version == "v1":
        body = builder.git(
            "show",
            "07fc900811f218518c0e3f236796e71394db6e65:artifacts/windows-demo-original-scope-20261124/windows-demo-original-scope-20261124.zip",
        )
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            prefix = "windows-demo-original-scope-20261124/"
            f = {name[len(prefix) :]: z.read(name) for name in z.namelist()}
    for n, b in f.items():
        (tmp_path / n).write_bytes(b)
    backup = tmp_path / "backup"
    backup.mkdir()
    for n in (
        "BACKUP_RECEIPT.json",
        "BACKUP_STATE.json",
        "manifest.json",
        "database.tar",
        "runtime.tar",
        "images.tar",
    ):
        (backup / n).write_text("{}")
    (backup / "BACKUP_STATE.json").write_text(
        json.dumps(dict(canonical_images=["sha256:" + "a" * 64, "sha256:" + "c" * 64]))
    )
    proof = tmp_path / "proof.json"
    proof.write_text("{}")
    config = json.loads(f["CONFIG.json"])
    config["restore_receipt_sha256"] = hashlib.sha256(proof.read_bytes()).hexdigest()
    (tmp_path / "CONFIG.json").write_text(json.dumps(config))
    ext = tmp_path / "FINANCIAL_EXT.ps1"
    # Keep actual Read-CopySources, Container-Ids, and Assert-CompareHelper. The
    # synthetic handoff invokes the latter chain with an inadmissible running reader.
    ext.write_text(
        ext.read_text()
        + f"""
function Find-RetainedBackup {{param($Config) return '{backup}'}}
function Assert-InputHashes {{param($Directory,$Config,$Receipt)}}
function Find-RestoreEvidence {{param($Config) return '{proof}'}}
function Assert-RestoreEvidence {{param($Value,$Config)}}
function Financial-AllContainerIds {{return ,'{config["comparison_helper_id"]}'}}
function Assert-HandoffStoppedScope {{param($Config,$Backup,$State,$Recovery) $null=Container-Ids;throw 'TEST_GUARD_NOT_CALLED'}}
function Read-Container {{param($Id) return [pscustomobject]@{{image=('sha256:'+('a'*64));running=$true}}}}
"""
    )
    manifest = json.loads(f["TRANSFER_MANIFEST.json"])
    for n in ("FINANCIAL_EXT.ps1", "CONFIG.json", "RUN_SCOPE.ps1"):
        manifest["files"][n] = hashlib.sha256((tmp_path / n).read_bytes()).hexdigest()
    raw = json.dumps(manifest).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(raw)
    value = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_SCOPE.ps1' -ManifestSHA256 '{hashlib.sha256(raw).hexdigest()}'\nexit $LASTEXITCODE",
    )
    assert value.returncode == 2 and not value.stderr, value.stdout + value.stderr
    r = json.loads(value.stdout.splitlines()[1])
    assert r["failure_category"] == (
        "SCOPE_CHECK_BLOCKED" if version == "v1" else "COMPARE_HELPER_SCOPE_INVALID"
    )
    if version == "v2":
        assert r["failure_stage"] == "CHECK_ORIGINAL_SCOPE"
        assert r["failure_script"] == "COMPARE_ADMISSION.ps1" and r["failure_line"] > 0
    assert not r["original_database_started"] and not r["original_database_changed"]
