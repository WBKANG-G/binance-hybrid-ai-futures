"""Native PowerShell export job journal and signed GET tests with synthetic responses."""

import hashlib
import json
import os
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_current_read_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch
from tests.unit.test_private_receipt_review_v2 import snapshot

pytestmark = pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
KEY = "synthetic-api-key-not-real"
SECRET = "synthetic-secret-not-real"


def digest(body):
    return hashlib.sha256(body).hexdigest()


def package():
    release = os.getenv("CURRENT_READ_RELEASE_ZIP")
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
    from tests.unit.test_trade_read_bundle import run as trade_run
    from tests.unit.test_trade_read_bundle import setup as trade_setup

    root, parent, pin = trade_setup(tmp_path)
    prior = trade_run(tmp_path, root, pin)
    assert prior["result"]["trade_read"] == "BTC_TRADE_RESPONSES_SAVED_NO_BASELINE_ADOPTION"
    for name, body in package().items():
        (tmp_path / name).write_bytes(body)
    return root, tmp_path / "data/WBKANG-DEMO-TradeReads", pin


def run(tmp_path, root, pin, mode="RequestOnce", fault="", extra="", production_storage=False):
    storage = (
        ""
        if production_storage
        else """
function Reference-AssertPrivateDirectory {param($Path) if(-not [IO.Directory]::Exists($Path)){throw 'REFERENCE_TEST_DIRECTORY_MISSING'}}
function Reference-NewDirectory {param($Token) $p=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) ('WBKANG-DEMO-CurrentReads/current-read-20261118-'+$Token);$null=[IO.Directory]::CreateDirectory($p);return $p}
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
. '{tmp_path}/INCOME_HELPERS.ps1'
. '{tmp_path}/SAVED_INCOME_CORE.ps1'
. '{tmp_path}/SAVED_TRADE_CORE.ps1'
. '{tmp_path}/TRADE_HELPERS.ps1'
. '{tmp_path}/CURRENT_CORE.ps1'
function tar.exe {{& tar @args}}
{storage}
function Export-Credentials {{return [pscustomobject]@{{key='{KEY}';secret='{SECRET}'}}}}
$script:calls=New-Object Collections.ArrayList
$script:fault='{fault}'
$script:accountCalls=0
$script:modeCalls=0
$script:configCalls=0
function Export-NewWebRequest {{param($Uri)
 $url=[Uri]$Uri
 if($url.Host -cne 'demo-fapi.binance.com' -or $url.Scheme -cne 'https'){{throw 'EXPORT_TEST_HOST_INVALID'}}
 $reply=@{{}}
 switch($url.AbsolutePath) {{
 '/fapi/v1/time' {{$reply=@{{serverTime=1794978000000L}}}}
 '/fapi/v1/exchangeInfo' {{$reply=@{{rateLimits=@(@{{rateLimitType='REQUEST_WEIGHT';interval='MINUTE';intervalNum=1;limit=2400}})}}}}
 '/fapi/v1/order' {{
  $id=[regex]::Match($url.Query,'orderId=([0-9]+)').Groups[1].Value
  $index=if($id -ceq '28668119491'){{0}}else{{1}}
  $reply=@{{symbol='BTCUSDT';orderId=[long]$id;clientOrderId=('PRIVATE_CLIENT_SENTINEL_'+$index);status='FILLED';side='BUY';type='LIMIT';positionSide='BOTH';reduceOnly=$false;origQty='0.0007';executedQty='0.0007';price='75725.10'}}
  if($script:fault -ceq 'anchor'){{$reply.clientOrderId='UNRELATED_ACCOUNT_ID'}}
 }}
 '/fapi/v1/accountConfig' {{
  $script:modeCalls++
  $reply=@{{dualSidePosition=$false;multiAssetsMargin=$false;canTrade=$true}}
  if($script:fault -ceq 'hedge' -or ($script:fault -ceq 'mode_change' -and $script:modeCalls -eq 2)){{$reply.dualSidePosition=$true}}
  if($script:fault -ceq 'multi'){{$reply.multiAssetsMargin=$true}}
 }}
 '/fapi/v1/symbolConfig' {{
  $reply=@(@{{symbol='BTCUSDT';leverage=2;marginType='ISOLATED';isAutoAddMargin='false'}})
  if($script:fault -ceq 'leverage'){{$reply[0].leverage=3}}
 }}
 '/fapi/v1/openOrders' {{
  if($url.Query -match 'symbol='){{throw 'CURRENT_TEST_FILTERED_ACCOUNT_ORDERS'}}
  $reply=@()
  if($script:fault -ceq 'regular'){{$reply=@(@{{symbol='ETHUSDT';clientOrderId='PRIVATE_OPEN_ORDER_SENTINEL';status='NEW'}})}}
  if($script:fault -ceq 'non_array'){{$reply=@{{code=200;msg='Unsupported order list'}}}}
 }}
 '/fapi/v1/openAlgoOrders' {{
  if($url.Query -match 'symbol='){{throw 'CURRENT_TEST_FILTERED_ACCOUNT_ALGOS'}}
  $reply=@()
  if($script:fault -ceq 'algo'){{$reply=@(@{{symbol='ETHUSDT';clientAlgoId='PRIVATE_OPEN_ALGO_SENTINEL';algoStatus='NEW'}})}}
 }}
 '/fapi/v3/positionRisk' {{
  if($url.Query -match 'symbol='){{throw 'CURRENT_TEST_FILTERED_ACCOUNT_POSITIONS'}}
  $reply=@(@{{symbol='BTCUSDT';positionSide='BOTH';positionAmt='0.00000000'}})
  if($script:fault -ceq 'position'){{$reply+=@{{symbol='ETHUSDT';positionSide='BOTH';positionAmt='0.01000000'}}}}
  if($script:fault -ceq 'hedge_rows'){{$reply[0].positionSide='LONG'}}
 }}
 '/fapi/v3/account' {{
  $script:accountCalls++
  $wallet=if($script:fault -ceq 'wallet_change' -and $script:accountCalls -eq 2){{'4999.74183608'}}else{{'4998.74183608'}}
  $reply=@{{assets=@(@{{asset='USDC';walletBalance='5000.00000000';unrealizedProfit='0.00000000'}},@{{asset='USDT';walletBalance=$wallet;unrealizedProfit='0.00000000';availableBalance=$wallet;marginBalance=$wallet}});positions=@(@{{symbol='BTCUSDT';positionAmt='0.00000000'}});totalInitialMargin='0.00000000';totalUnrealizedProfit='0.00000000'}}
  if($script:fault -ceq 'margin'){{$reply.totalInitialMargin='1.00000000'}}
 }}
 '/fapi/v1/income' {{
  $start=[long][regex]::Match($url.Query,'startTime=([0-9]+)').Groups[1].Value
  $reply=@()
  if($script:fault -ceq 'income_gap'){{$reply=@(@{{incomeType='TRANSFER';tranId=1;asset='USDT';income='0.00000000';time=$start;symbol=''}})}}
  if($script:fault -ceq 'gap_full'){{$reply=@(1..1000|ForEach-Object {{@{{incomeType='COMMISSION';tranId=$_;asset='USDT';income='0.00000000';time=($start+$_);symbol='BTCUSDT'}}}})}}
 }}
 '/fapi/v1/userTrades' {{
  $start=[long][regex]::Match($url.Query,'startTime=([0-9]+)').Groups[1].Value
  $reply=@()
  if($script:fault -ceq 'trade_gap'){{$reply=@(@{{symbol='BTCUSDT';id=1;orderId=99;time=$start;side='BUY';positionSide='BOTH';qty='0.0007';price='65000.0';realizedPnl='0.00000000';commission='0.00000000';commissionAsset='USDT'}})}}
 }}
 default {{throw 'EXPORT_TEST_ROUTE_INVALID'}}
 }}
 if($script:fault -ceq 'clock' -and $url.AbsolutePath -ceq '/fapi/v1/time'){{$reply.serverTime=1}}
 if($script:fault -ceq 'next_day' -and $url.AbsolutePath -ceq '/fapi/v1/time'){{$reply.serverTime=1795050000000L}}
 if($script:fault -ceq 'long_window' -and $url.AbsolutePath -ceq '/fapi/v1/time'){{$reply.serverTime=1795363200000L}}
 if($script:fault -ceq 'leak'){{$reply.secret='{SECRET}'}}
 $headers=New-Object Collections.Specialized.NameValueCollection
 $headers.Add('X-MBX-USED-WEIGHT-1M',$(if($script:fault -ceq 'headroom'){{'2000'}}else{{'20'}}))
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
  if($script:fault -ceq 'timeout' -and $this.Uri.AbsolutePath -ceq '/fapi/v1/userTrades'){{throw (New-Object Net.WebException('SENSITIVE_ERROR_{SECRET}'))}}
  return $this.Response
 }}
 $request|Add-Member ScriptMethod Abort {{}}
 return $request
}}
$plan=Invoke-IncomePlan '{root}' '{pin}'
$parent=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) 'WBKANG-DEMO-IncomeReads'
$source=Get-SavedIncome $plan $parent
$tradeParent=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) 'WBKANG-DEMO-TradeReads'
Add-SavedTrade $source $plan $tradeParent
{extra}
try{{$result=Invoke-CurrentRead $plan $source}}finally{{Trades-CloseSaved $source}}
[pscustomobject]@{{result=$result;calls=@($script:calls)}}|ConvertTo-Json -Depth 10 -Compress
"""
    result = launch(tmp_path, body)
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    for forbidden in (
        KEY,
        SECRET,
        "PRIVATE_CLIENT_SENTINEL",
        "PRIVATE_DOWNLOAD_URL",
        "PRIVATE_OPEN_ORDER_SENTINEL",
        "PRIVATE_OPEN_ALGO_SENTINEL",
    ):
        assert forbidden not in result.stdout
    return json.loads(result.stdout)


def test_current_settings_and_account_wide_opens_are_observed_without_changes(tmp_path):
    root, trade_parent, pin = setup(tmp_path)
    income_parent = tmp_path / "data/WBKANG-DEMO-IncomeReads"
    before = snapshot(root), snapshot(trade_parent), snapshot(income_parent)
    value = run(tmp_path, root, pin)
    result = value["result"]
    assert result["current_read"] == "CURRENT_ACCOUNT_OBSERVATIONS_SAVED_NO_CHANGES", value
    assert result["actual_http_attempts"] == 18
    assert result["summary"]["observations_consistent_with_expected_flat_profile"], result
    assert result["summary"]["issues"] == []
    assert result["summary"]["mode_before"]["one_way"]
    assert result["summary"]["mode_after"]["single_asset"]
    assert result["summary"]["btc_config_before"]["leverage"] == 2
    assert result["saved_source_hashes_verified_before_after"]
    assert result["non_atomic_read_sequence"]
    for flag in (
        "settings_changed_by_tool",
        "baseline_adopted",
        "historic_requests_resolved",
        "equity_baseline_reconstructed",
        "account_wide_historical_trade_history_verified",
        "execution_approved",
        "orders_enabled",
        "live_trading_enabled",
    ):
        assert result[flag] is False
    for path in (
        "/fapi/v1/openOrders",
        "/fapi/v1/openAlgoOrders",
        "/fapi/v3/positionRisk",
        "/fapi/v3/account",
        "/fapi/v1/accountConfig",
        "/fapi/v1/symbolConfig",
    ):
        assert value["calls"].count(path) == 2
    assert before == (snapshot(root), snapshot(trade_parent), snapshot(income_parent))
    for file in (tmp_path / "data/WBKANG-DEMO-CurrentReads").rglob("*.json"):
        assert KEY not in file.read_text() and SECRET not in file.read_text()


@pytest.mark.parametrize(
    "fault,issue",
    [
        ("regular", "REGULAR_OPEN_ORDERS_PRESENT"),
        ("algo", "ALGO_OPEN_ORDERS_PRESENT"),
        ("position", "CURRENT_POSITIONS_PRESENT"),
        ("hedge_rows", "POSITION_RISK_HEDGE_SIDE_ROWS"),
        ("hedge", "ACCOUNT_HEDGE_MODE"),
        ("multi", "ACCOUNT_MULTI_ASSET_MODE"),
        ("leverage", "BTC_SETTINGS_DO_NOT_MATCH_ISOLATED_2X_NO_AUTO_ADD"),
        ("margin", "MARGIN_OR_USDT_BALANCE_NOT_FLAT_UNENCUMBERED"),
        ("wallet_change", "CURRENT_WALLET_DIFFERS_FROM_SAVED_CANDIDATE"),
        ("mode_change", "SETTINGS_CHANGED_DURING_READ"),
        ("income_gap", "INCOME_ACTIVITY_GAP_NOT_EMPTY"),
        ("trade_gap", "BTC_TRADE_ACTIVITY_GAP_NOT_EMPTY"),
        ("gap_full", "ACTIVITY_GAP_SATURATED_NO_COMPLETE_COVERAGE"),
    ],
)
def test_observed_mismatch_is_reported_without_changing_settings_or_orders(tmp_path, fault, issue):
    root, _, pin = setup(tmp_path)
    value = run(tmp_path, root, pin, fault=fault)
    result = value["result"]
    assert result["current_read"] == "CURRENT_ACCOUNT_OBSERVATIONS_SAVED_NO_CHANGES", value
    assert issue in result["summary"]["issues"], result
    assert not result["summary"]["observations_consistent_with_expected_flat_profile"]
    assert not result["settings_changed_by_tool"] and result["order_writes"] == 0
    if fault == "regular":
        assert result["summary"]["rounds"][0]["regular_orders"]["non_btc_count"] == 1
    if fault == "algo":
        assert result["summary"]["rounds"][0]["algo_orders"]["non_btc_count"] == 1
    if fault == "position":
        assert result["summary"]["rounds"][0]["positions"]["nonzero_non_btc_positions"] == 1


def test_non_array_order_response_is_saved_before_schema_failure(tmp_path):
    root, _, pin = setup(tmp_path)
    value = run(tmp_path, root, pin, fault="non_array")
    assert value["result"]["failure_category"] == "READ_RESPONSE_NOT_ARRAY_SAVED_PRIVATELY", value
    saved = list(
        (tmp_path / "data/WBKANG-DEMO-CurrentReads").glob("current-read-*/REGULAR_OPEN_1.json")
    )
    assert len(saved) == 1 and json.loads(saved[0].read_text())["msg"] == "Unsupported order list"
    assert not value["result"]["automatic_retry"]


@pytest.mark.parametrize(
    "fault,code",
    [
        ("anchor", "EXPORT_ACCOUNT_ANCHOR_MISMATCH"),
        ("headroom", "CURRENT_INSUFFICIENT_WEIGHT_HEADROOM"),
        ("missing_weight", "CURRENT_RATE_HEADER_REQUIRED"),
        ("leak", "EXPORT_RESPONSE_CONTAINS_CREDENTIAL_VALUE"),
    ],
)
def test_preconditions_prevent_current_account_collection(tmp_path, fault, code):
    root, _, pin = setup(tmp_path)
    value = run(tmp_path, root, pin, fault=fault)
    assert value["result"]["failure_category"] == code, value
    assert "/fapi/v1/accountConfig" not in value["calls"]


@pytest.mark.parametrize(
    "path,params",
    [
        ("/fapi/v1/order/asyn", "@{}"),
        ("/fapi/v1/openOrders", "@{symbol='BTCUSDT'}"),
        ("/fapi/v1/openAlgoOrders", "@{symbol='BTCUSDT'}"),
        ("/fapi/v3/positionRisk", "@{symbol='BTCUSDT'}"),
        ("/fapi/v1/symbolConfig", "@{symbol='ETHUSDT'}"),
    ],
)
def test_creation_or_filtered_account_scope_is_forbidden(tmp_path, path, params):
    for name, data in package().items():
        (tmp_path / name).write_bytes(data)
    result = launch(
        tmp_path,
        f". '{tmp_path}/READ_CORE.ps1'\n. '{tmp_path}/READ_TRANSPORT.ps1'\ntry{{Export-ValidateRoute '{path}' {params};throw 'NOT_BLOCKED'}}catch{{if($_.Exception.Message -cne 'EXPORT_ROUTE_OR_PARAMETERS_FORBIDDEN'){{throw}};'BLOCKED'}}",
    )
    assert result.returncode == 0 and not result.stderr and result.stdout.strip() == "BLOCKED"


def test_saved_trade_hash_change_is_blocked_before_key_access(tmp_path):
    root, parent, pin = setup(tmp_path)
    folder = next(parent.glob("trade-read-*"))
    path = folder / "TRADES_ROOT.json"
    path.write_bytes(path.read_bytes() + b" ")
    before = snapshot(parent)
    result = launch(
        tmp_path,
        f"""
. '{tmp_path}/READ_CORE.ps1'
. '{tmp_path}/PLAN_CORE.ps1'
. '{tmp_path}/ARRAY_CORE.ps1'
. '{tmp_path}/READ_TRANSPORT.ps1'
. '{tmp_path}/INCOME_HELPERS.ps1'
. '{tmp_path}/SAVED_INCOME_CORE.ps1'
. '{tmp_path}/SAVED_TRADE_CORE.ps1'
function Reference-AssertPrivateDirectory {{param($Path)}}
function tar.exe {{& tar @args}}
$plan=Invoke-IncomePlan '{root}' '{pin}'
$source=Get-SavedIncome $plan '{tmp_path}/data/WBKANG-DEMO-IncomeReads'
try{{Add-SavedTrade $source $plan '{parent}';throw 'NOT_BLOCKED'}}catch{{if($_.Exception.Message -cne 'CURRENT_SAVED_TRADE_HASH_OR_ROWS_INVALID'){{throw}};'BLOCKED'}}finally{{Trades-CloseSaved $source}}
""",
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert result.stdout.strip() == "BLOCKED" and snapshot(parent) == before


def test_sealed_offline_check_links_both_old_runs_and_writes_nothing(tmp_path):
    root, parent, pin = setup(tmp_path)
    path = tmp_path / "RUN_CURRENT.ps1"
    path.write_text(
        path.read_text().replace(
            "2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d", pin
        )
    )
    acl = tmp_path / "PRIVATE_STORAGE.ps1"
    acl.write_text(
        "function Reference-AssertPrivateDirectory {param($Path) if(-not [IO.Directory]::Exists($Path)){throw 'REFERENCE_TEST_DIRECTORY_MISSING'}}"
    )
    manifest = json.loads((tmp_path / "TRANSFER_MANIFEST.json").read_bytes())
    for name in (path.name, acl.name):
        manifest["files"][name] = digest((tmp_path / name).read_bytes())
    raw = json.dumps(manifest).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(raw)
    before = snapshot(root), snapshot(parent)
    result = launch(
        tmp_path,
        f"function tar.exe {{& tar @args}}\n& '{path}' -ManifestSHA256 '{digest(raw)}' -CheckOnly\nexit $LASTEXITCODE",
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert "CURRENT_READ_CHECK_PASSED_NO_HTTP" in result.stdout
    assert before == (snapshot(root), snapshot(parent))
    assert not (tmp_path / "data/WBKANG-DEMO-CurrentReads").exists()


@pytest.mark.parametrize("file", ["CURRENT_CORE.ps1", "SAVED_TRADE_CORE.ps1", "READ_TRANSPORT.ps1"])
def test_seal_refuses_changes_before_credentials(tmp_path, file):
    for name, data in package().items():
        (tmp_path / name).write_bytes(data)
    pin = digest((tmp_path / "TRANSFER_MANIFEST.json").read_bytes())
    (tmp_path / file).write_text("PRIVATE_DO_NOT_RUN")
    result = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_CURRENT.ps1' -ManifestSHA256 '{pin}' -CollectOnce\nexit $LASTEXITCODE",
    )
    assert result.returncode == 2 and not result.stderr and "PRIVATE_DO_NOT" not in result.stdout
    assert json.loads(result.stdout)["failure_category"] == "INSPECT_TRANSFER_INVALID"


def test_live_context_and_new_day_stop_before_current_queries(tmp_path):
    root, _, pin = setup(tmp_path)
    live = run(
        tmp_path,
        root,
        pin,
        extra="$env:LIVE_TRADING_ENABLED='true';function Export-Credentials {throw 'CREDENTIALS_MUST_NOT_RUN'}",
    )
    assert live["result"]["failure_category"] == "CURRENT_LIVE_CONTEXT_FORBIDDEN"
    assert live["calls"] == []
    late = run(tmp_path, root, pin, fault="next_day")
    assert late["result"]["failure_category"] == "CURRENT_SAVED_PERIOD_OR_WINDOW_STALE"
    assert late["calls"] == ["/fapi/v1/time"]


def test_midnight_crossing_and_transport_failure_preserve_partial_evidence_without_retry(tmp_path):
    root, _, pin = setup(tmp_path)
    crossed = run(
        tmp_path,
        root,
        pin,
        extra="""
$script:clockIndex=0
function Income-Now {param($Context) $values=@(1794978000000L,1795014000001L);$index=$script:clockIndex;$script:clockIndex++;return $values[[Math]::Min($index,1)]}
""",
    )
    assert crossed["result"]["failure_category"] == "CURRENT_PERIOD_CHANGED_DURING_READ"
    assert not crossed["result"]["baseline_adopted"]
    failed = run(tmp_path, root, pin, fault="timeout")
    assert failed["result"]["current_read"] == "STOPPED_PARTIAL_RECORDS_RETAINED_NO_AUTOMATIC_RETRY"
    assert failed["calls"].count("/fapi/v1/userTrades") == 1
    assert not failed["result"]["automatic_retry"]
    assert list((tmp_path / "data/WBKANG-DEMO-CurrentReads").glob("current-read-*/ACCOUNT_2.json"))
