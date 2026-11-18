"""Native PowerShell export job journal and signed GET tests with synthetic responses."""

import hashlib
import json
import os
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_income_read_bundle as builder
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
    release = os.getenv("INCOME_READ_RELEASE_ZIP")
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
        state["policy"] = {"week_start_kst": 0}
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
function Reference-NewDirectory {param($Token) $p=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) ('WBKANG-DEMO-IncomeReads/income-read-20261118-'+$Token);$null=[IO.Directory]::CreateDirectory($p);return $p}
"""
    )
    # Transport mock retains the real route validation, signature, timeout, response size,
    # credential leak rejection and budget code. No production host is contacted.
    body = f"""
. '{tmp_path}/READ_CORE.ps1'
. '{tmp_path}/PRIVATE_STORAGE.ps1'
. '{tmp_path}/PLAN_CORE.ps1'
. '{tmp_path}/READ_TRANSPORT.ps1'
. '{tmp_path}/ARRAY_CORE.ps1'
. '{tmp_path}/INCOME_CORE.ps1'
function tar.exe {{& tar @args}}
{storage}
function Export-Credentials {{return [pscustomobject]@{{key='{KEY}';secret='{SECRET}'}}}}
$script:calls=New-Object Collections.ArrayList
$script:fault='{fault}'
$script:accountCalls=0
function Export-NewWebRequest {{param($Uri)
 $url=[Uri]$Uri
 if($url.Host -cne 'demo-fapi.binance.com' -or $url.Scheme -cne 'https'){{throw 'EXPORT_TEST_HOST_INVALID'}}
 $reply=@{{}}
 switch($url.AbsolutePath) {{
 '/fapi/v1/time' {{$reply=@{{serverTime=1794963600000L}}}}
 '/fapi/v1/exchangeInfo' {{$reply=@{{rateLimits=@(@{{rateLimitType='REQUEST_WEIGHT';interval='MINUTE';intervalNum=1;limit=2400}})}}}}
 '/fapi/v1/order' {{
  $id=[regex]::Match($url.Query,'orderId=([0-9]+)').Groups[1].Value
  $index=if($id -ceq '28668119491'){{0}}else{{1}}
  $reply=@{{symbol='BTCUSDT';orderId=[long]$id;clientOrderId=('PRIVATE_CLIENT_SENTINEL_'+$index);status='FILLED';side='BUY';type='LIMIT';positionSide='BOTH';reduceOnly=$false;origQty='0.0007';executedQty='0.0007';price='75725.10'}}
  if($script:fault -ceq 'anchor'){{$reply.clientOrderId='UNRELATED_ACCOUNT_ID'}}
 }}
 '/fapi/v3/account' {{
  $script:accountCalls++
  $wallet=if($script:fault -ceq 'wallet_change' -and $script:accountCalls -eq 2){{'4999.74183608'}}else{{'4998.74183608'}}
  $reply=@{{assets=@(@{{asset='USDC';walletBalance='5000.00000000';unrealizedProfit='0.00000000'}},@{{asset='USDT';walletBalance=$wallet;unrealizedProfit='0.00000000'}});positions=@(@{{symbol='BTCUSDT';positionAmt='0.00000000'}})}}
  if($script:fault -ceq 'open_position'){{$reply.positions[0].positionAmt='0.00070000'}}
 }}
 '/fapi/v1/income' {{
  $start=[long][regex]::Match($url.Query,'startTime=([0-9]+)').Groups[1].Value
  $end=[long][regex]::Match($url.Query,'endTime=([0-9]+)').Groups[1].Value
  $page=[int][regex]::Match($url.Query,'page=([0-9]+)').Groups[1].Value
  if($start -ge 1794963600000L){{
   $reply=@()
   if($script:fault -ceq 'tail_event'){{$reply=@(@{{incomeType='COMMISSION';tranId=90000;asset='USDT';income='0.00000000';time=$start;symbol='BTCUSDT'}})}}
  }}else{{
   $reply=@(
    @{{incomeType='TRANSFER';tranId=1;asset='USDT';income='10.00000000';time=1794754801000L;symbol=''}},
    @{{incomeType='COMMISSION';tranId=2;asset='USDT';income='-0.10000000';time=1794927601000L;symbol='BTCUSDT'}},
    @{{incomeType='REALIZED_PNL';tranId=3;asset='USDT';income='0.30000000';time=1794927602000L;symbol='BTCUSDT'}},
    @{{incomeType='FUNDING_FEE';tranId=4;asset='USDT';income='-0.02000000';time=1794754805000L;symbol='BTCUSDT'}},
    @{{incomeType='TRANSFER';tranId=5;asset='USDC';income='5000.00000000';time=1794754801000L;symbol=''}}
   )
   if($script:fault -ceq 'empty'){{$reply=@()}}
   if($script:fault -ceq 'conflict'){{$reply+=@{{incomeType='TRANSFER';tranId=1;asset='USDT';income='11.00000000';time=1794754801000L;symbol=''}}}}
   if($script:fault -ceq 'duplicate'){{$reply+=@{{incomeType='TRANSFER';tranId=1;asset='USDT';income='10.00000000';time=1794754801000L;symbol=''}}}}
   if($script:fault -ceq 'new_type'){{$reply[0].incomeType='NEW_UNREVIEWED_TYPE'}}
   if($script:fault -ceq 'week_boundary'){{$reply[0].time=1794754800000L}}
   if($script:fault -ceq 'day_boundary'){{$reply[1].time=1794927600000L}}
   if($script:fault -ceq 'bad_amount'){{$reply[0].income='1e2'}}
   if($script:fault -ceq 'outside'){{$reply[0].time=1794700000000L}}
   if($script:fault -ceq 'non_array'){{$reply=@{{code=200;msg='Unsupported income response'}}}}
   if($script:fault -in @('full','no_progress','two_pages')){{
    $offset=if($script:fault -ceq 'no_progress'){{0}}else{{($page-1)*1000}}
    $reply=@(1..1000|ForEach-Object {{@{{incomeType='COMMISSION';tranId=($_+$offset);asset='USDT';income='0.00000000';time=1794754801000L;symbol='BTCUSDT'}}}})
    if($script:fault -ceq 'two_pages' -and $page -eq 2){{$reply=@(@{{incomeType='COMMISSION';tranId=1;asset='USDT';income='0.00000000';time=1794754801000L;symbol='BTCUSDT'}},@{{incomeType='COMMISSION';tranId=1001;asset='USDT';income='0.00000000';time=1794754801000L;symbol='BTCUSDT'}})}}
   }}
  }}
 }}
 default {{throw 'EXPORT_TEST_ROUTE_INVALID'}}
 }}
 if($script:fault -ceq 'clock' -and $url.AbsolutePath -ceq '/fapi/v1/time'){{$reply.serverTime=1}}
 if($script:fault -ceq 'leak'){{$reply.secret='{SECRET}'}}
 $headers=New-Object Collections.Specialized.NameValueCollection
 $headers.Add('X-MBX-USED-WEIGHT-1M',$(if($script:fault -ceq 'headroom'){{'2200'}}else{{'20'}}))
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
  if($script:fault -ceq 'timeout' -and $this.Uri.AbsolutePath -ceq '/fapi/v1/income'){{throw (New-Object Net.WebException('SENSITIVE_ERROR_{SECRET}'))}}
  return $this.Response
 }}
 $request|Add-Member ScriptMethod Abort {{}}
 return $request
}}
$plan=Invoke-IncomePlan '{root}' '{pin}'
{extra}
$result=Invoke-IncomeRead $plan
[pscustomobject]@{{result=$result;calls=@($script:calls)}}|ConvertTo-Json -Depth 10 -Compress
"""
    result = launch(tmp_path, body)
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    for forbidden in (KEY, SECRET, "PRIVATE_CLIENT_SENTINEL", "PRIVATE_DOWNLOAD_URL"):
        assert forbidden not in result.stdout
    return json.loads(result.stdout)


def test_income_candidates_use_exact_usdt_flows_once_and_preserve_sources(tmp_path):
    root, _, pin = setup(tmp_path)
    before = snapshot(root)
    value = run(tmp_path, root, pin)
    result = value["result"]
    assert result["income_read"] == "INCOME_RESPONSES_SAVED_WALLET_CANDIDATES_ONLY", value
    assert result["boundaries"]["day_key"] == "2026-11-18"
    assert result["boundaries"]["week_key"] == "2026-11-16"
    assert result["summary"]["week_net_usdt"] == "10.18000000"
    assert result["summary"]["day_net_usdt"] == "0.20000000"
    assert result["summary"]["non_usdt_rows"] == 1
    assert result["current_usdt_wallet"] == "4998.74183608"
    assert result["day_wallet_candidate_usdt"] == "4998.54183608"
    assert result["week_wallet_candidate_usdt"] == "4988.56183608"
    assert result["actual_http_attempts"] == 8
    assert value["calls"].count("/fapi/v1/income") == 2
    assert snapshot(root) == before
    for flag in (
        "boundary_positions_verified",
        "equity_baseline_reconstructed",
        "history_completeness_independently_verified",
        "baseline_adopted",
        "orders_enabled",
        "live_trading_enabled",
    ):
        assert result[flag] is False
    parent = tmp_path / "data/WBKANG-DEMO-IncomeReads"
    for file in parent.rglob("*.json"):
        assert KEY not in file.read_text() and SECRET not in file.read_text()


@pytest.mark.parametrize("fault", ["wallet_change", "tail_event", "new_type"])
def test_unstable_or_unreviewed_evidence_does_not_offer_candidates(tmp_path, fault):
    root, _, pin = setup(tmp_path)
    value = run(tmp_path, root, pin, fault=fault)
    assert value["result"]["income_read"] == "INCOME_RESPONSES_SAVED_WALLET_CANDIDATES_ONLY", value
    assert value["result"]["day_wallet_candidate_usdt"] is None
    assert value["result"]["week_wallet_candidate_usdt"] is None


@pytest.mark.parametrize(
    "fault,missing",
    [
        ("week_boundary", "week_wallet_candidate_usdt"),
        ("day_boundary", "day_wallet_candidate_usdt"),
    ],
)
def test_boundary_events_withhold_the_ambiguous_boundary(tmp_path, fault, missing):
    root, _, pin = setup(tmp_path)
    value = run(tmp_path, root, pin, fault=fault)
    assert value["result"][missing] is None
    assert not value["result"]["baseline_adopted"]


def test_identical_duplicate_is_counted_once_and_cross_page_progress_is_verified(tmp_path):
    root, _, pin = setup(tmp_path)
    one = run(tmp_path, root, pin, fault="duplicate")
    assert one["result"]["duplicate_income_rows_ignored"] == 1
    assert one["result"]["summary"]["week_net_usdt"] == "10.18000000"
    two = run(tmp_path, root, pin, fault="two_pages")
    assert two["result"]["income_pagination_finished"]
    assert two["result"]["summary"]["unique_rows"] == 1001
    assert two["result"]["duplicate_income_rows_ignored"] == 1
    assert two["result"]["actual_http_attempts"] == 9


@pytest.mark.parametrize(
    "fault,code",
    [
        ("conflict", "INCOME_CONFLICTING_TRANSACTION"),
        ("full", "INCOME_PAGINATION_INCOMPLETE"),
        ("no_progress", "INCOME_PAGINATION_NO_PROGRESS"),
        ("bad_amount", "INCOME_AMOUNT_INVALID"),
        ("outside", "INCOME_ROW_OUTSIDE_WINDOW"),
        ("non_array", "READ_RESPONSE_NOT_ARRAY_SAVED_PRIVATELY"),
    ],
)
def test_partial_invalid_or_saturated_collection_retains_raw_and_offers_no_candidate(
    tmp_path, fault, code
):
    root, _, pin = setup(tmp_path)
    value = run(tmp_path, root, pin, fault=fault)
    assert value["result"]["failure_category"] == code, value
    assert value["result"]["day_wallet_candidate_usdt"] is None
    assert value["result"]["week_wallet_candidate_usdt"] is None
    assert value["result"]["actual_http_attempts"] <= 10
    assert value["calls"].count("/fapi/v1/income") <= 3
    assert list(
        (tmp_path / "data/WBKANG-DEMO-IncomeReads").glob("income-read-*/INCOME_PAGE_1.json")
    )


def test_empty_income_is_zero_not_one_and_current_position_does_not_prove_boundary(tmp_path):
    root, _, pin = setup(tmp_path)
    value = run(tmp_path, root, pin, fault="empty")
    assert value["result"]["summary"]["unique_rows"] == 0
    assert value["result"]["summary"]["week_net_usdt"] == "0.00000000"
    assert value["result"]["week_wallet_candidate_usdt"] == "4998.74183608"
    open_value = run(tmp_path, root, pin, fault="open_position")
    assert not open_value["result"]["positions_flat_at_snapshots"]
    assert not open_value["result"]["boundary_positions_verified"]
    assert not open_value["result"]["equity_baseline_reconstructed"]


@pytest.mark.parametrize(
    "fault,code",
    [
        ("anchor", "EXPORT_ACCOUNT_ANCHOR_MISMATCH"),
        ("headroom", "INCOME_INSUFFICIENT_WEIGHT_HEADROOM"),
        ("missing_weight", "INCOME_CURRENT_RATE_HEADER_REQUIRED"),
        ("leak", "EXPORT_RESPONSE_CONTAINS_CREDENTIAL_VALUE"),
    ],
)
def test_key_binding_weight_and_credential_protection_precede_account_requests(
    tmp_path, fault, code
):
    root, _, pin = setup(tmp_path)
    value = run(tmp_path, root, pin, fault=fault)
    assert value["result"]["failure_category"] == code, value
    assert "/fapi/v3/account" not in value["calls"]
    assert "/fapi/v1/income" not in value["calls"]


def test_native_decimal_and_configurable_kst_week_boundary(tmp_path):
    for name, data in package().items():
        (tmp_path / name).write_bytes(data)
    result = launch(
        tmp_path,
        f"""
