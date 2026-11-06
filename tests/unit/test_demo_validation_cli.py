"""Actual diagnostic CLI rejects unsafe flag combinations before authentication."""

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "flags",
    [
        [],
        ["--enable-demo"],
        ["--operation-id", "00000000-0000-0000-0000-000000000001"],
        ["--enable-demo", "--operation-id", "00000000-0000-0000-0000-000000000001", "--watch"],
        ["--enable-demo", "--operation-id", "00000000-0000-0000-0000-000000000001", "--initialize"],
    ],
)
def test_minimum_validator_requires_explicit_enable_and_stable_operation_only(flags):
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
            "DEMO-no-auth",
            "--validate-minimum",
            "LONG",
            *flags,
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2 and "--validate-minimum requires" in result.stderr
    assert "Traceback" not in result.stderr
