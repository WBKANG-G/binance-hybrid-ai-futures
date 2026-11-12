"""Build a pinned, candidate-only Windows review bundle from committed Git sources."""

import argparse
import base64
import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path

BASELINE = "e03b6ba683732cd5872c30cf0d245a5b1fb6d325"
PREFIXES = ("packages/", "services/", "integrations/", "scripts/", "apps/", "backtest/")


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args])


def source(ref: str) -> dict[str, bytes]:
    names = git("ls-tree", "-r", "--name-only", ref).decode().splitlines()
    selected = [
        n
        for n in names
        if (n.startswith(PREFIXES) and n.endswith(".py"))
        or (n.startswith("configs/") and n.endswith((".yaml", ".yml")))
        or n == "pyproject.toml"
    ]
    return {name: git("show", f"{ref}:{name}") for name in selected}


def hashes(files: dict[str, bytes]) -> dict[str, str]:
    return {name: hashlib.sha256(body).hexdigest() for name, body in sorted(files.items())}


def archive(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, body in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 11, 12, 0, 0, 0))
            info.external_attr = 0o100600 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(info, body)
    return out.getvalue()


def build(ref: str, output: Path) -> Path:
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    files = source(revision)
    installed = json.loads(
        git("show", f"{revision}:tools/windows-demo-read-review/INSTALLED_SOURCE_20261111.json")
    )
    files["CANDIDATE_MANIFEST.json"] = json.dumps(
        dict(
            candidate_reference=revision,
            baseline_reference=BASELINE,
            candidate_files=hashes(files),
            installed_files=installed,
        ),
        sort_keys=True,
    ).encode()
    payload = archive(files)
    loader = base64.b64encode(
        git("show", f"{revision}:scripts/demo_read_candidate_loader.py")
    ).decode()
    payload_hash = hashlib.sha256(payload).hexdigest()
    candidate_cmd = (
        '@echo off\r\nsetlocal DisableDelayedExpansion\r\nif "%~1"=="" exit /b 1\r\n'
        f"docker exec -i %1 python -I -c \"exec(__import__('base64').b64decode('{loader}'))\" {payload_hash} < \"%~dp0candidate.zip\"\r\n"
        "exit /b %errorlevel%\r\n"
    ).encode("ascii")
    if max(map(len, candidate_cmd.splitlines())) > 8000:
        raise ValueError("Windows command limit exceeded")
    package = {
        "candidate.zip": payload,
        "RUN_CANDIDATE.cmd": candidate_cmd,
        "REVIEW.ps1": git("show", f"{revision}:tools/windows-demo-read-review/REVIEW.ps1"),
        "SOURCE_REFERENCE.json": json.dumps(
            dict(
                candidate_reference=revision,
                baseline_reference=BASELINE,
                candidate_archive_sha256=payload_hash,
            ),
            sort_keys=True,
            indent=2,
        ).encode(),
        "README_KO.md": git("show", f"{revision}:RUNTIME_READ_DEPLOYMENT_KO.md"),
    }
    manifest = json.dumps(dict(files=hashes(package)), sort_keys=True, indent=2).encode()
    package["TRANSFER_MANIFEST.json"] = manifest
    package["RUN_REVIEW.cmd"] = (
        "@echo off\r\n"
        'powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0REVIEW.ps1" '
        f"-ManifestSHA256 {hashlib.sha256(manifest).hexdigest()}\r\n"
        "exit /b %errorlevel%\r\n"
    ).encode("ascii")
    destination = output / "windows-demo-read-deployment-review-20261112.zip"
    output.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as stream:
        stream.write(
            archive(
                {"windows-demo-read-deployment-review-20261112/" + n: b for n, b in package.items()}
            )
        )
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
    args = parser.parse_args()
    build(args.ref, args.output)
