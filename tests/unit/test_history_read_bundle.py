"""Native PowerShell export job journal and signed GET tests with synthetic responses."""

import hashlib
import json
import os
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_history_read_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch
from tests.unit.test_private_receipt_review_v2 import snapshot
from tests.unit.test_support_draft_bundle import setup as support_setup

pytestmark = pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
KEY = "synthetic-api-key-not-real"
SECRET = "synthetic-secret-not-real"


def digest(body):
    return hashlib.sha256(body).hexdigest()


def package():
    release = os.getenv("HISTORY_READ_RELEASE_ZIP")
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
    def change(state, original, exit_report):
        for i in (0, 1):
            identifier = state["orders"][str(i)]["client_id"]
            original["observations"].append(
                dict(
                    role="ENTRY",
                    request_reference_sha256=digest(identifier.encode()),
                    observed_status="FILLED",
                    response_identity_fields_match=True,
                    remote_resolution_verified=False,
                )
            )

    root, folder, pin = support_setup(tmp_path, change)
    for name, body in package().items():
        (tmp_path / name).write_bytes(body)
    return root, folder, pin


def run(tmp_path, root, pin, mode="RequestOnce", fault="", extra="", production_storage=False):
    storage = (
        ""
        if production_storage
        else """
function Reference-AssertPrivateDirectory {param($Path) if(-not [IO.Directory]::Exists($Path)){throw 'REFERENCE_TEST_DIRECTORY_MISSING'}}
function Reference-NewDirectory {param($Token) $p=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) ('WBKANG-DEMO-HistoryReads/history-read-20261117-'+$Token);$null=[IO.Directory]::CreateDirectory($p);return $p}
"""
    )
    # Transport mock retains the real route validation, signature, timeout, response size,
    # credential leak rejection and budget code. No production host is contacted.
    body = f"""
. '{tmp_path}/READ_CORE.ps1'
. '{tmp_path}/PRIVATE_STORAGE.ps1'
. '{tmp_path}/PLAN_CORE.ps1'
. '{tmp_path}/READ_TRANSPORT.ps1'
. '{tmp_path}/HISTORY_CORE.ps1'
function tar.exe {{& tar @args}}
{storage}
function Export-Credentials {{return [pscustomobject]@{{key='{KEY}';secret='{SECRET}'}}}}
$script:calls=New-Object Collections.ArrayList
$script:fault='{fault}'
function Export-NewWebRequest {{param($Uri)
 $url=[Uri]$Uri
 if($url.Host -cne 'demo-fapi.binance.com' -or $url.Scheme -cne 'https'){{throw 'EXPORT_TEST_HOST_INVALID'}}
 $reply=@{{}}
 switch($url.AbsolutePath) {{
 '/fapi/v1/time' {{$reply=@{{serverTime=1794877200000L}}}}
 '/fapi/v1/exchangeInfo' {{$reply=@{{rateLimits=@(@{{rateLimitType='REQUEST_WEIGHT';interval='MINUTE';intervalNum=1;limit=2400}})}}}}
 '/fapi/v1/order' {{
  $id=[regex]::Match($url.Query,'orderId=([0-9]+)').Groups[1].Value
  $index=if($id -ceq '28668119491'){{0}}else{{1}}
  $reply=@{{symbol='BTCUSDT';orderId=[long]$id;clientOrderId=('PRIVATE_CLIENT_SENTINEL_'+$index);status='FILLED';side='BUY';type='LIMIT';positionSide='BOTH';reduceOnly=$false;origQty='0.0007';executedQty='0.0007';price='75725.10'}}
  if($script:fault -ceq 'anchor'){{$reply.clientOrderId='UNRELATED_ACCOUNT_ID'}}
 }}
 '/fapi/v1/allOrders' {{$reply=@(@{{clientOrderId=('DX-'+('a'*30));orderId=99;symbol='BTCUSDT';status='CANCELED'}})}}
 '/fapi/v1/allAlgoOrders' {{$reply=@(@{{clientAlgoId=('DS-'+('a'*30));algoId=123;symbol='BTCUSDT';algoStatus='EXPIRED'}})}}
 default {{throw 'EXPORT_TEST_ROUTE_INVALID'}}
 }}
 if($script:fault -ceq 'clock' -and $url.AbsolutePath -ceq '/fapi/v1/time'){{$reply.serverTime=1}}
 if($script:fault -ceq 'non_array' -and $url.AbsolutePath -ceq '/fapi/v1/allOrders'){{$reply=@{{code=200;msg='Not supported in demo'}}}}
 if($script:fault -ceq 'empty' -and $url.AbsolutePath -in @('/fapi/v1/allOrders','/fapi/v1/allAlgoOrders')){{$reply=@()}}
 if($script:fault -ceq 'limit' -and $url.AbsolutePath -ceq '/fapi/v1/allOrders'){{$reply=@(1..1000|ForEach-Object {{@{{clientOrderId=('unrelated-'+$_)}}}})}}
 if($script:fault -ceq 'leak'){{$reply.secret='{SECRET}'}}
 $headers=New-Object Collections.Specialized.NameValueCollection
 $headers.Add('X-MBX-USED-WEIGHT-1M',$(if($script:fault -ceq 'headroom'){{'2350'}}else{{'20'}}))
 if($script:fault -ceq 'missing_weight'){{$headers.Remove('X-MBX-USED-WEIGHT-1M')}}
 $response=[pscustomobject]@{{StatusCode=200;Headers=$headers;Text=(ConvertTo-Json -InputObject $reply -Depth 8 -Compress)}}
 $response|Add-Member ScriptMethod GetResponseStream {{return New-Object IO.MemoryStream(,[Text.Encoding]::UTF8.GetBytes($this.Text))}}
 $response|Add-Member ScriptMethod Dispose {{}}
 $request=[pscustomobject]@{{Method='';AllowAutoRedirect=$true;KeepAlive=$true;Timeout=0;ReadWriteTimeout=0;Headers=(New-Object Collections.Specialized.NameValueCollection);Uri=$url;Response=$response}}
 $request|Add-Member ScriptMethod GetResponse {{
  $null=$script:calls.Add($this.Uri.AbsolutePath)
  if($this.Method -cne 'GET' -or $this.AllowAutoRedirect -or $this.Timeout -ne 10000){{throw 'EXPORT_TEST_REQUEST_POLICY_INVALID'}}
  $isPublic=$this.Uri.AbsolutePath -in @('/fapi/v1/time','/fapi/v1/exchangeInfo')
  if($isPublic){{if($null -ne $this.Headers['X-MBX-APIKEY']){{throw 'EXPORT_TEST_PUBLIC_KEY_LEAK'}}}}else{{if($this.Headers['X-MBX-APIKEY'] -cne '{KEY}'){{throw 'EXPORT_TEST_SIGNED_KEY_MISSING'}}}}
  if($this.Uri.Query.Contains('{KEY}') -or $this.Uri.Query.Contains('{SECRET}')){{throw 'EXPORT_TEST_URI_SECRET_LEAK'}}
  if($script:fault -ceq 'timeout' -and $this.Uri.AbsolutePath -ceq '/fapi/v1/allAlgoOrders'){{throw (New-Object Net.WebException('SENSITIVE_ERROR_{SECRET}'))}}
  return $this.Response
 }}
 $request|Add-Member ScriptMethod Abort {{}}
 return $request
}}
$plan=Invoke-HistoryReadPlan '{root}' '{pin}'
{extra}
$result=Invoke-HistoryRead $plan
[pscustomobject]@{{result=$result;calls=@($script:calls)}}|ConvertTo-Json -Depth 10 -Compress
"""
    result = launch(tmp_path, body)
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    for forbidden in (KEY, SECRET, "PRIVATE_CLIENT_SENTINEL", "PRIVATE_DOWNLOAD_URL"):
        assert forbidden not in result.stdout
    return json.loads(result.stdout)