. '{tmp_path}/READ_CORE.ps1'
. '{tmp_path}/INCOME_CORE.ps1'
[Globalization.CultureInfo]::CurrentCulture=[Globalization.CultureInfo]::GetCultureInfo('de-DE')
Income-Text ((Income-Decimal '0.10000000')+(Income-Decimal '0.20000000'))
Income-Boundaries 1794963600000 0|ConvertTo-Json -Compress
Income-Boundaries 1794963600000 6|ConvertTo-Json -Compress
""",
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "0.30000000"
    assert json.loads(lines[1])["week_key"] == "2026-11-16"
    assert json.loads(lines[2])["week_key"] == "2026-11-15"


@pytest.mark.parametrize(
    "path,params",
    [
        ("/fapi/v1/order/asyn", "@{}"),
        ("/fapi/v1/trade/asyn", "@{}"),
        ("/fapi/v1/allOrders", "@{}"),
        ("/fapi/v1/income", "@{startTime='1';endTime='1794963600000';page='1';limit='1000'}"),
        (
            "/fapi/v1/income",
            "@{startTime='1794754800000';endTime='1794963600000';page='4';limit='1000'}",
        ),
    ],
)
def test_unapproved_creation_route_or_query_range_is_denied(tmp_path, path, params):
    for name, data in package().items():
        (tmp_path / name).write_bytes(data)
    result = launch(
        tmp_path,
        f". '{tmp_path}/READ_CORE.ps1'\n. '{tmp_path}/READ_TRANSPORT.ps1'\ntry{{Export-ValidateRoute '{path}' {params};throw 'NOT_BLOCKED'}}catch{{if($_.Exception.Message -cne 'EXPORT_ROUTE_OR_PARAMETERS_FORBIDDEN'){{throw}};'BLOCKED'}}",
    )
    assert result.returncode == 0 and not result.stderr and result.stdout.strip() == "BLOCKED"


def test_offline_check_uses_backup_policy_and_no_credentials_or_writes(tmp_path):
    root, _, pin = setup(tmp_path)
    path = tmp_path / "RUN_INCOME.ps1"
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
    assert "INCOME_READ_CHECK_PASSED_NO_HTTP" in result.stdout
    assert snapshot(root) == before
    assert not (tmp_path / "data/WBKANG-DEMO-IncomeReads").exists()


def test_period_crossing_and_live_context_never_offer_a_candidate(tmp_path):
    root, _, pin = setup(tmp_path)
    changed = run(
        tmp_path,
        root,
        pin,
        extra="""
