"""Two-pass stopped-source comparison using one fully admitted retained RO reader."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_original_scope_bundle import package_files as scope_files
from scripts.build_windows_source_compare_v2_bundle import package_files as compare_files

FOLDER = "windows-demo-source-refresh-20261124"
SCOPE = "29be59f896af1289f01eeb6495bdd3b094584ada"
COMPARE = "5f9add3e53e633c3051cacfb1274f855713823f2"


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    f = {
        n: b
        for n, b in scope_files(SCOPE).items()
        if not n.endswith(".cmd") and n != "TRANSFER_MANIFEST.json"
    }
    comparison = compare_files(COMPARE)
    for n in ("worker.py", "READ_LIBRARY.py"):
        f[n] = comparison[n]
    for n, path in [
        ("REFRESH_CORE.ps1", "tools/windows_demo_source_refresh/REFRESH_CORE.ps1"),
        ("README_KO.md", "tools/windows_demo_source_refresh/README_KO.md"),
    ]:
        f[n] = git("show", revision + ":" + path)
    core = f.pop("RUN_SCOPE.ps1").decode()
    core = core.replace(
        "[string]$ManifestSHA256)",
        "[string]$ManifestSHA256,[switch]$CheckOnly,[switch]$CompareOnce)",
    )
    core = core.replace(
        "$handles=@();",
        "$handles=@();$helper=$null;$helperStopped=$false;$directory=$null;$mutex=$null;$locked=$false;",
    )
    core = core.replace(
        " $stage='READ_CONFIG'",
        " if(([int][bool]$CheckOnly+[int][bool]$CompareOnce) -ne 1){throw 'COMPARE_EXPLICIT_MODE_REQUIRED'}\n . (Join-Path $PSScriptRoot 'REFRESH_CORE.ps1')\n $stage='READ_CONFIG'",
    )
    start = core.index(" $stage='RECHECK_CONTAINER_SNAPSHOT'")
    end = core.index("} catch {", start)
    stage = git("show", revision + ":tools/windows_demo_source_refresh/COMPARE_STAGE.ps1").decode()
    core = core[:start] + stage + core[end:]
    core = core.replace(
        " [pscustomobject]@{scope_check='BLOCKED_KEEP_STOPPED_NO_RETRY'",
        " if($null -ne $helper -and -not $helperStopped){try{Assert-RefreshReader $config $state $original;$helperStopped=$true}catch{}}\n if($null -ne $directory){try{Write-BackupJson (Join-Path $directory 'FAILURE.json') @{failure_stage=$stage;failure_category=$reason;helper_id=$helper;helper_stopped_verified=$helperStopped;start_attempted=$script:RefreshStartAttempted;automatic_retry=$false}}catch{}}\n [pscustomobject]@{helper_stopped_verified=$helperStopped;retained_helper_id=$helper;private_report_directory=$directory;scope_check='BLOCKED_KEEP_STOPPED_NO_RETRY'",
    )
    core = core.replace(
        " foreach($handle in $handles){$handle.Dispose()}",
        " if($locked){$mutex.ReleaseMutex()};if($null -ne $mutex){$mutex.Dispose()}\n foreach($handle in $handles){$handle.Dispose()}",
    )
    core = core.replace("'RUN_SCOPE.ps1'", "'RUN_COMPARE.ps1'")
    f["COMMON.ps1"] = f["COMMON.ps1"].replace(
        b"copy-financial-20261118-", b"source-refresh-20261124-"
    )
    config = json.loads(f["CONFIG.json"])
    config["source_reference"] = revision
    f["CONFIG.json"] = (json.dumps(config, sort_keys=True) + "\n").encode()
    f["RUN_COMPARE.ps1"] = core.encode()
    lines = core.splitlines()
    i = next(i for i, s in enumerate(lines) if s.lstrip().startswith("$required=@('"))
    lines[i] = " $required=@(" + ",".join("'" + n + "'" for n in f) + ")"
    f["RUN_COMPARE.ps1"] = ("\n".join(lines) + "\n").encode()
    m = (
        json.dumps(dict(source_reference=revision, files=hashes(f)), sort_keys=True, indent=2)
        + "\n"
    ).encode()
    f["TRANSFER_MANIFEST.json"] = m
    for n, mode in [("RUN_CHECK.cmd", "CheckOnly"), ("RUN_COMPARE.cmd", "CompareOnce")]:
        f[n] = (
            '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_COMPARE.ps1" -ManifestSHA256 '
            + hashlib.sha256(m).hexdigest()
            + " -"
            + mode
            + "\r\nexit /b %errorlevel%\r\n"
        ).encode("ascii")
    return f


def build(ref, output):
    f = package_files(ref)
    output.mkdir(parents=True, exist_ok=True)
    p = output / (FOLDER + ".zip")
    with p.open("xb") as stream:
        stream.write(archive({FOLDER + "/" + n: b for n, b in f.items()}))
    print(json.dumps(dict(artifact=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest())))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ref", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    build(a.ref, a.output)
