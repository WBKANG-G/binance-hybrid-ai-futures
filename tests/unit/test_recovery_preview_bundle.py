"""Offline proposal checks with saved synthetic evidence; no exchange or DB access."""

import hashlib
import io
import json
import os
import tarfile
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_recovery_preview_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_current_read_bundle import run as current_run
from tests.unit.test_current_read_bundle import setup as current_setup
from tests.unit.test_evidence_receipt_review import launch
from tests.unit.test_private_receipt_review_v2 import snapshot

pytestmark = pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def package():
    release = os.getenv("RECOVERY_PREVIEW_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git
    ref = real("rev-parse", "HEAD").decode().strip()

    def read(*args):
        if args[0] == "show" and args[1].startswith(ref + ":tools/windows_demo_recovery_preview/"):
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with patch.object(builder, "git", read):
        return builder.package_files("HEAD")


def setup(tmp_path):
    root, _, pin = current_setup(tmp_path)
    value = current_run(tmp_path, root, pin)
    assert value["result"]["summary"]["observations_consistent_with_expected_flat_profile"]
    # Enrich the test-only backup with the already-tested baseline reference records.
    # Reseal all local intents against this new synthetic manifest before preview.
    from tests.unit.test_baseline_reference_bundle import records

    baseline, complete, commit = records()
    folder = next(root.glob("cold-*"))
    archive = folder / "runtime.tar"
    with tarfile.open(archive) as tar:
        bodies = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}
    name = next(n for n in bodies if "/accounted-day13-adoption-" in n)
    adoption = json.loads(bodies[name])
    adoption["old_row"].update(fence=58)
    for key, val in baseline["old_row"]["state"].items():
        if key not in {"orders", "requests", "algo_server_ids", "mode"}:
            adoption["old_row"]["state"][key] = val
    bodies[name] = json.dumps(adoption).encode()
    prefix = name.removesuffix("intent.json")
    bodies[prefix + "complete.json"] = json.dumps(complete).encode()
    bodies[prefix + "commit-intent.json"] = json.dumps(commit).encode()
    with tarfile.open(archive, "w") as tar:
        for name, raw in bodies.items():
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            tar.addfile(member, io.BytesIO(raw))
    manifest = {
        "groups": {
            "runtime": {
                "archive_sha256": digest(archive.read_bytes()),
                "archive_bytes": archive.stat().st_size,
                "entries": [{"path": n, "kind": "file", "size": len(b)} for n, b in bodies.items()],
            }
        }
    }
    raw = json.dumps(manifest).encode()
    (folder / "manifest.json").write_bytes(raw)
    pin = digest(raw)
    for file in (tmp_path / "data").glob("WBKANG-DEMO-*Reads/*/INTENT.json"):
        intent = json.loads(file.read_text())
        intent["manifest_sha256"] = pin
        file.write_text(json.dumps(intent))
    for name, raw in package().items():
        (tmp_path / name).write_bytes(raw)
    return root, pin


def run(tmp_path, root, pin):
    # Linux lacks Windows ACLs; only the positive ACL check is substituted.
    # Production still requires protected per-user/SYSTEM DACLs and no reparse points.
    script = f"""
foreach($name in @('READ_CORE.ps1','ARRAY_CORE.ps1','PRIVATE_READ.ps1','PLAN_CORE.ps1','BASELINE_CORE.ps1','PURE_HELPERS.ps1','SAVED_INCOME_CORE.ps1','SAVED_TRADE_CORE.ps1','PREVIEW_CORE.ps1')){{. (Join-Path '{tmp_path}' $name)}}
function Reference-AssertPrivateDirectory {{param($Path) if(-not [IO.Directory]::Exists($Path)){{throw 'REFERENCE_TEST_DIRECTORY_MISSING'}}}}
function tar.exe {{& tar @args}}
$source=$null
try{{
 $plan=Invoke-IncomePlan '{root}' '{pin}'
 $baseline=Invoke-BaselineReferenceInspection '{root}' '{pin}'
 $source=Get-SavedIncome $plan '{tmp_path}/data/WBKANG-DEMO-IncomeReads'
 Add-SavedTrade $source $plan '{tmp_path}/data/WBKANG-DEMO-TradeReads'
 $current=Get-SavedCurrent $source $plan '{tmp_path}/data/WBKANG-DEMO-CurrentReads'
 New-RecoveryPreview $source $plan $current $baseline|ConvertTo-Json -Depth 10 -Compress
}} catch {{[pscustomobject]@{{blocked=$_.Exception.Message}}|ConvertTo-Json -Compress}} finally {{if($null -ne $source){{Trades-CloseSaved $source}}}}
"""
    value = launch(tmp_path, script)
    assert value.returncode == 0 and not value.stderr, value.stdout + value.stderr
    for text in (
        "PRIVATE_CLIENT_SENTINEL",
        "SENSITIVE_RAW_STATE",
        "synthetic-api-key",
        "synthetic-secret",
    ):
        assert text not in value.stdout
    return json.loads(value.stdout)