$script:stampIndex=0
function Income-Now {param($Context) $times=@(1794963600000L,1794963600001L,1795014000001L);$i=$script:stampIndex;$script:stampIndex++;return $times[[Math]::Min($i,2)]}
""",
    )
    assert changed["result"]["failure_category"] == "INCOME_PERIOD_CHANGED_DURING_COLLECTION"
    assert changed["result"]["day_wallet_candidate_usdt"] is None
    live = run(tmp_path, root, pin, extra="$env:LIVE_TRADING_ENABLED='true'")
    assert live["result"]["failure_category"] == "INCOME_LIVE_CONTEXT_FORBIDDEN"
    assert live["calls"] == []


def test_id_deduplication_scopes_type_and_conflicting_asset_is_not_silently_ignored(tmp_path):
    for name, data in package().items():
        (tmp_path / name).write_bytes(data)
    result = launch(
        tmp_path,
        f"""
. '{tmp_path}/READ_CORE.ps1'
. '{tmp_path}/ARRAY_CORE.ps1'
. '{tmp_path}/INCOME_CORE.ps1'
$rows=(Read-List '[{{"incomeType":"COMMISSION","tranId":1,"asset":"USDT","income":"-0.10000000","time":1794754801000}},{{"incomeType":"REALIZED_PNL","tranId":1,"asset":"USDT","income":"0.30000000","time":1794754801000}}]').rows
$seen=@{{}};$null=Income-AddRows $rows $seen 1794754800000 1794963600000
Income-Text (Income-Summary $seen (Income-Boundaries 1794963600000 0)).week_net
$other=(Read-List '[{{"incomeType":"COMMISSION","tranId":1,"asset":"USDC","income":"-0.10000000","time":1794754801000}}]').rows
try{{Income-AddRows $other $seen 1794754800000 1794963600000|Out-Null;throw 'NOT_BLOCKED'}}catch{{if($_.Exception.Message -cne 'INCOME_CONFLICTING_TRANSACTION'){{throw}};'ASSET_CONFLICT_BLOCKED'}}
""",
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["0.20000000", "ASSET_CONFLICT_BLOCKED"]


def test_decimal_rejects_nonnumeric_wrong_types_and_excess_precision(tmp_path):
    for name, data in package().items():
        (tmp_path / name).write_bytes(data)
    result = launch(
        tmp_path,
        f"""
