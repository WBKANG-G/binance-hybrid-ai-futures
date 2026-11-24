"""Read-only launcher lifecycle and no-start enforcement with native PowerShell."""

import hashlib
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
        "CONTEXT.ps1",
        "SUPPORT.ps1",
        "SOURCE_SCOPE.ps1",
        "COMPARE_ADMISSION.ps1",
        "FINANCIAL_EXT.ps1",
    ):
        assert f[n] == old[n]
    assert b"function Invoke-IsolatedRestore" not in f["COPY_CORE.ps1"]
    assert b"function Stop-OwnedClone" not in f["COPY_CORE.ps1"]
    assert "restore_worker.py" not in f and "account_check.sql" not in f
    m = json.loads(f["TRANSFER_MANIFEST.json"])
    assert all(hashlib.sha256(f[n]).hexdigest() == h for n, h in m["files"].items())


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("fault", ["none", "hash", "running", "scope"])
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
function Assert-InputHashes {{param($Directory,$Config,$Receipt) $script:ScopeHashCount++;if('{fault}' -ceq 'hash'){{throw 'RESTORE_INPUT_HASH_MISMATCH'}}}}
function Read-CopySources {{param($Config,$Receipt,$State) if('{fault}' -ceq 'running'){{throw 'RESTORE_ORIGINAL_NOT_STOPPED'}};if('{fault}' -ceq 'scope'){{throw 'HANDOFF_HELPER_CONFIGURATION_CHANGED'}};$null=Backup-Docker @('inspect','readonly');return ,@([pscustomobject]@{{id=('b'*64)}})}}
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