def rewrite_current(tmp_path, name, mutate, reseal=True):
    folder = next((tmp_path / "data/WBKANG-DEMO-CurrentReads").glob("current-read-*"))
    file = folder / name
    obj = json.loads(file.read_text())
    mutate(obj)
    file.write_text(json.dumps(obj))
    if reseal and name not in {"RECEIPT.json", "INTENT.json"}:
        path = folder / "RECEIPT.json"
        receipt = json.loads(path.read_text())
        for entry in receipt["raw_evidence"]:
            if entry["file"] == name:
                entry["sha256"] = digest(file.read_bytes())
        path.write_text(json.dumps(receipt))


def test_success_proposal_preserves_history_and_all_source_files(tmp_path):
    root, pin = setup(tmp_path)
    before = snapshot(tmp_path / "data")
    value = run(tmp_path, root, pin)
    assert (
        value.get("recovery_preview") == "OFFLINE_EVIDENCE_LINKED_CONDITIONAL_PROPOSAL_KEEP_STOPPED"
    ), value
    assert value["candidate"]["daily_base_usdt"] == "4998.74183608"
    assert value["candidate"]["week_kst"] == "2026-11-16"
    assert value["preserve"]["high_water_usdt"] == "5000.00000000"
    assert value["preserve"]["value_components_usdt"]["realized"] == "-0.80000000"
    assert value["preserve"]["value_components_usdt"]["fees"] == "0.45816392"
    assert value["preserve"]["unresolved_requests"] == 4
    assert sorted(r["request_state"] for r in value["preserve"]["requests"]) == [
        "SUBMITTING",
        "UNKNOWN",
        "UNKNOWN",
        "UNKNOWN",
    ]
    assert value["current_raw_files_verified"] == 14
    assert "LEGACY_APPLY_ANCHOR_EXCEEDS_7_DAYS" in value["blockers"]
    for flag in (
        "files_written",
        "apply_token_created",
        "current_database_state_verified",
        "baseline_adopted",
        "request_states_changed",
        "credentials_accessed",
        "services_started",
        "orders_enabled",
        "live_trading_enabled",
    ):
        assert value[flag] is False
    assert value["actual_http_attempts"] == value["order_writes"] == 0
    assert before == snapshot(tmp_path / "data")


@pytest.mark.parametrize(
    "name,mutate,code",
    [
        ("MODE_AFTER.json", lambda x: x.update(dualSidePosition=True), "PREVIEW_MODE_INVALID"),
        ("BTC_CONFIG_BEFORE.json", lambda x: x[0].update(leverage=3), "PREVIEW_BTC_CONFIG_INVALID"),
        (
            "REGULAR_OPEN_1.json",
            lambda x: x.append(dict(symbol="ETHUSDT", clientOrderId="hidden", status="NEW")),
            "PREVIEW_CURRENT_EXPOSURE_OR_WALLET_INVALID",
        ),
        (
            "ALGO_OPEN_2.json",
            lambda x: x.append(dict(symbol="BTCUSDT", clientAlgoId="hidden", algoStatus="NEW")),
            "PREVIEW_CURRENT_EXPOSURE_OR_WALLET_INVALID",
        ),
        (
            "POSITIONS_1.json",
            lambda x: x[0].update(positionAmt="0.0007"),
            "PREVIEW_CURRENT_EXPOSURE_OR_WALLET_INVALID",
        ),
        (
            "ACCOUNT_2.json",
            lambda x: x.update(totalInitialMargin="1.0"),
            "PREVIEW_CURRENT_EXPOSURE_OR_WALLET_INVALID",
        ),
        ("INCOME_GAP.json", lambda x: x.append(dict(income="0")), "PREVIEW_ACTIVITY_GAP_NOT_EMPTY"),
        (
            "BTC_TRADES_GAP.json",
            lambda x: x.append(dict(qty="0")),
            "PREVIEW_ACTIVITY_GAP_NOT_EMPTY",
        ),
        (
            "INTENT.json",
            lambda x: x.update(saved_income_receipt_sha256="0" * 64),
            "PREVIEW_INTENT_LINK_INVALID",
        ),
        (
            "RECEIPT.json",
            lambda x: x.update(baseline_adopted=True),
            "PREVIEW_RECORDED_STATE_INVALID",
        ),
        (
            "RECEIPT.json",
            lambda x: x.update(observations_end_ms=1795050000000),
            "PREVIEW_WINDOW_INVALID",
        ),
        (
            "RECEIPT.json",
            lambda x: x["raw_evidence"][0].update(file="../outside.json"),
            "PREVIEW_EVIDENCE_SCOPE_INVALID",
        ),
    ],
)
def test_misleading_success_summary_cannot_override_raw_or_link_checks(
    tmp_path, name, mutate, code
):
    root, pin = setup(tmp_path)
    rewrite_current(tmp_path, name, mutate)
    before = snapshot(tmp_path / "data")
    value = run(tmp_path, root, pin)
    assert value == {"blocked": code}, value
    assert before == snapshot(tmp_path / "data")


