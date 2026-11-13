"""Pinned two-GET probe; project imports resolve to installed /app code only."""

import argparse
import ast
import base64
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.review_demo_read_code_install import INSTALL_FILES


def fingerprint_source(source: str) -> bytes:
    tree = ast.parse(source)
    names = {
        "mark",
        "_budget",
        "_table_snapshot",
        "SNAPSHOT_SECONDS",
        "MAX_TABLE_ROWS",
        "MAX_ROW_BYTES",
    }
    parts = []
    found = set()
    for node in tree.body:
        name = (
            node.name
            if isinstance(node, ast.FunctionDef)
            else node.targets[0].id
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
            else None
        )
        if name in names:
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            parts.append(segment)
            found.add(name)
    assert found == names
    prefix = "import hashlib,re,time\nfrom contextvars import ContextVar\nfrom typing import Any\nfrom sqlalchemy import text\nfrom packages.persistence.demo_read_audit import require\nDIAGNOSTIC=ContextVar('probe_fingerprint',default=None)\n"
    result = (prefix + "\n\n".join(parts) + "\n").encode()
    compile(result, "probe_fingerprint.py", "exec")
    return result


def build(
    ref: str,
    output: Path,
    *,
    recover: bool = False,
    flat_review: bool = False,
    day13: bool = False,
    day13_apply: bool = False,
    post_gate: bool = False,
    historical: bool = False,
) -> Path:
    if sum((recover, flat_review, day13, day13_apply, post_gate, historical)) > 1:
        raise ValueError("Choose one operation")
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    installed = json.loads(
        git("show", f"{revision}:tools/windows-demo-read-review/INSTALLED_SOURCE_20261111.json")
    )
    installed.update(
        {
            name: hashlib.sha256(git("show", f"{revision}:{name}")).hexdigest()
            for name in INSTALL_FILES
        }
    )
    candidate = {
        "scripts/probe_demo_accounted_reads.py": git(
            "show", f"{revision}:scripts/probe_demo_accounted_reads.py"
        ),
        "probe_fingerprint.py": fingerprint_source(
            git("show", f"{revision}:scripts/review_demo_read_code_install.py").decode()
        ),
    }
    if day13_apply or post_gate or historical:
        candidate["scripts/adopt_demo_accounted_day13.py"] = git(
            "show", f"{revision}:scripts/adopt_demo_accounted_day13.py"
        )
    if post_gate or historical:
        candidate["scripts/review_demo_post_adoption.py"] = git(
            "show", f"{revision}:scripts/review_demo_post_adoption.py"
        )
        candidate["COST_EVIDENCE.json"] = git(
            "show", f"{revision}:docs/request_cost_evidence/PINNED_RUNTIME_COST_PROOF.json"
        )
    if historical:
        candidate["scripts/review_demo_historical_requests.py"] = git(
            "show", f"{revision}:scripts/review_demo_historical_requests.py"
        )
    candidate["PROBE_MANIFEST.json"] = json.dumps(
        dict(
            source_reference=revision,
            installed_files=installed,
            runtime_modules=list(INSTALL_FILES),
            candidate_files=hashes(candidate),
        ),
        sort_keys=True,
    ).encode()
    payload = archive(candidate)
    loader = git("show", f"{revision}:scripts/demo_read_candidate_loader.py").decode()
    assert (
        loader.count("sys.path.insert(0, str(root))") == 1
        and loader.count(
            'runpy.run_module("scripts.review_demo_read_deployment", run_name="__main__")'
        )
        == 1
    )
    loader = loader.replace("sys.path.insert(0, str(root))", 'sys.path.insert(0, "/app")').replace(
        'runpy.run_module("scripts.review_demo_read_deployment", run_name="__main__")',
        'runpy.run_path(str(root / "scripts/probe_demo_accounted_reads.py"), run_name="__main__")',
    )
    encoded = base64.b64encode(loader.encode()).decode()
    worker_args = (
        "%2"
        if historical
        else "--post-adoption-gate"
        if post_gate
        else "%2 %3"
        if recover or day13 or day13_apply
        else "--review-flat"
        if flat_review
        else "--run-two-gets"
    )
    runner = (
        "HISTORICAL_REQUESTS"
        if historical
        else "POST_ADOPTION_GATE"
        if post_gate
        else "DAY13_APPLY"
        if day13_apply
        else "DAY13_PREVIEW"
        if day13
        else "RECOVER_AND_PROBE"
        if recover
        else "FLAT_REVIEW"
        if flat_review
        else "PROBE"
    )
    command = (
        '@echo off\r\nsetlocal DisableDelayedExpansion\r\nif "%~1"=="" exit /b 1\r\n'
        f"docker exec -i %1 python -I -c \"exec(__import__('base64').b64decode('{encoded}'))\" {hashlib.sha256(payload).hexdigest()} {worker_args} < \"%~dp0candidate.zip\"\r\n"
        "exit /b %errorlevel%\r\n"
    ).encode("ascii")
    if max(map(len, command.splitlines())) > 8000:
        raise ValueError("Windows command limit exceeded")
    package = {
        "candidate.zip": payload,
        "RUN_WORKER.cmd": command,
        runner + ".ps1": git("show", f"{revision}:tools/windows-demo-accounted-probe/{runner}.ps1"),
        "INSTALL_INSPECTION.ps1": git(
            "show", f"{revision}:tools/windows-demo-read-install/INSTALL_INSPECTION.ps1"
        ),
        "README_KO.md": git(
            "show",
            f"{revision}:"
            + (
                "HISTORICAL_REQUEST_EVIDENCE_KO.md"
                if historical
                else "POST_ADOPTION_LOCAL_GATE_KO.md"
                if post_gate
                else "RUNTIME_ACCOUNTED_DAY13_APPLY_KO.md"
                if day13_apply
                else "RUNTIME_ACCOUNTED_DAY13_PREVIEW_KO.md"
                if day13
                else "RUNTIME_ACCOUNTED_FLAT_REVIEW_KO.md"
                if flat_review
                else "RUNTIME_ACCOUNTED_CLOCK_RECOVERY_KO.md"
                if recover
                else "RUNTIME_ACCOUNTED_READ_PROBE_KO.md"
            ),
        ),
        "SOURCE_REFERENCE.json": json.dumps(
            dict(source_reference=revision, installed_files=installed), sort_keys=True, indent=2
        ).encode(),
    }
    manifest = json.dumps(dict(files=hashes(package)), sort_keys=True, indent=2).encode()
    package["TRANSFER_MANIFEST.json"] = manifest
    package["RUN_" + runner + ".cmd"] = (
        f'@echo off\r\npowershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0{runner}.ps1" '
        f"-ManifestSHA256 {hashlib.sha256(manifest).hexdigest()}\r\nexit /b %errorlevel%\r\n"
    ).encode("ascii")
    if historical:
        package["RUN_HISTORICAL_STATUS.cmd"] = (
            '@echo off\r\npowershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0HISTORICAL_REQUESTS.ps1" -StatusOnly '
            f"-ManifestSHA256 {hashlib.sha256(manifest).hexdigest()}\r\nexit /b %errorlevel%\r\n"
        ).encode("ascii")
    if day13_apply:
        package["RUN_DAY13_STATUS.cmd"] = (
            '@echo off\r\npowershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0DAY13_APPLY.ps1" -StatusOnly '
            f"-ManifestSHA256 {hashlib.sha256(manifest).hexdigest()}\r\nexit /b %errorlevel%\r\n"
        ).encode("ascii")
    folder = (
        "windows-demo-historical-request-evidence-20261113"
        if historical
        else "windows-demo-post-adoption-gate-v2-20261113"
        if post_gate
        else "windows-demo-accounted-day13-apply-v2-20261113"
        if day13_apply
        else "windows-demo-accounted-day13-preview-20261113"
        if day13
        else "windows-demo-accounted-flat-review-20261112"
        if flat_review
        else "windows-demo-accounted-clock-recovery-20261112"
        if recover
        else "windows-demo-accounted-read-probe-20261112"
    )
    output.mkdir(parents=True, exist_ok=True)
    destination = output / (folder + ".zip")
    with destination.open("xb") as stream:
        stream.write(archive({folder + "/" + name: body for name, body in package.items()}))
    print(
        json.dumps(
            dict(
                artifact=str(destination),
                sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
                source=revision,
            )
        )
    )
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--recover-clock", action="store_true")
    parser.add_argument("--flat-review", action="store_true")
    parser.add_argument("--day13-preview", action="store_true")
    parser.add_argument("--day13-apply", action="store_true")
    parser.add_argument("--post-adoption-gate", action="store_true")
    parser.add_argument("--historical-requests", action="store_true")
    args = parser.parse_args()
    build(
        args.ref,
        args.output,
        recover=args.recover_clock,
        flat_review=args.flat_review,
        day13=args.day13_preview,
        day13_apply=args.day13_apply,
        post_gate=args.post_adoption_gate,
        historical=args.historical_requests,
    )
