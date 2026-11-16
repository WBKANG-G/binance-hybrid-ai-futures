"""Stored amounts and provenance remain separate from current-state/adoption proof."""

import hashlib
import io
import json
import os
import tarfile
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_baseline_reference_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch
from tests.unit.test_private_receipt_review_v2 import snapshot
from tests.unit.test_request_reference_bundle import records as request_records

pytestmark = pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")


def h(body):
    return hashlib.sha256(body).hexdigest()


def package():
    release = os.getenv("BASELINE_REFERENCE_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git
    ref = real("rev-parse", "HEAD").decode().strip()

    def read(*args):
        if args[0] == "show" and args[1].startswith(ref + ":"):
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with patch.object(builder, "git", read):
        return builder.package_files("HEAD")


def records():
    a, _ = request_records()
    s = a["old_row"]["state"]
    s.update(
        paused=True,
        daily_key="2026-11-12",
        weekly_key="2026-11-09",
        daily_base="4998.74183608",
        weekly_base="4998.74183608",
        high_water="5000.00000000",
        available_balance="4998.74183608",
        value=dict(
            as_of="2026-11-06T06:52:53.987Z",
            initial="5000.00000000",
            realized="-0.80000000",
            unrealized="0.00000000",
            fees="0.45816392",
            funding="0.00000000",
            net_deposits="0.00000000",
        ),
    )
    a["old_row"]["fence"] = 58
    a["sensitive_unknown"] = "SENSITIVE_RAW_STATE_MUST_NOT_OUTPUT"
    operation = "11111111-1111-1111-1111-111111111111"
    complete = dict(
        accounted_day13_adoption="APPLIED_VERIFIED_KEEP_PAUSED",
        operation_id=operation,
        day_kst="2026-11-13",
        week_kst_preserved="2026-11-09",
        daily_base_usdt="4998.74183608",
        weekly_base_usdt_preserved="4998.74183608",
        high_water_usdt_preserved="5000.00000000",
        baseline_source="OPERATOR_ATTESTED_FLAT_WITH_SIGNED_USDT_EVIDENCE",
        day_adopted=True,
        operator_attestation_used=True,
        prior_financial_history_preserved=True,
        only_reviewed_account_fields_changed=True,
        paused=True,
        exact_day_midnight_valuation_verified=False,
        account_wide_trade_export_verified=False,
        exchange_account_identity_verified=False,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
    )
    commit = dict(
        policy=a["policy"], operation_id=operation, new_fence=59, new_full_ledger_sha256="a" * 64
    )
    return a, complete, commit


def setup(tmp_path, mutate=None, missing=None):
    a, c, k = records()
    if mutate:
        mutate(a, c, k)
    root = tmp_path / "data" / "WBKANG-DEMO-Backups"
    folder = root / ("cold-20261114-" + "1" * 32)
    folder.mkdir(parents=True)
    archive = folder / "runtime.tar"
    entries = []
    prefix = "demo-runtime-20261111/accounted-day13-adoption-11111111-1111-1111-1111-111111111111/"
    with tarfile.open(archive, "w") as tar:
        for name, value in [("intent.json", a), ("complete.json", c), ("commit-intent.json", k)]:
            if missing == name:
                continue
            body = json.dumps(value).encode()
            info = tarfile.TarInfo(prefix + name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
            entries.append(dict(path=prefix + name, kind="file", size=len(body)))
    manifest = json.dumps(
        dict(
            groups=dict(
                runtime=dict(
                    entries=entries,
                    archive_bytes=archive.stat().st_size,
                    archive_sha256=h(archive.read_bytes()),
                )
            )
        )
    ).encode()
    (folder / "manifest.json").write_bytes(manifest)
    for p, b in package().items():
        (tmp_path / p).write_bytes(b)
    return root, folder, h(manifest)


def run(tmp_path, root, pin, extra=""):
    r = launch(
        tmp_path,
        f"""
. '{tmp_path}/READ_CORE.ps1'
. '{tmp_path}/BASELINE_CORE.ps1'
function tar.exe {{& tar @args}}
function docker {{throw 'DOCKER_MUST_NOT_RUN'}}
{extra}
Invoke-BaselineReferenceInspection '{root}' '{pin}'|ConvertTo-Json -Depth 8 -Compress
""",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    return json.loads(r.stdout), r.stdout


def test_native_tar_reads_stored_numbers_without_current_state_or_new_acceptance(tmp_path):
    root, _, pin = setup(tmp_path)
    before = snapshot(root)
    v, out = run(tmp_path, root, pin)
    assert (
        v["baseline_reference_inspection"] == "PINNED_PRIOR_BASELINES_AND_HISTORICAL_REPORT_READ"
    ), v
    r = v["review"]
    assert r["prior_snapshot_day_kst"] == "2026-11-12"
    assert r["historical_report_day_kst"] == "2026-11-13"
    assert r["derived_prior_snapshot_equity_usdt"] == "4998.74183608"
    assert r["recorded_value_components_usdt"]["initial"] == "5000.00000000"
    assert r["recorded_prior_high_water_usdt"] == "5000.00000000"
    assert r["historical_report_operator_attestation_used"] is True
    for flag in [
        "historical_attestation_independently_verified_now",
        "initial_funding_independently_verified",
        "historical_balance_provenance_complete",
        "new_operator_attestation_received",
        "baseline_source_accepted_now",
        "baseline_adoption_approved",
        "period_baselines_changed",
        "current_database_state_verified",
        "commit_hash_compared_to_current_database",
        "historical_report_exact_midnight_valuation_verified",
        "historical_report_account_wide_trade_export_verified",
    ]:
        assert r[flag] is False
    for flag in [
        "files_written",
        "archive_extracted_to_disk",
        "raw_account_state_displayed",
        "raw_client_ids_displayed",
        "credentials_accessed",
        "docker_used",
        "services_started",
        "execution_approved",
        "orders_enabled",
        "live_trading_enabled",
    ]:
        assert v[flag] is False
    assert snapshot(root) == before
    assert "PRIVATE_CLIENT_SENTINEL" not in out and "SENSITIVE_RAW_STATE_MUST_NOT_OUTPUT" not in out


@pytest.mark.parametrize(
    "value",
    [
        "NaN",
        "Infinity",
        "1e3",
        "1,000",
        "BAD_PRIVATE_VALUE",
        True,
        None,
        5000.0,
        "12345678901234567890.123456789",
    ],
)
def test_noncanonical_or_overprecise_money_denied_without_raw_value(tmp_path, value):
    root, _, pin = setup(
        tmp_path, lambda a, c, k: a["old_row"]["state"]["value"].update(initial=value)
    )
    v, out = run(tmp_path, root, pin)
    assert v["failure_category"] == "BASELINE_MONEY_INVALID"
    assert "BAD_PRIVATE_VALUE" not in out and "review" not in v


@pytest.mark.parametrize(
    "change",
    [
        "account",
        "period",
        "daily_amount",
        "weekly_amount",
        "high_water",
        "fence",
        "operation",
        "source",
        "authority",
        "midnight_flag",
        "string_flag",
        "paused",
    ],
)
def test_invalid_provenance_links_are_not_accepted(tmp_path, change):
    def mutate(a, c, k):
        if change == "account":
            a["old_row"]["account_id"] = "OTHER"
        elif change == "period":
            c["day_kst"] = "2026-11-16"
        elif change == "daily_amount":
            c["daily_base_usdt"] = "9999"
        elif change == "weekly_amount":
            c["weekly_base_usdt_preserved"] = "9999"
        elif change == "high_water":
            c["high_water_usdt_preserved"] = "9999"
        elif change == "fence":
            k["new_fence"] = 60
        elif change == "operation":
            k["operation_id"] = "22222222-2222-2222-2222-222222222222"
        elif change == "source":
            c["baseline_source"] = "SENSITIVE_UNTRUSTED_SOURCE"
        elif change == "authority":
            c["orders_enabled"] = True
        elif change == "midnight_flag":
            c["exact_day_midnight_valuation_verified"] = True
        elif change == "string_flag":
            c["operator_attestation_used"] = "true"
        elif change == "paused":
            a["old_row"]["state"]["paused"] = False

    root, _, pin = setup(tmp_path, mutate)
    before = snapshot(root)
    v, out = run(tmp_path, root, pin)
    assert v["baseline_reference_inspection"] == "BLOCKED_NO_WRITES" and "review" not in v
    assert "SENSITIVE_UNTRUSTED_SOURCE" not in out and snapshot(root) == before


@pytest.mark.parametrize("name", ["intent.json", "complete.json", "commit-intent.json"])
def test_missing_same_folder_evidence_denied(tmp_path, name):
    root, _, pin = setup(tmp_path, missing=name)
    v, _ = run(tmp_path, root, pin)
    assert v["failure_category"] == "BASELINE_RECORD_NOT_UNIQUE"


def test_decimal_arithmetic_and_output_are_culture_independent(tmp_path):
    root, _, pin = setup(tmp_path)
    v, _ = run(
        tmp_path,
        root,
        pin,
        "[Threading.Thread]::CurrentThread.CurrentCulture=[Globalization.CultureInfo]::GetCultureInfo('de-DE')",
    )
    assert v["review"]["derived_prior_snapshot_equity_usdt"] == "4998.74183608"


@pytest.mark.parametrize("which", ["manifest", "archive"])
def test_changed_source_denied_before_reading_records(tmp_path, which):
    root, folder, pin = setup(tmp_path)
    p = folder / ("manifest.json" if which == "manifest" else "runtime.tar")
    p.write_bytes(p.read_bytes() + b" ")
    v, _ = run(tmp_path, root, pin, "function Inspect-ReadArchive {throw 'MUST_NOT_READ'}")
    assert v["failure_category"] == (
        "BASELINE_BACKUP_NOT_UNIQUE" if which == "manifest" else "BASELINE_ARCHIVE_HASH_MISMATCH"
    )


@pytest.mark.parametrize("file", ["READ_CORE.ps1", "BASELINE_CORE.ps1", "README_KO.md"])
def test_seal_tamper_denied(tmp_path, file):
    for p, b in package().items():
        (tmp_path / p).write_bytes(b)
    pin = h((tmp_path / "TRANSFER_MANIFEST.json").read_bytes())
    (tmp_path / file).write_text("SENTINEL_MUST_NOT_RUN")
    r = launch(
        tmp_path, f"& '{tmp_path}/RUN_BASELINE.ps1' -ManifestSHA256 '{pin}'\nexit $LASTEXITCODE"
    )
    assert r.returncode == 2 and not r.stderr and "SENTINEL" not in r.stdout
    assert json.loads(r.stdout)["failure_category"] == "INSPECT_TRANSFER_INVALID"


def test_sealed_launcher_with_only_synthetic_backup_pin_and_tar_name_substituted(tmp_path):
    root, _, pin = setup(tmp_path)
    launcher = tmp_path / "RUN_BASELINE.ps1"
    launcher.write_text(
        launcher.read_text().replace(
            "2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d", pin
        )
    )
    manifest = json.loads((tmp_path / "TRANSFER_MANIFEST.json").read_bytes())
    manifest["files"]["RUN_BASELINE.ps1"] = h(launcher.read_bytes())
    body = json.dumps(manifest).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(body)
    before = snapshot(root)
    r = launch(
        tmp_path,
        f"function tar.exe {{& tar @args}}\n& '{tmp_path}/RUN_BASELINE.ps1' -ManifestSHA256 '{h(body)}'\nexit $LASTEXITCODE",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    assert "PRIOR_BASELINES_READ_NO_ADOPTION_NO_WRITES_NO_DOCKER" in r.stdout
    assert snapshot(root) == before


def test_reused_reader_is_byte_identical_to_windows_successful_release():
    p = (
        ROOT
        / "artifacts/windows-demo-request-reference-20261116/windows-demo-request-reference-20261116.zip"
    )
    with zipfile.ZipFile(p) as z:
        assert package()["READ_CORE.ps1"] == z.read(
            "windows-demo-request-reference-20261116/INSPECT_CORE.ps1"
        )


def test_duplicate_pinned_backup_is_never_selected_by_first_or_newest(tmp_path):
    import shutil

    root, folder, pin = setup(tmp_path)
    shutil.copytree(folder, root / ("cold-20261114-" + "2" * 32))
    v, _ = run(tmp_path, root, pin)
    assert v["failure_category"] == "BASELINE_BACKUP_NOT_UNIQUE"


def test_final_hash_recheck_failure_does_not_return_review(tmp_path):
    root, _, pin = setup(tmp_path)
    before = snapshot(root)
    v, _ = run(
        tmp_path,
        root,
        pin,
        """
$script:hash=${function:Inspect-StreamHash};$script:hashCalls=0
function Inspect-StreamHash([IO.Stream]$Stream) {$script:hashCalls++;if ($script:hashCalls -eq 4) {return ('0'*64)};return (& $script:hash $Stream)}
""",
    )
    assert v["failure_category"] == "BASELINE_SOURCE_CHANGED" and "review" not in v
    assert snapshot(root) == before


def test_parse_and_no_network_write_or_service_commands(tmp_path):
    for p, b in package().items():
        (tmp_path / p).write_bytes(b)
    r = launch(
        tmp_path,
        f"""
foreach ($file in @('RUN_BASELINE.ps1','BASELINE_CORE.ps1','READ_CORE.ps1')) {{
 $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path '{tmp_path}' $file),[ref]$tokens,[ref]$errors)
 if ($errors.Count -gt 0) {{throw 'PARSE_ERROR'}}
 foreach ($node in $ast.FindAll({{param($n) $n -is [Management.Automation.Language.CommandAst]}},$true)) {{
  if ($node.GetCommandName() -in @('docker','Start-Process','Invoke-Expression','Remove-Item','Move-Item','Copy-Item','Set-Content','Out-File','Invoke-WebRequest','Invoke-RestMethod')) {{throw 'FORBIDDEN_COMMAND'}}
 }}
}}
Write-Output 'PARSED_NO_WRITES_NETWORK_OR_SERVICES'
""",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    assert "PARSED_NO_WRITES_NETWORK_OR_SERVICES" in r.stdout