def test_two_lists_saved_without_new_export_or_source_changes(tmp_path):
    root, _, pin = setup(tmp_path)
    old = tmp_path / "data/WBKANG-DEMO-Exports"
    old.mkdir()
    (old / "ORDER_EXPORT_ATTEMPT_20261117.json").write_text("unresolved-old-guard")
    before = snapshot(root), snapshot(old)
    value = run(tmp_path, root, pin)
    result = value["result"]
    assert result["history_read"] == "TWO_HISTORY_LISTS_SAVED_NO_EXPORT_JOB_CREATED", value
    assert value["calls"] == [
        "/fapi/v1/time",
        "/fapi/v1/exchangeInfo",
        "/fapi/v1/order",
        "/fapi/v1/order",
        "/fapi/v1/allOrders",
        "/fapi/v1/allAlgoOrders",
    ]
    assert result["actual_http_attempts"] == 6 and result["export_jobs_created"] == 0
    assert [r["exact_id_candidate_count"] for r in result["reports"]] == [1, 1]
    assert (
        not result["remote_request_resolution_verified"]
        and not result["history_completeness_verified"]
    )
    assert (snapshot(root), snapshot(old)) == before
    directory = next((tmp_path / "data/WBKANG-DEMO-HistoryReads").glob("history-read-*"))
    for name in ("REGULAR_20261020.json", "ALGO_20261020.json"):
        raw = (directory / name).read_bytes()
        assert raw.startswith(b"[")
        assert digest(raw) in [r["response_sha256"] for r in result["reports"]]
    for file in directory.iterdir():
        assert KEY not in file.read_text() and SECRET not in file.read_text()


