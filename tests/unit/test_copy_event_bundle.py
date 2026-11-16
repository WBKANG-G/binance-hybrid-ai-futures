"""Sealed copy boundary, exact projection payload and native launcher checks."""

import hashlib
import json
import os
import zipfile
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from packages.persistence.demo_order_quota import LocalQuotaStopped
from scripts import build_windows_copy_event_bundle as builder
from services.execution.demo_copy_event_review import plan_from_state, review_copy_request_events
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch
from tests.unit.test_request_reference_bundle import records


def package():
    release = os.getenv("COPY_EVENT_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git
    ref = real("rev-parse", "HEAD").decode().strip()

    def read(*args):
        if args[0] == "show" and args[1].startswith(ref + ":"):
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with ExitStack() as stack:
        for name in ["copy_event", "copy_transition", "response_candidate_review"]:
            stack.enter_context(patch("scripts.build_windows_" + name + "_bundle.git", read))
        return builder.package_files("HEAD")


def fixture_state():
    a, p = records()
    s = a["old_row"]["state"]
    s.update(
        paused=True, daily_key="2026-11-13", weekly_key="2026-11-09", request_no_send_evidence={}
    )
    for o in s["orders"].values():
        o.update(
            state="CANCELED",
            position_state="CLOSED",
            exit_state="NONE",
            filled="0",
            remaining="0",
            open_quantity="0",
        )
    return s, p


def test_explicit_copy_review_precedes_access():
    with pytest.raises(LocalQuotaStopped, match="COPY_EVENTS_EXPLICIT_COPY_REVIEW_REQUIRED"):
        review_copy_request_events(None, {})


def test_plan_never_exports_wire_ids_and_matches_original_four_requests():
    s, p = fixture_state()
    plan = plan_from_state(s, p)
    assert len(plan) == 4 and [r["role"] for r in plan].count("ENTRY") == 2
    assert [r["role"] for r in plan].count("STOP_LOSS") == 1
    assert all(len(r["request_reference_sha256"]) == 64 for r in plan)


@pytest.mark.parametrize(
    "change", ["scope", "day", "paused", "count", "hash", "role", "state", "symbol"]
)
def test_invalid_copy_or_original_plan_refused(change):
    s, p = fixture_state()
    if change == "scope":
        s["mode"] = "LIVE"
    elif change == "day":
        s["daily_key"] = "2026-11-16"
    elif change == "paused":
        s["paused"] = False
    elif change == "count":
        del s["orders"]["33"]
    elif change == "hash":
        p["plan"][0]["request_reference_sha256"] = "0" * 64
    elif change == "role":
        p["plan"][0]["role"] = "EXIT"
    elif change == "state":
        p["plan"][0]["state"] = "NOT_SENT"
    elif change == "symbol":
        s["orders"]["0"]["signal"]["symbol"] = "ETHUSDT"
    with pytest.raises(LocalQuotaStopped):
        plan_from_state(s, p)


def test_sealed_package_preserves_source_restore_checks_and_only_two_read_candidates():
    f = package()
    cfg = json.loads(f["CONFIG.json"])
    manifest = json.loads(f["TRANSFER_MANIFEST.json"])
    assert len(cfg["baseline_files"]) == 89 and len(cfg["candidate_files"]) == 2
    assert (
        cfg["manifest_sha256"] == "2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d"
    )
    for p, pin in manifest["files"].items():
        assert hashlib.sha256(f[p]).hexdigest() == pin
    code = f["COPY_CORE.ps1"].decode() + f["COPY_EXT.ps1"].decode()
    assert "'--network','none'" in code and "'--pull=never'" in code
    assert "target=/review-runtime,readonly,volume-nocopy" in code
    assert "target=/review-socket,readonly,volume-nocopy" in code
    assert "Stop-OwnedClone $clone $volumes[0] $original" in code
    assert "Assert-InputHashes $backup $config $receipt" in code
    assert "Assert-RestoreSourceSnapshot $original $helpers $false" in code
    assert "'READ_ONLY_REQUEST_EVENT_PROJECTION'" in code
    assert "diagnostic=$script:CopyDiagnostic.summary" in code
    assert "SetAccessControl($parent" not in f["COMMON.ps1"].decode()
    assert "'--signal=SIGKILL'" not in code and "'rm'" not in code


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("file", ["COPY_EXT.ps1", "COPY_CORE.ps1", "CONFIG.json", "copy_worker.py"])
def test_tamper_refuses_before_docker(tmp_path, file):
    f = package()
    for name, body in f.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
    pin = hashlib.sha256(f["TRANSFER_MANIFEST.json"]).hexdigest()
    (tmp_path / file).write_text("PRIVATE_SENTINEL_MUST_NOT_RUN")
    r = launch(
        tmp_path,
        f"function docker {{throw 'DOCKER_MUST_NOT_RUN'}}\n& '{tmp_path}/RUN_COPY.ps1' -ManifestSHA256 '{pin}' -ExecuteReview\nexit $LASTEXITCODE",
    )
    assert r.returncode == 2 and not r.stderr and "PRIVATE_SENTINEL" not in r.stdout
    assert "COLD_TRANSFER_OR_LAUNCHER_FAILED" in r.stdout


def test_restore_and_source_scope_support_are_unchanged_from_tested_copy_runner():
    from scripts.build_windows_copy_transition_bundle import package_files as old_package

    f = package()
    real = builder.git
    ref = real("rev-parse", "HEAD").decode().strip()

    def read(*args):
        if args[0] == "show" and args[1].startswith(ref + ":"):
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with ExitStack() as stack:
        for name in ["copy_transition", "response_candidate_review"]:
            stack.enter_context(patch("scripts.build_windows_" + name + "_bundle.git", read))
        old = old_package("HEAD")
    for name in [
        "CONTEXT.ps1",
        "SUPPORT.ps1",
        "SOURCE_SCOPE.ps1",
        "restore_worker.py",
        "cold_archive.py",
        "account_check.sql",
    ]:
        assert f[name] == old[name]
    assert f["COMMON.ps1"] == old["COMMON.ps1"].replace(
        b"copy-review-20261115-", b"copy-event-review-20261116-"
    )


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_ps_parse_and_restore_docker_start_stop_guard_commands_unchanged(tmp_path):
    from scripts.build_windows_copy_transition_bundle import derive_core

    f = package()
    for name in ["COPY_CORE.ps1", "COPY_EXT.ps1", "RUN_COPY.ps1"]:
        (tmp_path / name).write_bytes(f[name])
    old = derive_core((ROOT / "tools/windows_demo_isolated_restore/RESTORE_CORE.ps1").read_text())
    (tmp_path / "old.ps1").write_text(old)
    r = launch(
        tmp_path,
        f"""
function Critical([string]$File) {{
 $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile($File,[ref]$tokens,[ref]$errors)
 if ($errors.Count -gt 0) {{throw 'PARSE_ERROR'}}
 return @($ast.FindAll({{param($n) $n -is [Management.Automation.Language.CommandAst] -and $n.GetCommandName() -in @('docker','Backup-Docker','Stop-OwnedClone','Assert-RestoreSourceSnapshot','Assert-InputHashes','Read-CopySources','Invoke-CopyDiagnostic')}},$true)|ForEach-Object {{$_.Extent.Text}})
}}
if (((Critical '{tmp_path}/old.ps1') -join "`n") -cne ((Critical '{tmp_path}/COPY_CORE.ps1') -join "`n")) {{throw 'RESTORE_COMMANDS_CHANGED'}}
foreach ($name in @('COPY_EXT.ps1','RUN_COPY.ps1')) {{
 $tokens=$null;$errors=$null;$null=[Management.Automation.Language.Parser]::ParseFile((Join-Path '{tmp_path}' $name),[ref]$tokens,[ref]$errors)
 if ($errors.Count -gt 0) {{throw 'PARSE_ERROR'}}
}}
Write-Output 'PARSED_RESTORE_GUARD_COMMANDS_UNCHANGED'
""",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    assert "PARSED_RESTORE_GUARD_COMMANDS_UNCHANGED" in r.stdout
