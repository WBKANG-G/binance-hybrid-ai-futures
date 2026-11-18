"""No-network reanalysis of previously saved responses, including PS5 array semantics."""

import json
import os
import shutil
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_history_review_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch
from tests.unit.test_history_read_bundle import setup as source_setup
from tests.unit.test_private_receipt_review_v2 import snapshot
from tests.unit.test_request_reference_bundle import h
from tests.unit.test_support_draft_bundle import EXIT

pytestmark = pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")


def package():
    release = os.getenv("HISTORY_REVIEW_RELEASE_ZIP")
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


def setup(tmp_path):
    root, _, pin = source_setup(tmp_path)
    parent = tmp_path / "data/WBKANG-DEMO-HistoryReads"
    folder = parent / ("history-read-20261117-" + "1" * 32)
    folder.mkdir(parents=True)
    regular = [
        dict(
            clientOrderId="unrelated-order",
            orderId=123,
            symbol="BTCUSDT",
            side="BuySecretDoNotPrint",
            type="LIMIT",
            status="CANCELED",
            origQty="0.0007",
            executedQty="0",
            price="81726.50",
            private_extra="SECRET_MUST_NOT_PRINT",
        ),
        dict(
            clientOrderId=EXIT,
            orderId=456,
            symbol="BTCUSDT",
            side="SELL",
            type="MARKET",
            status="FILLED",
            origQty="0.0007",
            executedQty="0.0007",
            price="0",
            avgPrice="82000.0",
            reduceOnly=True,
            time=1792505265000,
        ),
    ]
    raw = json.dumps(regular).encode()
    (folder / "REGULAR_20261020.json").write_bytes(raw)
    (folder / "ALGO_20261020.json").write_bytes(b"[]")
    intent = dict(
        policy="DEMO_HISTORY_READ_INTENT_V1",
        manifest_sha256=pin,
        start_ms=1792454400000,
        end_ms=1792540799999,
        export_jobs_allowed=0,
    )
    receipt = dict(
        history_read="TWO_HISTORY_LISTS_SAVED_NO_EXPORT_JOB_CREATED",
        private_directory=str(folder),
        actual_http_attempts=6,
        export_jobs_created=0,
        order_writes=0,
        old_export_guard_changed=False,
        automatic_retry=False,
        credentials_saved=False,
        source_files_written=False,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
        reports=[
            dict(role="EXIT", response_sha256=h(raw), returned_count=1, exact_id_candidate_count=0),
            dict(
                role="STOP_LOSS",
                response_sha256=h(b"[]"),
                returned_count=1,
                exact_id_candidate_count=0,
            ),
        ],
    )
    (folder / "INTENT.json").write_text(json.dumps(intent))
    (folder / "RECEIPT.json").write_text(json.dumps(receipt))
    for name, body in package().items():
        (tmp_path / name).write_bytes(body)
    return root, parent, folder, pin


LEGACY = """
function ConvertFrom-Json {
 [CmdletBinding()]
 param([Parameter(ValueFromPipeline=$true)][object]$InputObject)
 process {Microsoft.PowerShell.Utility\\ConvertFrom-Json -InputObject $InputObject -NoEnumerate}
}
"""


