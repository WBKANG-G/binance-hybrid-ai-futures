"""Expose only safe source-scope probes without a mutation-capable launcher."""

import hashlib
import json
import os
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_source_scope_diagnostic_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch

pytestmark = pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")


def package():
    release = os.getenv("SCOPE_DIAGNOSTIC_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git
    revision = real("rev-parse", "HEAD").decode().strip()

    def read(*args):
        if args[0] == "show" and args[1].startswith(
            revision + ":tools/windows_demo_source_scope_diagnostic/"
        ):
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with patch.object(builder, "git", read):
        return builder.package_files("HEAD")


@pytest.mark.parametrize(
    "step",
    [
        "CONTAINER_COUNTS",
        "ROLE_COUNT",
        "PRIMARY_SELECTION",
        "PRIMARY_BINDINGS",
        "SAVED_SNAPSHOT",
        "COMPLETE",
    ],
)
def test_block_position_retained_without_ids_paths_or_secret_values(tmp_path, step):
    for n, b in package().items():
        (tmp_path / n).write_bytes(b)
    body = f"""
. '{tmp_path}/DIAGNOSTIC_CORE.ps1'
function Read-RestoreJson {{param($Path) return [pscustomobject]@{{}}}}
function Find-RetainedBackup {{param($Config) return 'PRIVATE_PATH_SENTINEL'}}
function Assert-InputHashes {{param($Directory,$Config,$Receipt)}}
function Read-RestoreSources {{param($Config,$Receipt,$State)
 $script:RestoreScopeProbe=[pscustomobject]@{{step='{step}';project_count=8;saved_count=6;role='api';image_match=$false;runtime_volume_match=$true;private='PRIVATE_SECRET_SENTINEL';id='PRIVATE_ID_SENTINEL';fields=@('mounts','PRIVATE_FIELD_SENTINEL')}}
 if('{step}' -cne 'COMPLETE'){{throw 'RESTORE_SOURCE_SCOPE_CHANGED'}}
}}
Invoke-ScopeDiagnostic '{tmp_path}'|ConvertTo-Json -Depth 5 -Compress
"""
    value = launch(tmp_path, body)
    assert value.returncode == 0 and not value.stderr, value.stdout + value.stderr
    assert "PRIVATE_" not in value.stdout
    result = json.loads(value.stdout)
    assert result["scope_probe"]["step"] == step
    assert result["scope_probe"]["project_count"] == 8
    assert result["scope_probe"]["fields"] == ["mounts"]
    assert result["scope_probe"]["image_match"] is False
    assert result["failure_category"] == (
        None if step == "COMPLETE" else "RESTORE_SOURCE_SCOPE_CHANGED"
    )
    assert not result["helper_created"] and not result["originals_started"]


def test_bad_input_does_not_call_docker_or_leak_error(tmp_path):
    (tmp_path / "CORE.ps1").write_bytes(package()["DIAGNOSTIC_CORE.ps1"])
    value = launch(
        tmp_path,
        f"""
. '{tmp_path}/CORE.ps1'
function Read-RestoreJson {{param($Path) throw 'PRIVATE_ARBITRARY_ERROR'}}
function Read-RestoreSources {{throw 'MUST_NOT_RUN'}}
Invoke-ScopeDiagnostic '{tmp_path}'|ConvertTo-Json -Depth 4 -Compress
""",
    )
    assert value.returncode == 0 and not value.stderr
    result = json.loads(value.stdout)
    assert result["failure_category"] == "SCOPE_DIAGNOSTIC_BLOCKED"
    assert result["scope_probe"] == {"step": "NOT_RECORDED"}
    assert "PRIVATE_" not in value.stdout and "MUST_NOT_RUN" not in value.stdout


def test_sealed_package_cannot_create_or_start_and_guards_are_unchanged(tmp_path):
    files = package()
    assert set(n for n in files if n.endswith(".cmd")) == {"RUN_DIAGNOSE.cmd"}
    text = b"\n".join(v for n, v in files.items() if n.endswith(".ps1"))
    for forbidden in (
        b"docker start",
        b"'create'",
        b"'update'",
        b"'kill'",
        b"'rm'",
        b"'stop'",
        b"CompareOnce",
        b"FileAccess]::Write",
        b"Config.Env",
    ):
        assert forbidden not in text
    from scripts.build_windows_source_compare_bundle import package_files as prior

    old = prior(builder.PIN)
    for n in ("CONTEXT.ps1", "SUPPORT.ps1", "SOURCE_SCOPE.ps1", "CONFIG.json"):
        assert files[n] == old[n]
    for n, b in files.items():
        (tmp_path / n).write_bytes(b)
    (tmp_path / "DIAGNOSTIC_CORE.ps1").write_text("PRIVATE_TAMPER_SENTINEL")
    pin = hashlib.sha256(files["TRANSFER_MANIFEST.json"]).hexdigest()
    value = launch(
        tmp_path, f"& '{tmp_path}/RUN_DIAGNOSE.ps1' -ManifestSHA256 '{pin}'\nexit $LASTEXITCODE"
    )
    assert value.returncode == 2 and not value.stderr
    assert "PRIVATE_TAMPER_SENTINEL" not in value.stdout
    assert json.loads(value.stdout)["source_scope_diagnostic"] == "TRANSFER_OR_LAUNCHER_BLOCKED"
