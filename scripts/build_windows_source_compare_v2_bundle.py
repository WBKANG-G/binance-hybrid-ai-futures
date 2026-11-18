"""Use receipt-bound post-restore scope admission for the stopped-source comparator."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_offline_handoff_bundle import RESTORE_RECEIPT, RESTORE_SOURCE
from scripts.build_windows_source_compare_bundle import package_files as prior

FOLDER = "windows-demo-source-compare-v2-20261118"
PIN = "a1a8435a963a01b9b9d9f08f40cf8180b4d9188c"


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    files = {
        n: b
        for n, b in prior(PIN).items()
        if not n.endswith(".cmd") and n != "TRANSFER_MANIFEST.json"
    }
    config = json.loads(files["CONFIG.json"])
    config.update(
        source_reference=revision,
        restore_receipt_sha256=RESTORE_RECEIPT,
        restore_source_reference=RESTORE_SOURCE,
    )
    files["CONFIG.json"] = (json.dumps(config, sort_keys=True) + "\n").encode()
    evidence = git("show", PIN + ":tools/windows_demo_offline_handoff/HANDOFF_CORE.ps1").decode()
    files["RESTORE_EVIDENCE.ps1"] = evidence[
        : evidence.index("function Assert-HandoffMediaFacts(")
    ].encode()
    for n in ("REVIEWED_SCOPE.ps1", "README_KO.md"):
        files[n] = git("show", revision + ":tools/windows_demo_source_compare_v2/" + n)
    core = files["COMPARE_CORE.ps1"].decode()
    core = core.replace(
        "$stage='INPUTS';$stopped=$false",
        "$stage='INPUTS';$stopped=$false;$script:ReviewedRestoreHandle=$null",
    )
    core = core.replace(
        "(COMPARE|RESTORE|COLD|BACKUP_PLAN)", "(COMPARE|RESTORE|COLD|BACKUP_PLAN|HANDOFF)"
    )
    core = core.replace(
        "} finally {foreach($handle in $handles)",
        "} finally {if($null -ne $script:ReviewedRestoreHandle){$script:ReviewedRestoreHandle.Dispose()};foreach($handle in $handles)",
    )
    if "$script:ReviewedRestoreHandle.Dispose()" not in core:
        raise ValueError("SCOPE_TEMPLATE_CHANGED")
    files["COMPARE_CORE.ps1"] = core.encode()
    runner = files["RUN_COMPARE.ps1"].decode()
    runner = runner.replace(
        "'READ_LIBRARY.py','CONFIG.json')",
        "'READ_LIBRARY.py','CONFIG.json','RESTORE_EVIDENCE.ps1','REVIEWED_SCOPE.ps1')",
    )
    runner = runner.replace(
        "'SOURCE_SCOPE.ps1','COMPARE_CORE.ps1'",
        "'SOURCE_SCOPE.ps1','RESTORE_EVIDENCE.ps1','REVIEWED_SCOPE.ps1','COMPARE_CORE.ps1'",
    )
    files["RUN_COMPARE.ps1"] = runner.encode()
    manifest = (
        json.dumps(dict(source_reference=revision, files=hashes(files)), sort_keys=True, indent=2)
        + "\n"
    ).encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    for name, mode in [("RUN_CHECK.cmd", "CheckOnly"), ("RUN_COMPARE.cmd", "CompareOnce")]:
        files[name] = (
            '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_COMPARE.ps1" -ManifestSHA256 '
            + hashlib.sha256(manifest).hexdigest()
            + " -"
            + mode
            + "\r\nexit /b %errorlevel%\r\n"
        ).encode("ascii")
    return files


def build(ref, output):
    files = package_files(ref)
    output.mkdir(parents=True, exist_ok=True)
    target = output / (FOLDER + ".zip")
    with target.open("xb") as stream:
        stream.write(archive({FOLDER + "/" + n: b for n, b in files.items()}))
    print(
        json.dumps(
            dict(artifact=str(target), sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ref", required=True)
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    build(a.ref, a.output)
