"""Native PowerShell export job journal and signed GET tests with synthetic responses."""

import hashlib
import json
import os
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_trade_read_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch
from tests.unit.test_private_receipt_review_v2 import snapshot

pytestmark = pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
KEY = "synthetic-api-key-not-real"
SECRET = "synthetic-secret-not-real"


def digest(body):
    return hashlib.sha256(body).hexdigest()


def package():
    release = os.getenv("TRADE_READ_RELEASE_ZIP")
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
    from tests.unit.test_income_read_bundle import run as income_run
    from tests.unit.test_income_read_bundle import setup as income_setup

    root, folder, pin = income_setup(tmp_path)
    prior = income_run(tmp_path, root, pin, fault="empty")
    assert prior["result"]["income_read"] == "INCOME_RESPONSES_SAVED_WALLET_CANDIDATES_ONLY"
    for name, body in package().items():
        (tmp_path / name).write_bytes(body)
    parent = tmp_path / "data/WBKANG-DEMO-IncomeReads"
    return root, parent, pin


def run(tmp_path, root, pin, mode="RequestOnce", fault="", extra="", production_storage=False):
    storage = (
        ""
        if production_storage
        else """
function Reference-AssertPrivateDirectory {param($Path) if(-not [IO.Directory]::Exists($Path)){throw 'REFERENCE_TEST_DIRECTORY_MISSING'}}
function Reference-NewDirectory {param($Token) $p=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) ('WBKANG-DEMO-TradeReads/trade-read-20261118-'+$Token);$null=[IO.Directory]::CreateDirectory($p);return $p}
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
. '{tmp_path}/SAVED_CORE.ps1'
. '{tmp_path}/TRADE_CORE.ps1'
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
 '/fapi/v1/time' {{$reply=@{{serverTime=1794970800000L}}}}
 '/fapi/v1/exchangeInfo' {{$reply=@{{rateLimits=@(@{{rateLimitType='REQUEST_WEIGHT';interval='MINUTE';intervalNum=1;limit=2400}})}}}}
 '/fapi/v1/order' {{
  $id=[regex]::Match($url.Query,'orderId=([0-9]+)').Groups[1].Value
  $index=if($id -ceq '28668119491'){{0}}else{{1}}
  $reply=@{{symbol='BTCUSDT';orderId=[long]$id;clientOrderId=('PRIVATE_CLIENT_SENTINEL_'+$index);status='FILLED';side='BUY';type='LIMIT';positionSide='BOTH';reduceOnly=$false;origQty='0.0007';executedQty='0.0007';price='75725.10'}}
  if($script:fault -ceq 'anchor'){{$reply.clientOrderId='UNRELATED_ACCOUNT_ID'}}
 }}
 '/fapi/v1/userTrades' {{
  $start=[long][regex]::Match($url.Query,'startTime=([0-9]+)').Groups[1].Value
  $end=[long][regex]::Match($url.Query,'endTime=([0-9]+)').Groups[1].Value
  $reply=@()
  if($script:fault -in @('one','cancelled_net','later','outside','non_array')) {{
   $stamp=if($script:fault -ceq 'later'){{1794967200000L}}else{{1794927601000L}}
   $reply=@(@{{symbol='BTCUSDT';id=1;orderId=99;time=$stamp;side='BUY';positionSide='BOTH';qty='0.0007';price='65000.0';realizedPnl='0.25000000';commission='0.01000000';commissionAsset='USDT'}})
   if($script:fault -ceq 'cancelled_net'){{$reply[0].commission='0.00000000';$reply+=@{{symbol='BTCUSDT';id=2;orderId=100;time=$stamp;side='SELL';positionSide='BOTH';qty='0.0007';price='65000.0';realizedPnl='-0.25000000';commission='0.00000000';commissionAsset='USDT'}}}}
   if($script:fault -ceq 'outside'){{$reply[0].time=1794700000000L}}
   if($script:fault -ceq 'non_array'){{$reply=@{{code=200;msg='Unsupported trade list'}}}}
  }}
  if($script:fault -in @('split','saturated','conflict')) {{
   $all=@(1..1500|ForEach-Object {{@{{symbol='BTCUSDT';id=$_;orderId=($_+1000);time=(1794754800000L+[long][Math]::Floor((1794970800000L-1794754800000L)*$_/1501));side='BUY';positionSide='BOTH';qty='0.0007';price='65000.0';realizedPnl='0.00000000';commission='0.00000000';commissionAsset='USDT'}}}})
   if($script:fault -ceq 'saturated'){{$all|ForEach-Object {{$_.time=1794754800000L}}}}
   $reply=@($all|Where-Object {{$_.time -ge $start -and $_.time -le $end}}|Select-Object -First 1000)
   if($script:fault -ceq 'conflict' -and $end -lt 1794970800000L){{$reply|ForEach-Object {{$_.price='65001.0'}}}}
  }}
 }}
 default {{throw 'EXPORT_TEST_ROUTE_INVALID'}}
 }}
 if($script:fault -ceq 'clock' -and $url.AbsolutePath -ceq '/fapi/v1/time'){{$reply.serverTime=1}}
 if($script:fault -ceq 'long_window' -and $url.AbsolutePath -ceq '/fapi/v1/time'){{$reply.serverTime=1795363200000L}}
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
  if($script:fault -ceq 'timeout' -and $this.Uri.AbsolutePath -ceq '/fapi/v1/userTrades'){{throw (New-Object Net.WebException('SENSITIVE_ERROR_{SECRET}'))}}
  return $this.Response
 }}
 $request|Add-Member ScriptMethod Abort {{}}
 return $request
}}
$plan=Invoke-IncomePlan '{root}' '{pin}'
$parent=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) 'WBKANG-DEMO-IncomeReads'
$source=Get-SavedIncome $plan $parent
{extra}
try{{$result=Invoke-TradeRead $plan $source}}finally{{Trades-CloseSaved $source}}
[pscustomobject]@{{result=$result;calls=@($script:calls)}}|ConvertTo-Json -Depth 10 -Compress
"""
    result = launch(tmp_path, body)
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    for forbidden in (KEY, SECRET, "PRIVATE_CLIENT_SENTINEL", "PRIVATE_DOWNLOAD_URL"):
        assert forbidden not in result.stdout
    return json.loads(result.stdout)


def test_no_btc_fills_reuses_saved_income_and_never_claims_account_wide_proof(tmp_path):
    root, parent, pin = setup(tmp_path)
    before = snapshot(root), snapshot(parent)
    value = run(tmp_path, root, pin)
    result = value["result"]
    assert result["trade_read"] == "BTC_TRADE_RESPONSES_SAVED_NO_BASELINE_ADOPTION", value
    assert result["summary"]["unique_trade_rows"] == 0
    assert result["summary"]["no_btc_fills_through_old_cutoff"]
    assert result["summary"]["pnl_and_commission_zero_matches_saved_empty_income"]
    assert result["actual_http_attempts"] == 5
    assert value["calls"] == [
        "/fapi/v1/time",
        "/fapi/v1/exchangeInfo",
        "/fapi/v1/order",
        "/fapi/v1/order",
        "/fapi/v1/userTrades",
    ]
    assert result["saved_income_hashes_verified_before_after"]
    assert result["saved_wallet_candidate_usdt"] == "4998.74183608"
    assert result["operator_statement_as_of_kst_day"] == "2026-11-18"
    for flag in (
        "account_wide_trade_history_verified",
        "operator_statement_independently_verified",
        "operator_statement_extended_to_new_query_end",
        "boundary_positions_independently_verified",
        "equity_baseline_reconstructed",
        "history_completeness_independently_verified",
        "baseline_adopted",
        "orders_enabled",
        "live_trading_enabled",
    ):
        assert result[flag] is False
    assert before == (snapshot(root), snapshot(parent))
    for file in (tmp_path / "data/WBKANG-DEMO-TradeReads").rglob("*.json"):
        assert KEY not in file.read_text() and SECRET not in file.read_text()


@pytest.mark.parametrize("fault,count", [("one", 1), ("cancelled_net", 2)])
def test_nonzero_trade_cashflows_conflict_with_saved_empty_income_even_when_net_cancels(
    tmp_path, fault, count
):
    root, _, pin = setup(tmp_path)
    result = run(tmp_path, root, pin, fault=fault)["result"]
    assert result["summary"]["rows_through_saved_income_cutoff"] == count
    assert result["summary"]["nonzero_financial_trade_rows"] == count
    assert not result["summary"]["pnl_and_commission_zero_matches_saved_empty_income"]
    assert result["saved_wallet_candidate_usdt"] == "4998.74183608"
    assert not result["baseline_adopted"]
    if fault == "cancelled_net":
        assert result["summary"]["prior_window_realized_pnl_usdt"] == "0.00000000"


def test_later_fills_are_not_mixed_into_the_saved_income_window(tmp_path):
    root, _, pin = setup(tmp_path)
    result = run(tmp_path, root, pin, fault="later")["result"]
    assert result["summary"]["rows_through_saved_income_cutoff"] == 0
    assert result["summary"]["rows_after_saved_cutoff"] == 1
    assert not result["summary"]["no_btc_fills_in_returned_scope"]
    assert result["summary"]["no_btc_fills_through_old_cutoff"]
    assert not result["current_positions_refreshed"]


def test_one_level_time_split_completes_and_deduplicates_without_from_id_or_page(tmp_path):
    root, parent, pin = setup(tmp_path)
    before = snapshot(parent)
    result = run(tmp_path, root, pin, fault="split")["result"]
    assert result["trade_read"] == "BTC_TRADE_RESPONSES_SAVED_NO_BASELINE_ADOPTION", result
    assert result["actual_http_attempts"] == 7
    assert result["summary"]["unique_trade_rows"] == 1500
    assert result["endpoint_window_pagination_finished"]
    windows = result["response_windows"]
    assert len(windows) == 3
    assert windows[1]["end_ms"] + 1 == windows[2]["start_ms"]
    assert windows[0]["start_ms"] == windows[1]["start_ms"]
    assert windows[0]["end_ms"] == windows[2]["end_ms"]
    assert snapshot(parent) == before


@pytest.mark.parametrize(
    "fault,code",
    [
        ("saturated", "TRADES_SPLIT_WINDOW_INCOMPLETE"),
        ("conflict", "TRADES_CONFLICTING_ID"),
        ("outside", "TRADES_ROW_OUTSIDE_WINDOW"),
        ("non_array", "READ_RESPONSE_NOT_ARRAY_SAVED_PRIVATELY"),
    ],
)
def test_partial_or_invalid_trade_data_is_preserved_and_never_adopted(tmp_path, fault, code):
    root, parent, pin = setup(tmp_path)
    before = snapshot(parent)
    result = run(tmp_path, root, pin, fault=fault)["result"]
    assert result["failure_category"] == code, result
    assert result["actual_http_attempts"] <= 7
    assert not result["baseline_adopted"] and not result["endpoint_window_pagination_finished"]
    assert list((tmp_path / "data/WBKANG-DEMO-TradeReads").glob("trade-read-*/TRADES_ROOT.json"))
    assert snapshot(parent) == before


@pytest.mark.parametrize(
    "fault,code",
    [
        ("anchor", "EXPORT_ACCOUNT_ANCHOR_MISMATCH"),
        ("headroom", "TRADES_INSUFFICIENT_WEIGHT_HEADROOM"),
        ("missing_weight", "TRADES_CURRENT_RATE_HEADER_REQUIRED"),
        ("leak", "EXPORT_RESPONSE_CONTAINS_CREDENTIAL_VALUE"),
        ("timeout", "TRADES_RUN_BLOCKED"),
    ],
)
def test_preconditions_and_transport_errors_do_not_trigger_retry(tmp_path, fault, code):
    root, _, pin = setup(tmp_path)
    value = run(tmp_path, root, pin, fault=fault)
    assert value["result"]["trade_read"] == "STOPPED_PARTIAL_RECORDS_RETAINED_NO_AUTOMATIC_RETRY"
    if fault != "timeout":
        assert value["result"]["failure_category"] == code, value
        assert "/fapi/v1/userTrades" not in value["calls"]
    else:
        assert value["calls"].count("/fapi/v1/userTrades") == 1
    assert not value["result"]["automatic_retry"]


@pytest.mark.parametrize("fault", ["raw_hash", "window", "duplicate_run"])
def test_saved_income_link_rejects_changes_or_ambiguous_sources_before_credentials(tmp_path, fault):
    root, parent, pin = setup(tmp_path)
    folder = next(parent.glob("income-read-*"))
    if fault == "raw_hash":
        p = folder / "INCOME_PAGE_1.json"
        p.write_bytes(p.read_bytes() + b" ")
    elif fault == "window":
        p = folder / "WINDOW.json"
        data = json.loads(p.read_text())
        data["income_cutoff_ms"] += 86400000
        p.write_text(json.dumps(data))
    else:
        import shutil

        other = parent / ("income-read-20261118-" + "2" * 32)
        shutil.copytree(folder, other)
        data = json.loads((other / "RECEIPT.json").read_text())
        data["private_directory"] = str(other)
        (other / "RECEIPT.json").write_text(json.dumps(data))
    before = snapshot(parent)
    script = f"""
. '{tmp_path}/READ_CORE.ps1'
. '{tmp_path}/PLAN_CORE.ps1'
. '{tmp_path}/ARRAY_CORE.ps1'
. '{tmp_path}/READ_TRANSPORT.ps1'
. '{tmp_path}/INCOME_HELPERS.ps1'
. '{tmp_path}/SAVED_CORE.ps1'
function Reference-AssertPrivateDirectory {{param($Path)}}
function tar.exe {{& tar @args}}
$plan=Invoke-IncomePlan '{root}' '{pin}'
try{{$source=Get-SavedIncome $plan '{parent}';Trades-CloseSaved $source;throw 'NOT_BLOCKED'}}catch{{if($_.Exception.Message -cnotmatch '^TRADES_'){{throw}};'BLOCKED'}}
"""
    result = launch(tmp_path, script)
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert result.stdout.strip() == "BLOCKED" and snapshot(parent) == before


@pytest.mark.parametrize(
    "path,params",
    [
        ("/fapi/v1/order/asyn", "@{}"),
        ("/fapi/v1/income", "@{}"),
        ("/fapi/v3/account", "@{}"),
        (
            "/fapi/v1/userTrades",
            "@{symbol='ETHUSDT';startTime='1794754800000';endTime='1794970800000';limit='1000'}",
        ),
        (
            "/fapi/v1/userTrades",
            "@{symbol='BTCUSDT';fromId='1';startTime='1794754800000';endTime='1794970800000';limit='1000'}",
        ),
    ],
)
def test_scope_mutation_and_incompatible_pagination_parameters_are_forbidden(
    tmp_path, path, params
):
    for name, data in package().items():
        (tmp_path / name).write_bytes(data)
    result = launch(
        tmp_path,
        f". '{tmp_path}/READ_CORE.ps1'\n. '{tmp_path}/READ_TRANSPORT.ps1'\ntry{{Export-ValidateRoute '{path}' {params};throw 'NOT_BLOCKED'}}catch{{if($_.Exception.Message -cne 'EXPORT_ROUTE_OR_PARAMETERS_FORBIDDEN'){{throw}};'BLOCKED'}}",
    )
    assert result.returncode == 0 and not result.stderr and result.stdout.strip() == "BLOCKED"


def test_sealed_check_uses_saved_files_only_and_rejects_native_linux_acl(tmp_path):
    root, parent, pin = setup(tmp_path)
    path = tmp_path / "RUN_TRADES.ps1"
    path.write_text(
        path.read_text().replace(
            "2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d", pin
        )
    )
    acl = tmp_path / "PRIVATE_STORAGE.ps1"
    original_acl = acl.read_text()
    acl.write_text(
        "function Reference-AssertPrivateDirectory {param($Path) if(-not [IO.Directory]::Exists($Path)){throw 'REFERENCE_TEST_DIRECTORY_MISSING'}}"
    )
    manifest = json.loads((tmp_path / "TRANSFER_MANIFEST.json").read_bytes())
    for name in (path.name, acl.name):
        manifest["files"][name] = digest((tmp_path / name).read_bytes())
    body = json.dumps(manifest).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(body)
    before = snapshot(root), snapshot(parent)
    result = launch(
        tmp_path,
        f"function tar.exe {{& tar @args}}\n& '{path}' -ManifestSHA256 '{digest(body)}' -CheckOnly\nexit $LASTEXITCODE",
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert "TRADE_READ_CHECK_PASSED_NO_HTTP" in result.stdout
    assert before == (snapshot(root), snapshot(parent))
    acl.write_text(original_acl)
    result = launch(
        tmp_path,
        f". '{tmp_path}/READ_CORE.ps1'\n. '{acl}'\ntry{{Reference-AssertPrivateDirectory '{parent}';throw 'NOT_BLOCKED'}}catch{{if($_.Exception.Message -cne 'REFERENCE_WINDOWS_ACL_REQUIRED'){{throw}};'BLOCKED'}}",
    )
    assert result.returncode == 0 and not result.stderr and result.stdout.strip() == "BLOCKED"


@pytest.mark.parametrize("file", ["TRADE_CORE.ps1", "SAVED_CORE.ps1", "READ_TRANSPORT.ps1"])
def test_seal_blocks_tamper_before_credentials(tmp_path, file):
    for name, data in package().items():
        (tmp_path / name).write_bytes(data)
    pin = digest((tmp_path / "TRANSFER_MANIFEST.json").read_bytes())
    (tmp_path / file).write_text("PRIVATE_MUST_NOT_RUN")
    result = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_TRADES.ps1' -ManifestSHA256 '{pin}' -CollectOnce\nexit $LASTEXITCODE",
    )
    assert result.returncode == 2 and not result.stderr and "PRIVATE_MUST" not in result.stdout
    assert json.loads(result.stdout)["failure_category"] == "INSPECT_TRANSFER_INVALID"


def test_live_context_and_seven_day_limit_do_not_trigger_credential_or_trade_fallback(tmp_path):
    root, _, pin = setup(tmp_path)
    value = run(
        tmp_path,
        root,
        pin,
        extra="$env:LIVE_TRADING_ENABLED='true';function Export-Credentials {throw 'CREDENTIALS_MUST_NOT_RUN'}",
    )
    assert value["result"]["failure_category"] == "TRADES_LIVE_CONTEXT_FORBIDDEN"
    assert value["calls"] == []
    late = run(tmp_path, root, pin, fault="long_window")
    assert late["result"]["failure_category"] == "TRADES_SAVED_WINDOW_NOT_COVERED"
    assert "/fapi/v1/userTrades" not in late["calls"]
