"""An opposite-quote option does not enable general execution implicitly."""

import os
import subprocess
import sys


def test_opposite_style_requires_preview_or_explicit_diagnostic():
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
            "--entry-style",
            "OPPOSITE_QUOTE",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2 and "--entry-style requires" in result.stderr
    assert "Traceback" not in result.stderr
