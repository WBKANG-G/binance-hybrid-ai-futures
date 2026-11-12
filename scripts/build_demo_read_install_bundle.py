"""Pinned six-module image installation bundle; existing runtime files stay intact."""

import argparse
import base64
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import BASELINE, archive, git, hashes, source
from scripts.review_demo_read_code_install import INSTALL_FILES


def build(ref: str, output: Path, *, diagnostic: bool = False) -> Path:
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    files = source(revision)
    installed = json.loads(
        git("show", f"{revision}:tools/windows-demo-read-review/INSTALLED_SOURCE_20261111.json")
    )
    targets = {name: files[name] for name in INSTALL_FILES}
    assert not (set(targets) & set(installed))
    files["CANDIDATE_MANIFEST.json"] = json.dumps(
        dict(
            candidate_reference=revision,
            baseline_reference=BASELINE,
            operation="review",
            candidate_files=hashes(files),
            installed_files=installed,
            install_files=hashes(targets),
        ),
        sort_keys=True,
    ).encode()
    payload = archive(files)
    original = git("show", f"{revision}:scripts/demo_read_candidate_loader.py").decode()
    assert original.count('"scripts.review_demo_read_deployment"') == 1
    loader = base64.b64encode(
        original.replace(
            '"scripts.review_demo_read_deployment"', '"scripts.review_demo_read_code_install"'
        ).encode()
    ).decode()
    command = (
        '@echo off\r\nsetlocal DisableDelayedExpansion\r\nif "%~1"=="" exit /b 1\r\n'
        f"docker exec -i %1 python -I -c \"exec(__import__('base64').b64decode('{loader}'))\" {hashlib.sha256(payload).hexdigest()} %2 %3 < \"%~dp0candidate.zip\"\r\n"
        "exit /b %errorlevel%\r\n"
    ).encode("ascii")
    if max(map(len, command.splitlines())) > 8000:
        raise ValueError("Windows command limit exceeded")
    package = {
        "candidate.zip": payload,
        "RUN_GUARD.cmd": command,
        "Dockerfile": b"ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\nCOPY --chown=10001:10001 payload/ /app/\n",
        "SOURCE_REFERENCE.json": json.dumps(
            dict(
                candidate_reference=revision,
                baseline_reference=BASELINE,
                install_files=hashes(targets),
            ),
            sort_keys=True,
            indent=2,
        ).encode(),
        "README_KO.md": git(
            "show",
            f"{revision}:"
            + (
                "RUNTIME_READ_INSTALL_DIAGNOSTIC_KO.md"
                if diagnostic
                else "RUNTIME_READ_CODE_INSTALL_KO.md"
            ),
        ),
    }
    for name in ("INSTALL.ps1", "INSTALL_INSPECTION.ps1", "DOCKER_RECOVERY.ps1"):
        package[name] = git("show", f"{revision}:tools/windows-demo-read-install/{name}")
    package.update({"payload/" + name: body for name, body in targets.items()})
    package[".dockerignore"] = b"*\n!Dockerfile\n!payload/\n!payload/**\n"
    if diagnostic:
        package = {
            n: b
            for n, b in package.items()
            if not n.startswith("payload/")
            and n not in {"Dockerfile", ".dockerignore", "INSTALL.ps1", "DOCKER_RECOVERY.ps1"}
        }
        package["DIAGNOSE.ps1"] = git(
            "show", f"{revision}:tools/windows-demo-read-install/DIAGNOSE.ps1"
        )
    manifest = json.dumps(dict(files=hashes(package)), sort_keys=True, indent=2).encode()
    package["TRANSFER_MANIFEST.json"] = manifest
    package["RUN_DIAGNOSE.cmd" if diagnostic else "RUN_INSTALL.cmd"] = (
        '@echo off\r\npowershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0'
        + ("DIAGNOSE.ps1" if diagnostic else "INSTALL.ps1")
        + '" '
        f"-ManifestSHA256 {hashlib.sha256(manifest).hexdigest()}\r\nexit /b %errorlevel%\r\n"
    ).encode("ascii")
    folder = (
        "windows-demo-read-install-diagnostic-20261112"
        if diagnostic
        else "windows-demo-read-code-install-v2-20261112"
    )
    output.mkdir(parents=True, exist_ok=True)
    destination = output / (folder + ".zip")
    with destination.open("xb") as stream:
        stream.write(archive({folder + "/" + n: b for n, b in package.items()}))
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
    parser.add_argument("--diagnostic", action="store_true")
    args = parser.parse_args()
    build(args.ref, args.output, diagnostic=args.diagnostic)
