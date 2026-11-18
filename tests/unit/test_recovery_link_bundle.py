"""Offline report linkage preserves provenance limits and never adopts a baseline."""

import hashlib
import json
import os
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_recovery_link_bundle as builder
from tests.unit import test_recovery_preview_bundle as previous
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch
from tests.unit.test_offline_handoff import synthetic_inputs
from tests.unit.test_private_receipt_review_v2 import snapshot

pytestmark = pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")


def package():
    release = os.getenv("RECOVERY_LINK_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git
    ref = real("rev-parse", "HEAD").decode().strip()

    def read(*args):
        if args[0] == "show" and args[1].startswith(ref + ":tools/windows_demo_recovery_link/"):
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with patch.object(builder, "git", read):
        return builder.package_files("HEAD")


def setup(tmp_path, fault=""):
    root, pin = previous.setup(tmp_path)
    separate = tmp_path / "restoration-fixture"
    separate.mkdir()
    _, proof = synthetic_inputs(separate)
    record = json.loads(proof.read_bytes())
    config = json.loads(package()["CONFIG.json"])
    config["manifest_sha256"] = pin
    record["source_reference"] = config["restore_source_reference"]
    record["backup_manifest_sha256"] = pin
    record["backup_receipt_sha256"] = config["receipt_sha256"]
    if fault == "readonly":
        record["account"]["transaction_read_only"] = "off"
    if fault == "orders":
        record["account"]["orders"] = 33
    if fault == "date":
        record["account"]["daily_key"] = "2026-11-12"
    if fault == "backup-link":
        record["backup_manifest_sha256"] = "0" * 64
    if fault == "source":
        record["source_reference"] = "0" * 40
    if fault == "paused":
        record["account"]["paused"] = False
    folder = root / ("cold-20261114-" + "f" * 32)
    folder.mkdir()
    raw = json.dumps(record).encode()
    (folder / "ISOLATED_RESTORE_RECEIPT.json").write_bytes(raw)
    config["restore_receipt_sha256"] = hashlib.sha256(raw).hexdigest()
    if fault == "hash":
        config["restore_receipt_sha256"] = "0" * 64
    if fault == "duplicate":
        other = root / ("cold-20261114-" + "e" * 32)
        other.mkdir()
        (other / "ISOLATED_RESTORE_RECEIPT.json").write_bytes(raw)
    files = package()
    files["CONFIG.json"] = json.dumps(config).encode()
    files["PRIVATE_READ.ps1"] = (
        b"function Reference-AssertPrivateDirectory {param($Path) if(-not [IO.Directory]::Exists($Path)){throw 'REFERENCE_TEST_DIRECTORY_MISSING'}}"
    )
    m = json.loads(files["TRANSFER_MANIFEST.json"])
    for n in ("CONFIG.json", "PRIVATE_READ.ps1"):
        m["files"][n] = hashlib.sha256(files[n]).hexdigest()
    files["TRANSFER_MANIFEST.json"] = json.dumps(m).encode()
    for n, b in files.items():
        (tmp_path / n).write_bytes(b)
    return hashlib.sha256(files["TRANSFER_MANIFEST.json"]).hexdigest()


def run(tmp_path, pin):
    value = launch(
        tmp_path,
        f"function tar.exe {{& tar @args}}\n& '{tmp_path}/RUN_LINK.ps1' -ManifestSHA256 '{pin}'\nexit $LASTEXITCODE",
    )
    assert not value.stderr, value.stdout + value.stderr
    for raw in ("PRIVATE_CLIENT_SENTINEL", "SENSITIVE_RAW_STATE", "PRIVATE_SECRET"):
        assert raw not in value.stdout
    result = json.loads(next(line for line in value.stdout.splitlines() if line.startswith("{")))
    return value, result


def test_full_sealed_link_preserves_values_and_distinguishes_sql_report_from_financial_projection(
    tmp_path,
):
    pin = setup(tmp_path)
    before = snapshot(tmp_path / "data")
    value, result = run(tmp_path, pin)
    assert value.returncode == 0, result
    assert result["restored_sql_report"]["daily_key"] == "2026-11-13"
    assert result["conditional_changes"][0]["before"] == "2026-11-13"
    assert result["conditional_changes"][0]["candidate"] == "2026-11-18"
    assert result["conditional_changes"][2]["candidate"] == "4998.74183608"
    assert result["preserve"]["high_water_usdt"] == "5000.00000000"
    assert result["preserve"]["unresolved_requests"] == 4
    for flag in (
        "full_prospective_ledger_diff_verified",
        "financial_fields_directly_read_from_restored_sql_report",
        "current_source_sql_queried",
        "physical_source_comparison_independently_rerun",
        "baseline_adopted",
        "apply_token_created",
        "request_states_changed",
        "files_written",
        "credentials_accessed",
        "docker_used",
        "orders_enabled",
        "live_trading_enabled",
    ):
        assert result[flag] is False
    assert result["actual_http_attempts"] == result["order_writes"] == 0
    assert before == snapshot(tmp_path / "data")
    assert "RECOVERY_LINK_COMPLETE_CONDITIONAL_DIFF_NO_APPLY_NO_HTTP_NO_WRITES" in value.stdout


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("readonly", "HANDOFF_RESTORE_RECEIPT_INVALID"),
        ("orders", "HANDOFF_RESTORE_RECEIPT_INVALID"),
        ("date", "HANDOFF_RESTORE_RECEIPT_INVALID"),
        ("backup-link", "HANDOFF_RESTORE_RECEIPT_INVALID"),
        ("source", "HANDOFF_RESTORE_RECEIPT_INVALID"),
        ("paused", "HANDOFF_RESTORE_RECEIPT_INVALID"),
        ("hash", "LINK_RESTORE_RECEIPT_NOT_UNIQUE"),
        ("duplicate", "LINK_RESTORE_RECEIPT_NOT_UNIQUE"),
    ],
)
def test_invalid_receipt_or_ambiguous_report_cannot_be_used(tmp_path, fault, expected):
    pin = setup(tmp_path, fault)
    before = snapshot(tmp_path / "data")
    value, result = run(tmp_path, pin)
    assert value.returncode == 2 and result["failure_category"] == expected, result
    assert before == snapshot(tmp_path / "data")


def test_no_docker_transport_writer_or_apply_code_and_seal_blocks_tampering(tmp_path):
    files = package()
    text = b"\n".join(b for n, b in files.items() if n.endswith(".ps1"))
    for forbidden in (
        b"Export-Http",
        b"Export-Credentials",
        b"WebRequest",
        b"FileAccess]::Write",
        b"FileMode]::Create",
        b"docker ",
        b"apply_paused_periods",
    ):
        assert forbidden not in text
    for n, b in files.items():
        (tmp_path / n).write_bytes(b)
    pin = hashlib.sha256(files["TRANSFER_MANIFEST.json"]).hexdigest()
    (tmp_path / "LINK_CORE.ps1").write_text("PRIVATE_TAMPER_SENTINEL")
    value, result = run(tmp_path, pin)
    assert value.returncode == 2 and result["failure_category"] == "LINK_TRANSFER_INVALID"