def test_non_array_response_retained_before_schema_rejection(tmp_path):
    root, _, pin = setup(tmp_path)
    value = run(tmp_path, root, pin, fault="non_array")
    assert value["result"]["failure_category"] == "READ_RESPONSE_NOT_ARRAY_SAVED_PRIVATELY", value
    directory = next((tmp_path / "data/WBKANG-DEMO-HistoryReads").glob("history-read-*"))
    raw = json.loads((directory / "REGULAR_20261020.json").read_bytes())
    assert raw == dict(code=200, msg="Not supported in demo")
    assert not (directory / "ALGO_20261020.json").exists()
    assert "/fapi/v1/allAlgoOrders" not in value["calls"]


@pytest.mark.parametrize("fault", ["empty", "limit"])
def test_empty_or_full_page_does_not_prove_complete_or_unsent(tmp_path, fault):
    root, _, pin = setup(tmp_path)
    value = run(tmp_path, root, pin, fault=fault)
    assert value["result"]["history_read"] == "TWO_HISTORY_LISTS_SAVED_NO_EXPORT_JOB_CREATED", value
    for report in value["result"]["reports"]:
        assert not report["history_completeness_verified"] and not report["absence_proves_not_sent"]
    assert value["result"]["reports"][0]["page_limit_reached"] is (fault == "limit")


@pytest.mark.parametrize(
    "fault,code",
    [
        ("anchor", "EXPORT_ACCOUNT_ANCHOR_MISMATCH"),
        ("headroom", "READ_INSUFFICIENT_WEIGHT_HEADROOM"),
        ("missing_weight", "READ_CURRENT_RATE_HEADER_REQUIRED"),
        ("leak", "EXPORT_RESPONSE_CONTAINS_CREDENTIAL_VALUE"),
        ("clock", "EXPORT_SERVER_CLOCK_INVALID"),
    ],
)
def test_failed_preconditions_prevent_history_reads(tmp_path, fault, code):
    root, _, pin = setup(tmp_path)
    value = run(tmp_path, root, pin, fault=fault)
    assert value["result"]["failure_category"] == code, value
    assert "/fapi/v1/allOrders" not in value["calls"]
    assert "/fapi/v1/allAlgoOrders" not in value["calls"]