. '{tmp_path}/READ_CORE.ps1'
. '{tmp_path}/INCOME_CORE.ps1'
$values=@('NaN','1e2','0.000000001','1000000000000',$null,$true,1.1)
foreach($value in $values){{try{{Income-Decimal $value|Out-Null;throw 'NOT_BLOCKED'}}catch{{if($_.Exception.Message -cne 'INCOME_AMOUNT_INVALID'){{throw}}}}}}
'INVALID_AMOUNT_INPUTS_BLOCKED'
""",
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert result.stdout.strip() == "INVALID_AMOUNT_INPUTS_BLOCKED"


@pytest.mark.parametrize("file", ["INCOME_CORE.ps1", "READ_TRANSPORT.ps1", "ARRAY_CORE.ps1"])
def test_sealed_launcher_detects_mutation_before_key_prompt(tmp_path, file):
    for name, data in package().items():
        (tmp_path / name).write_bytes(data)
    pin = digest((tmp_path / "TRANSFER_MANIFEST.json").read_bytes())
    (tmp_path / file).write_text("PRIVATE_MUST_NOT_RUN")
    result = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_INCOME.ps1' -ManifestSHA256 '{pin}' -CollectOnce\nexit $LASTEXITCODE",
    )
    assert result.returncode == 2 and not result.stderr and "PRIVATE_MUST" not in result.stdout
    assert json.loads(result.stdout)["failure_category"] == "INSPECT_TRANSFER_INVALID"


def test_production_acl_and_old_export_guard_are_preserved(tmp_path):
    root, _, pin = setup(tmp_path)
    old = tmp_path / "data/WBKANG-DEMO-Exports"
    old.mkdir()
    (old / "ORDER_EXPORT_ATTEMPT_20261117.json").write_text("UNRESOLVED_OLD_INTENT")
    before = snapshot(old)
    value = run(tmp_path, root, pin)
    assert value["result"]["export_jobs_created"] == 0
    assert snapshot(old) == before
    native = run(tmp_path, root, pin, production_storage=True)
    assert native["result"]["failure_category"] == "REFERENCE_WINDOWS_ACL_REQUIRED"
    assert native["calls"] == []