def run(tmp_path, root, parent, pin, legacy=False, production_acl=False):
    acl = (
        ""
        if production_acl
        else "function Reference-AssertPrivateDirectory {param($Path) if(-not [IO.Directory]::Exists($Path)){throw 'REFERENCE_TEST_DIRECTORY_MISSING'}}"
    )
    result = launch(
        tmp_path,
        f"""
. '{tmp_path}/READ_CORE.ps1'
. '{tmp_path}/PLAN_CORE.ps1'
. '{tmp_path}/PRIVATE_READ.ps1'
. '{tmp_path}/ARRAY_CORE.ps1'
. '{tmp_path}/REVIEW_CORE.ps1'
{acl}
{LEGACY if legacy else ""}
function tar.exe {{& tar @args}}
function Read-Host {{throw 'CREDENTIALS_FORBIDDEN'}}
$plan=Invoke-HistoryReadPlan '{root}' '{pin}'
Invoke-SavedHistoryReview $plan '{parent}'|ConvertTo-Json -Depth 15 -Compress
""",
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    for forbidden in (EXIT, "SECRET_MUST_NOT_PRINT", "BuySecretDoNotPrint", "unrelated-order"):
        assert forbidden not in result.stdout
    return json.loads(result.stdout)


@pytest.mark.parametrize("legacy", [False, True])
def test_saved_rows_reanalyzed_without_inputs_changing_or_credential_access(tmp_path, legacy):
    root, parent, _, pin = setup(tmp_path)
    before = snapshot(root), snapshot(parent)
    value = run(tmp_path, root, parent, pin, legacy=legacy)
    assert value["saved_history_review"] == "SAVED_HISTORY_REANALYZED_NO_HTTP_NO_WRITES", value
    byrole = {r["role"]: r for r in value["reports"]}
    assert byrole["EXIT"]["old_reported_count"] == 1
    assert byrole["EXIT"]["corrected_returned_count"] == 2
    assert byrole["EXIT"]["corrected_exact_id_candidate_count"] == 1
    assert byrole["EXIT"]["safe_preview"][0]["side"] is None
    assert byrole["EXIT"]["exact_match_preview"][0]["server_id"] == "456"
    assert byrole["STOP_LOSS"]["corrected_returned_count"] == 0
    assert byrole["STOP_LOSS"]["corrected_exact_id_candidate_count"] == 0
    assert value["actual_http_attempts"] == 0 and not value["files_written"]
    assert not value["credentials_accessed"] and not value["request_states_changed"]
    assert not any(r["remote_resolution_verified"] for r in value["reports"])
    assert before == (snapshot(root), snapshot(parent))


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("body,count", [("[]", 0), ('[{"a":1}]', 1), ('[{"a":1},{"a":2}]', 2)])
def test_parser_exact_zero_single_multi_counts_for_native_and_ps5_output(
    tmp_path, legacy, body, count
):
    for name, data in package().items():
        (tmp_path / name).write_bytes(data)
    result = launch(
        tmp_path,
        f". '{tmp_path}/READ_CORE.ps1'\n. '{tmp_path}/ARRAY_CORE.ps1'\n{LEGACY if legacy else ''}\n(Read-List '{body}').returned_count",
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert int(result.stdout) == count


@pytest.mark.parametrize(
    "body",
    ["[[]]", "[null]", "[1]", '["private-value"]', '[],"items":[{"a":1}]', '[{"a":1}]garbage'],
)
def test_nested_scalar_null_and_invalid_root_injection_are_rejected(tmp_path, body):
    for name, data in package().items():
        (tmp_path / name).write_bytes(data)
    result = launch(
        tmp_path,
        f". '{tmp_path}/READ_CORE.ps1'\n. '{tmp_path}/ARRAY_CORE.ps1'\ntry{{Read-List '{body}'|Out-Null;throw 'NOT_BLOCKED'}}catch{{if($_.Exception.Message -cnotmatch '^READ_'){{throw}};'BLOCKED'}}",
    )
    assert result.returncode == 0 and not result.stderr and result.stdout.strip() == "BLOCKED"


def test_legacy_empty_array_bug_reproduces_from_published_source_and_fixed_parser_corrects_it(
    tmp_path,
):
    for name, data in package().items():
        (tmp_path / name).write_bytes(data)
    old = builder.git("show", "45e8e00:tools/windows_demo_history_read/HISTORY_CORE.ps1").decode()
    old = old[old.index("function Read-List(") : old.index("function Invoke-HistoryRead(")].replace(
        "function Read-List(", "function Read-List-Old("
    )
    (tmp_path / "OLD.ps1").write_text(old)
    result = launch(
        tmp_path,
        f". '{tmp_path}/READ_CORE.ps1'\n. '{tmp_path}/OLD.ps1'\n. '{tmp_path}/ARRAY_CORE.ps1'\n{LEGACY}\n[pscustomobject]@{{old=(Read-List-Old '[]').returned_count;corrected=(Read-List '[]').returned_count}}|ConvertTo-Json -Compress",
    )
    assert result.returncode == 0 and not result.stderr
    assert json.loads(result.stdout) == dict(old=1, corrected=0)


@pytest.mark.parametrize("fault", ["raw", "receipt_hash", "intent", "duplicate", "nonempty_algo"])
def test_unmatched_or_changed_saved_data_does_not_produce_a_review(tmp_path, fault):
    root, parent, folder, pin = setup(tmp_path)
    receipt = folder / "RECEIPT.json"
    if fault == "raw":
        p = folder / "REGULAR_20261020.json"
        p.write_bytes(p.read_bytes() + b" ")
    elif fault == "receipt_hash":
        value = json.loads(receipt.read_text())
        value["reports"][0]["response_sha256"] = "0" * 64
        receipt.write_text(json.dumps(value))
    elif fault == "intent":
        p = folder / "INTENT.json"
        value = json.loads(p.read_text())
        value["manifest_sha256"] = "0" * 64
        p.write_text(json.dumps(value))
    elif fault == "duplicate":
        other = parent / ("history-read-20261117-" + "2" * 32)
        shutil.copytree(folder, other)
        value = json.loads(receipt.read_text())
        value["private_directory"] = str(other)
        (other / "RECEIPT.json").write_text(json.dumps(value))
    else:
        (folder / "ALGO_20261020.json").write_bytes(b"[{}]")
        value = json.loads(receipt.read_text())
        value["reports"][1]["response_sha256"] = h(b"[{}]")
        receipt.write_text(json.dumps(value))
    before = snapshot(parent)
    value = run(tmp_path, root, parent, pin)
    assert value["saved_history_review"] == "BLOCKED_NO_HTTP_NO_WRITES"
    assert value["actual_http_attempts"] == 0 and snapshot(parent) == before


def test_production_acl_refuses_linux_without_windows_claim(tmp_path):
    root, parent, _, pin = setup(tmp_path)
    value = run(tmp_path, root, parent, pin, production_acl=True)
    assert value["failure_category"] == "REFERENCE_WINDOWS_ACL_REQUIRED"


@pytest.mark.parametrize("file", ["ARRAY_CORE.ps1", "REVIEW_CORE.ps1", "PRIVATE_READ.ps1"])
def test_sealed_launcher_denies_tamper(tmp_path, file):
    for name, body in package().items():
        (tmp_path / name).write_bytes(body)
    pin = h((tmp_path / "TRANSFER_MANIFEST.json").read_bytes())
    (tmp_path / file).write_text("MUST_NOT_RUN_PRIVATE")
    result = launch(
        tmp_path, f"& '{tmp_path}/RUN_REVIEW.ps1' -ManifestSHA256 '{pin}'\nexit $LASTEXITCODE"
    )
    assert result.returncode == 2 and not result.stderr and "PRIVATE" not in result.stdout
    assert json.loads(result.stdout)["failure_category"] == "INSPECT_TRANSFER_INVALID"


def test_full_sealed_review_with_fixture_pin_and_acl_substitutions(tmp_path):
    root, parent, _, pin = setup(tmp_path)
    path = tmp_path / "RUN_REVIEW.ps1"
    path.write_text(
        path.read_text().replace(
            "2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d", pin
        )
    )
    acl = tmp_path / "PRIVATE_READ.ps1"
    acl.write_text(
        "function Reference-AssertPrivateDirectory {param($Path) if(-not [IO.Directory]::Exists($Path)){throw 'REFERENCE_TEST_DIRECTORY_MISSING'}}"
    )
    manifest = json.loads((tmp_path / "TRANSFER_MANIFEST.json").read_bytes())
    for name in (path.name, acl.name):
        manifest["files"][name] = h((tmp_path / name).read_bytes())
    raw = json.dumps(manifest).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(raw)
    before = snapshot(root), snapshot(parent)
    result = launch(
        tmp_path,
        f"function tar.exe {{& tar @args}}\n& '{path}' -ManifestSHA256 '{h(raw)}'\nexit $LASTEXITCODE",
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert "SAVED_HISTORY_REVIEW_COMPLETE_NO_HTTP_NO_WRITES" in result.stdout
    assert (
        "456" in result.stdout
        and EXIT not in result.stdout
        and "SECRET_MUST_NOT_PRINT" not in result.stdout
    )
    assert before == (snapshot(root), snapshot(parent))


def test_payloads_have_no_credentials_network_or_file_write_commands(tmp_path):
    for name, body in package().items():
        (tmp_path / name).write_bytes(body)
    result = launch(
        tmp_path,
        f"""
foreach($file in @('ARRAY_CORE.ps1','REVIEW_CORE.ps1','PRIVATE_READ.ps1','PLAN_CORE.ps1','READ_CORE.ps1','RUN_REVIEW.ps1')) {{
 $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path '{tmp_path}' $file),[ref]$tokens,[ref]$errors)
 if($errors.Count -gt 0){{throw 'PARSE_ERROR'}}
 foreach($node in $ast.FindAll({{param($n) $n -is [Management.Automation.Language.CommandAst]}},$true)){{
  if($node.GetCommandName() -in @('Read-Host','docker','Start-Process','Invoke-WebRequest','Invoke-RestMethod','Invoke-Expression','Set-Content','Out-File','New-Item','Remove-Item','Copy-Item','Move-Item')){{throw 'FORBIDDEN_COMMAND'}}
 }}
}}
'PARSED_OFFLINE_READ_ONLY'
""",
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr


def test_unknown_sensitive_count_metadata_is_not_printed(tmp_path):
    root, parent, folder, pin = setup(tmp_path)
    path = folder / "RECEIPT.json"
    value = json.loads(path.read_text())
    value["reports"][0]["returned_count"] = "SECRET_MUST_NOT_PRINT"
    path.write_text(json.dumps(value))
    result = run(tmp_path, root, parent, pin)
    assert result["failure_category"] == "REVIEW_REPORT_COUNT_INVALID"


def test_offline_parser_is_the_same_fix_used_by_future_collection(tmp_path):
    core = (ROOT / "tools/windows_demo_history_read/HISTORY_CORE.ps1").read_bytes()
    parser = core[core.index(b"function Read-List(") : core.index(b"function Invoke-HistoryRead(")]
    assert package()["ARRAY_CORE.ps1"] == parser
