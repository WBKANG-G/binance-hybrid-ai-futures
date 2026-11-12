"""PowerShell runner tested with controlled Docker metadata; no daemon mutation."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PWSH = shutil.which("pwsh") or "/tmp/runtime-installer-pwsh/opt/microsoft/powershell/7/pwsh"
pytestmark = pytest.mark.skipif(not Path(PWSH).exists(), reason="PowerShell required")
SOURCE = Path("tools/windows-demo-read-review/REVIEW.ps1").read_bytes()


def execute(tmp_path, fault=None):
    root = tmp_path / "package with spaces"
    root.mkdir()
    (root / "REVIEW.ps1").write_bytes(SOURCE)
    candidate = root / "RUN_CANDIDATE.cmd"
    candidate.write_text(
        '#!/bin/sh\ntest "$1" = "'
        + "a" * 64
        + '" || exit 9\necho CANDIDATE_TEST_OK\nexit '
        + ("1" if fault == "candidate_failed" else "0")
        + "\n"
    )
    candidate.chmod(0o700)
    if fault == "startup":
        status = json.dumps(
            dict(
                read_deployment_review="WAIT_STARTUP_HOLD",
                orders_enabled=False,
                live_trading_enabled=False,
                execution_approved=False,
            )
        )
        candidate.write_text(
            "#!/bin/sh\nif [ ! -f '"
            + str(root / "called")
            + "' ]; then\ntouch '"
            + str(root / "called")
            + "'\necho '"
            + status
            + "'\nexit 2\nfi\necho CANDIDATE_TEST_OK\nexit 0\n"
        )
    body = json.dumps(
        dict(
            files={
                n: hashlib.sha256((root / n).read_bytes()).hexdigest()
                for n in ["REVIEW.ps1", "RUN_CANDIDATE.cmd"]
            }
        )
    ).encode()
    (root / "TRANSFER_MANIFEST.json").write_bytes(body)
    expected = hashlib.sha256(body).hexdigest()
    details = dict(
        Id="a" * 64,
        Name="/hybrid-windows-api-1",
        Config=dict(
            WorkingDir="/app",
            User="10001",
            Env=["PRIVATE_KEY=PRIVATE_VALUE"],
            Labels={
                "com.docker.compose.project": "hybrid-windows",
                "com.docker.compose.service": "api",
            },
        ),
        State=dict(Running=True, Health=dict(Status="healthy")),
    )
    if fault == "identity":
        details["Name"] = "/wrong"
    elif fault == "unhealthy":
        details["State"]["Health"]["Status"] = "starting"
    elif fault == "user":
        details["Config"]["User"] = "root"
    elif fault == "tampered":
        candidate.write_text("PRIVATE_TAMPERED")
    elif fault == "manifest":
        expected = "0" * 64
    metadata = tmp_path / "inspect.json"
    metadata.write_text(json.dumps([details]))
    wrapper = tmp_path / "wrapper.ps1"
    wrapper.write_text(
        "function global:docker { $global:LASTEXITCODE="
        + ("1" if fault == "docker" else "0")
        + "; Get-Content -Raw -LiteralPath '"
        + str(metadata)
        + "' }\n& '"
        + str(root / "REVIEW.ps1")
        + "' -ManifestSHA256 "
        + expected
        + (" -WaitStartupHold" if fault == "startup" else "")
        + "\nexit $LASTEXITCODE\n"
    )
    return subprocess.run(
        [PWSH, "-NoProfile", "-File", str(wrapper)],
        capture_output=True,
        text=True,
        timeout=20,
        env=dict(
            os.environ,
            XDG_CACHE_HOME=str(tmp_path / "cache"),
            XDG_CONFIG_HOME=str(tmp_path / "config"),
            XDG_DATA_HOME=str(tmp_path / "data"),
        ),
    )


def test_runner_validates_metadata_and_passes_fixed_container_id(tmp_path):
    result = execute(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CANDIDATE_PLAN_VERIFIED_NO_API_REPLACEMENT_NO_AUDIT_APPLY" in result.stdout
    assert "PRIVATE" not in result.stdout + result.stderr


def test_runner_continues_only_expected_startup_hold(tmp_path):
    result = execute(tmp_path, "startup")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "STARTUP_HOLD_KEEP_PAUSED_NO_ORDERS" in result.stdout
    assert "UNUSED_CLOCK_RECOVERED_CANDIDATE_PLAN_VERIFIED_KEEP_PAUSED" in result.stdout


@pytest.mark.parametrize(
    "fault", ["identity", "unhealthy", "user", "tampered", "manifest", "docker", "candidate_failed"]
)
def test_runner_blocks_without_exposing_metadata_or_retrying(tmp_path, fault):
    result = execute(tmp_path, fault)
    assert result.returncode == 1
    assert '"api_replaced":false' in result.stdout
    assert "PRIVATE" not in result.stdout + result.stderr
    if fault != "candidate_failed":
        assert "CANDIDATE_TEST_OK" not in result.stdout
