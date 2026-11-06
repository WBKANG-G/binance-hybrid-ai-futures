"""Preview execution flags are rejected before credentials or database access."""

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "extra",
    [
        ["--enable-demo"],
        ["--watch"],
        ["--initialize"],
        ["--check-private"],
        ["--signal", "not-read.json"],
    ],
)
def test_preview_cannot_be_combined_with_execution_or_initialization(extra):
    environment = os.environ.copy()
    environment["LIVE_TRADING_ENABLED"] = "false"
    environment["TRADING_MODE"] = "PAPER"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.run_demo",
            "--config",
            "configs/demo-isolated.example.yaml",
            "--account",
            "DEMO-no-access",
            "--preview-entry",
            *extra,
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "--preview-entry requires writes disabled" in result.stderr
    assert "Traceback" not in result.stderr