def test_second_list_transport_failure_retains_first_without_automatic_retry(tmp_path):
    root, _, pin = setup(tmp_path)
    value = run(tmp_path, root, pin, fault="timeout")
    assert value["result"]["history_read"] == "STOPPED_PARTIAL_RECORDS_RETAINED_NO_AUTOMATIC_RETRY"
    assert value["calls"].count("/fapi/v1/allAlgoOrders") == 1
    assert len(value["result"]["reports"]) == 1
    assert not value["result"]["automatic_retry"]


@pytest.mark.parametrize(
    "path",
    [
        "/fapi/v1/order/asyn",
        "/fapi/v1/order/asyn/id",
        "/fapi/v1/trade/asyn",
        "/fapi/v1/income/asyn",
        "https://fapi.binance.com/fapi/v1/allOrders",
    ],
)
def test_all_export_creation_and_live_paths_absent_from_reader_transport(tmp_path, path):
    for name, data in package().items():
        (tmp_path / name).write_bytes(data)
    result = launch(
        tmp_path,
        f". '{tmp_path}/READ_CORE.ps1'\n. '{tmp_path}/READ_TRANSPORT.ps1'\ntry{{Export-ValidateRoute '{path}' @{{}};throw 'NOT_BLOCKED'}}catch{{if($_.Exception.Message -cne 'EXPORT_ROUTE_OR_PARAMETERS_FORBIDDEN'){{throw}};'BLOCKED'}}",
    )
    assert result.returncode == 0 and not result.stderr and result.stdout.strip() == "BLOCKED"
    assert b"Invoke-HistoryExport" not in package()["READ_TRANSPORT.ps1"]


def test_live_context_and_native_linux_acl_refuse_before_network(tmp_path):
    root, _, pin = setup(tmp_path)
    value = run(tmp_path, root, pin, extra="$env:LIVE_TRADING_ENABLED='true'")
    assert (
        value["result"]["failure_category"] == "READ_LIVE_CONTEXT_FORBIDDEN" and not value["calls"]
    )
    value = run(tmp_path, root, pin, production_storage=True)
    assert (
        value["result"]["failure_category"] == "REFERENCE_WINDOWS_ACL_REQUIRED"
        and not value["calls"]
    )


@pytest.mark.parametrize("file", ["READ_TRANSPORT.ps1", "HISTORY_CORE.ps1", "PLAN_CORE.ps1"])
def test_seal_rejects_changes_before_credentials(tmp_path, file):
    for name, data in package().items():
        (tmp_path / name).write_bytes(data)
    pin = digest((tmp_path / "TRANSFER_MANIFEST.json").read_bytes())
    (tmp_path / file).write_text("DO_NOT_RUN_PRIVATE_CONTENT")
    result = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_READ.ps1' -ManifestSHA256 '{pin}' -CollectOnce\nexit $LASTEXITCODE",
    )
    assert result.returncode == 2 and not result.stderr and "PRIVATE_CONTENT" not in result.stdout
    assert json.loads(result.stdout)["failure_category"] == "INSPECT_TRANSFER_INVALID"


def test_sealed_offline_check_preserves_sources_and_creates_no_output_directory(tmp_path):
    root, _, pin = setup(tmp_path)
    path = tmp_path / "RUN_READ.ps1"
    path.write_text(
        path.read_text().replace(
            "2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d", pin
        )
    )
    manifest = json.loads((tmp_path / "TRANSFER_MANIFEST.json").read_bytes())
    manifest["files"][path.name] = digest(path.read_bytes())
    body = json.dumps(manifest).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(body)
    before = snapshot(root)
    result = launch(
        tmp_path,
        f"function tar.exe {{& tar @args}}\n& '{path}' -ManifestSHA256 '{digest(body)}' -CheckOnly\nexit $LASTEXITCODE",
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert "HISTORY_READ_CHECK_PASSED_NO_HTTP" in result.stdout
    assert snapshot(root) == before
    assert not (tmp_path / "data/WBKANG-DEMO-HistoryReads").exists()