def test_unsealed_source_modification_and_duplicate_success_fail(tmp_path):
    root, pin = setup(tmp_path)
    rewrite_current(
        tmp_path, "MODE_AFTER.json", lambda x: x.update(dualSidePosition=True), reseal=False
    )
    assert run(tmp_path, root, pin) == {"blocked": "PREVIEW_RAW_HASH_MISMATCH"}


def test_bundle_has_no_transport_credentials_or_writer_and_rejects_tampering(tmp_path):
    files = package()
    for name, raw in files.items():
        (tmp_path / name).write_bytes(raw)
    executable = b"\n".join(v for n, v in files.items() if n.endswith(".ps1"))
    for forbidden in (
        b"Export-Http",
        b"Export-Credentials",
        b"Invoke-WebRequest",
        b"WebRequest",
        b"FileMode]::Create",
        b"FileAccess]::Write",
        b"New-RecoveryApply",
    ):
        assert forbidden not in executable
    pin = digest(files["TRANSFER_MANIFEST.json"])
    (tmp_path / "PREVIEW_CORE.ps1").write_text("PRIVATE_DO_NOT_EXECUTE")
    result = launch(
        tmp_path, f"& '{tmp_path}/RUN_PREVIEW.ps1' -ManifestSHA256 '{pin}'\nexit $LASTEXITCODE"
    )
    assert result.returncode == 2 and not result.stderr
    assert "PRIVATE_DO_NOT_EXECUTE" not in result.stdout
    assert json.loads(result.stdout)["failure_category"] == "PREVIEW_TRANSFER_INVALID"


def test_duplicate_current_success_is_not_silently_selected(tmp_path):
    import shutil

    root, pin = setup(tmp_path)
    parent = tmp_path / "data/WBKANG-DEMO-CurrentReads"
    folder = next(parent.glob("current-read-*"))
    other = parent / ("current-read-20261118-" + "f" * 32)
    shutil.copytree(folder, other)
    receipt = json.loads((other / "RECEIPT.json").read_text())
    receipt["private_directory"] = str(other)
    (other / "RECEIPT.json").write_text(json.dumps(receipt))
    before = snapshot(tmp_path / "data")
    assert run(tmp_path, root, pin) == {"blocked": "PREVIEW_CURRENT_NOT_UNIQUE"}
    assert before == snapshot(tmp_path / "data")


def test_sealed_launcher_full_preview_does_not_write_or_prompt(tmp_path):
    root, pin = setup(tmp_path)
    path = tmp_path / "RUN_PREVIEW.ps1"
    path.write_text(
        path.read_text().replace(
            "2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d", pin
        )
    )
    acl = tmp_path / "PRIVATE_READ.ps1"
    acl.write_text(
        "function Reference-AssertPrivateDirectory {param($Path) if(-not [IO.Directory]::Exists($Path)){throw 'REFERENCE_TEST_DIRECTORY_MISSING'}}"
    )
    manifest = json.loads((tmp_path / "TRANSFER_MANIFEST.json").read_text())
    for file in (path, acl):
        manifest["files"][file.name] = digest(file.read_bytes())
    raw = json.dumps(manifest).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(raw)
    before = snapshot(tmp_path / "data")
    result = launch(
        tmp_path,
        f"function tar.exe {{& tar @args}}\n& '{path}' -ManifestSHA256 '{digest(raw)}'\nexit $LASTEXITCODE",
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert "RECOVERY_PREVIEW_COMPLETE_NO_APPLY_NO_HTTP_NO_WRITES_KEEP_STOPPED" in result.stdout
    assert before == snapshot(tmp_path / "data")
