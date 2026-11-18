"""Actual COMMIT and rollback rehearsal exclusively on a freshly owned backup copy."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_adoption_model_bundle import package_files as model_files
from scripts.build_windows_copy_transition_bundle import once

FOLDER = "windows-demo-copy-commit-20261118"
PIN = "3d938f6ca404e4f34455ab728f1966ee0d103bb1"
DRAFT_PIN = "effbee73c2e1f7ab2cbedac8cd597ef1c17fec1a"


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    files = {
        n: b
        for n, b in model_files(PIN).items()
        if not n.endswith(".cmd") and n != "TRANSFER_MANIFEST.json"
    }
    files["commit_model.py"] = (
        git("show", DRAFT_PIN + ":services/execution/demo_period_draft_model_review.py")
        + b"\n"
        + git("show", revision + ":services/execution/demo_period_adoption_model.py")
    )
    for name, path in [
        ("copy_commit_worker.py", "tools/windows_demo_copy_commit/worker.py"),
        ("README_KO.md", "tools/windows_demo_copy_commit/README_KO.md"),
    ]:
        files[name] = git("show", revision + ":" + path)
    prefix = files["MODEL_EXT.ps1"].decode().split(" $d=$Account.transition_draft\n", 1)[0]
    prefix = prefix.replace("$script:TypedReview=$null", "$script:CopyCommitReview=$null").replace(
        "Invoke-TypedDraftReview", "Invoke-CopyCommitReview"
    )
    prefix = once(
        prefix,
        "[object]$Account) {",
        "[object]$Account,[string]$Clone,[string]$CloneVolume) {\n"
        " if($Owned -cnotcontains $Clone -or $Socket -cne $script:CopySocket){throw 'MODEL_COPY_CLONE_BINDING_INVALID'}\n"
        " Assert-NewClone $Clone $CloneVolume $Original\n"
        " $db=Read-Container $Clone\n"
        " if(-not $db.running -or -not (Resolve-RestoreImageMatch $db.image $State.canonical_images[1])){throw 'MODEL_COPY_CLONE_BINDING_INVALID'}\n"
        " foreach($old in $Original){foreach($m in @($old.mounts)){if($m.Type -ceq 'volume' -and @($CloneVolume,$Socket) -ccontains $m.Name){throw 'MODEL_COPY_ORIGINAL_VOLUME_DENIED'}}}\n",
    )
    prefix = prefix.replace("typed-draft-review", "copy-commit-review").replace(
        "ADOPTION_MODEL_RESOURCE.json", "COPY_COMMIT_RESOURCE.json"
    )
    files["COMMIT_EXT.ps1"] = prefix.encode() + git(
        "show", revision + ":tools/windows_demo_copy_commit/COMMIT_BODY.ps1"
    )
    core = files["COPY_CORE.ps1"].decode()
    core = once(
        core,
        "        $stage='STOP_COPY_AND_REVERIFY_INPUTS';",
        "        $stage='COPY_ONLY_ATOMIC_TRANSACTION_REHEARSAL'; Write-Output ('COPY_COMMIT_STAGE='+$stage)\n"
        "        $helpers+=,(Invoke-CopyCommitReview $Root $config $state $volumes[2] $original $helpers $directory $account $clone $volumes[0])\n"
        "        $stage='STOP_COPY_AND_REVERIFY_INPUTS';",
    )
    core = core.replace(
        "typed_model_review=$script:TypedReview;",
        "copy_commit_review=$script:CopyCommitReview;operation_scope='FRESH_DISPOSABLE_COPY_ONLY';copy_database_changed=$true;original_database_changed=$false;original_source_accepted=$false;account_projection_scope='BEFORE_COPY_TRANSACTION';typed_model_review=$script:TypedReview;",
    )
    core = core.replace(
        "financial_write_attempted=$false",
        "copy_financial_write_attempted=$true;original_financial_write_attempted=$false",
    )
    core = core.replace(
        "stored_day_preserved='2026-11-13'",
        "source_stored_day='2026-11-13';copy_stored_day='2026-11-18'",
    )
    core = core.replace("adoption_model_rehearsal=", "copy_commit_rehearsal=").replace(
        "COMPLETION_MODEL_COPY_REHEARSAL_REVIEWED_ORIGINALS_UNCHANGED",
        "COPY_ONLY_ATOMIC_COMMIT_REVIEWED_ORIGINALS_UNCHANGED",
    )
    core = core.replace("ADOPTION_MODEL_RECEIPT.json", "COPY_COMMIT_RECEIPT.json").replace(
        "ADOPTION_MODEL_CHECK_PASSED", "COPY_COMMIT_CHECK_PASSED"
    )
    core = core.replace(
        "ADOPTION_MODEL_REHEARSAL_COMPLETE_COPY_STOPPED_NO_ADOPTION_NO_ORDERS",
        "COPY_COMMIT_REHEARSAL_COMPLETE_COPY_STOPPED_ORIGINALS_UNCHANGED_NO_ORDERS",
    )
    core = core.replace(
        "REVIEW_COMPLETION_MODEL_NO_SOURCE_ACCEPTANCE_NO_COMMIT_KEEP_STOPPED",
        "REVIEW_COPY_COMMIT_NO_ORIGINAL_ADOPTION_KEEP_STOPPED",
    )
    files["COPY_CORE.ps1"] = core.encode()
    files["COMMON.ps1"] = files["COMMON.ps1"].replace(
        b"adoption-model-20261118-", b"copy-commit-20261118-"
    )
    config = json.loads(files["CONFIG.json"])
    config.update(
        source_reference=revision,
        commit_candidate_sha256=hashlib.sha256(files["commit_model.py"]).hexdigest(),
        read_worker_sha256=hashlib.sha256(files["typed_worker.py"]).hexdigest(),
    )
    files["CONFIG.json"] = (json.dumps(config, sort_keys=True) + "\n").encode()
    lines = files["RUN_FINANCIAL.ps1"].decode().splitlines()
    required = next(i for i, s in enumerate(lines) if "$required=@(" in s)
    lines[required] = " $required=@(" + ",".join("'" + n + "'" for n in files) + ")"
    load = next(i for i, s in enumerate(lines) if "foreach($name in @(" in s)
    lines[load] = lines[load].replace("'MODEL_EXT.ps1')", "'MODEL_EXT.ps1','COMMIT_EXT.ps1')")
    files["RUN_FINANCIAL.ps1"] = ("\n".join(lines) + "\n").encode()
    manifest = (
        json.dumps(dict(source_reference=revision, files=hashes(files)), sort_keys=True, indent=2)
        + "\n"
    ).encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    for name, mode in [("RUN_CHECK.cmd", "CheckOnly"), ("RUN_REHEARSE_COMMIT.cmd", "ExecuteRead")]:
        files[name] = (
            '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_FINANCIAL.ps1" -ManifestSHA256 '
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
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    build(a.ref, a.output)
