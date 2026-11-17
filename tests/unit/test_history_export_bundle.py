"""Native PowerShell export job journal and signed GET tests with synthetic responses."""

import hashlib
import hmac
import json
import os
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_history_export_bundle as builder
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
    release = os.getenv("HISTORY_EXPORT_RELEASE_ZIP")
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
function Reference-NewDirectory {param($Token) $p=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) ('WBKANG-DEMO-Exports/history-export-20261117-'+$Token);$null=[IO.Directory]::CreateDirectory($p);return $p}
"""
    )
    # Transport mock retains the real route validation, signature, timeout, response size,
    # credential leak rejection and budget code. No production host is contacted.
    body = f"""
. '{tmp_path}/READ_CORE.ps1'
. '{tmp_path}/PRIVATE_STORAGE.ps1'
. '{tmp_path}/PLAN_CORE.ps1'
. '{tmp_path}/EXPORT_CORE.ps1'
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
 '/fapi/v1/order/asyn' {{$reply=@{{downloadId='synthetic-job-123';avgCostTimestampOfLast30d=100}}}}
 '/fapi/v1/order/asyn/id' {{$reply=@{{downloadId='synthetic-job-123';status='completed';url='https://example.invalid/PRIVATE_DOWNLOAD_URL'}}}}
 default {{throw 'EXPORT_TEST_ROUTE_INVALID'}}
 }}
 if($script:fault -ceq 'clock' -and $url.AbsolutePath -ceq '/fapi/v1/time'){{$reply.serverTime=1}}
 if($script:fault -ceq 'missing_id' -and $url.AbsolutePath -ceq '/fapi/v1/order/asyn'){{$reply=@{{code=-1}}}}
 if($script:fault -ceq 'numeric_id' -and $url.AbsolutePath -ceq '/fapi/v1/order/asyn'){{$reply=@{{downloadId=123456789}}}}
 if($script:fault -ceq 'wrong_status_id' -and $url.AbsolutePath -ceq '/fapi/v1/order/asyn/id'){{$reply.downloadId='other-job'}}
 if($script:fault -ceq 'leak'){{$reply.secret='{SECRET}'}}
 $headers=New-Object Collections.Specialized.NameValueCollection
 $headers.Add('X-MBX-USED-WEIGHT-1M',$(if($script:fault -ceq 'headroom'){{'2000'}}else{{'20'}}))
 if($script:fault -ceq 'missing_weight'){{$headers.Remove('X-MBX-USED-WEIGHT-1M')}}
 $response=[pscustomobject]@{{StatusCode=200;Headers=$headers;Text=($reply|ConvertTo-Json -Depth 8 -Compress)}}
 $response|Add-Member ScriptMethod GetResponseStream {{return New-Object IO.MemoryStream(,[Text.Encoding]::UTF8.GetBytes($this.Text))}}
 $response|Add-Member ScriptMethod Dispose {{}}
 $request=[pscustomobject]@{{Method='';AllowAutoRedirect=$true;KeepAlive=$true;Timeout=0;ReadWriteTimeout=0;Headers=(New-Object Collections.Specialized.NameValueCollection);Uri=$url;Response=$response}}
 $request|Add-Member ScriptMethod GetResponse {{
  $null=$script:calls.Add($this.Uri.AbsolutePath)
  if($this.Method -cne 'GET' -or $this.AllowAutoRedirect -or $this.Timeout -ne 10000){{throw 'EXPORT_TEST_REQUEST_POLICY_INVALID'}}
  $isPublic=$this.Uri.AbsolutePath -in @('/fapi/v1/time','/fapi/v1/exchangeInfo')
  if($isPublic){{if($null -ne $this.Headers['X-MBX-APIKEY']){{throw 'EXPORT_TEST_PUBLIC_KEY_LEAK'}}}}else{{if($this.Headers['X-MBX-APIKEY'] -cne '{KEY}'){{throw 'EXPORT_TEST_SIGNED_KEY_MISSING'}}}}
  if($this.Uri.Query.Contains('{KEY}') -or $this.Uri.Query.Contains('{SECRET}')){{throw 'EXPORT_TEST_URI_SECRET_LEAK'}}
  if($script:fault -ceq 'timeout' -and $this.Uri.AbsolutePath -ceq '/fapi/v1/order/asyn'){{throw (New-Object Net.WebException('SENSITIVE_ERROR_{SECRET}'))}}
  return $this.Response
 }}
 $request|Add-Member ScriptMethod Abort {{}}
 return $request
}}
$plan=Invoke-ExportPlan '{root}' '{pin}'
{extra}
$result=Invoke-HistoryExport $plan '{mode}'
[pscustomobject]@{{result=$result;calls=@($script:calls)}}|ConvertTo-Json -Depth 10 -Compress
"""
    result = launch(tmp_path, body)
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    for forbidden in (KEY, SECRET, "PRIVATE_CLIENT_SENTINEL", "PRIVATE_DOWNLOAD_URL"):
        assert forbidden not in result.stdout
    return json.loads(result.stdout)


def test_one_job_and_status_preserve_sources_and_refuse_second_creation(tmp_path):
    root, _, pin = setup(tmp_path)
    before = snapshot(root)
    first = run(tmp_path, root, pin)
    assert first["result"]["history_export"] == "ONE_EXPORT_JOB_RECORDED_NO_ARCHIVE_DOWNLOADED", (
        first
    )
    assert first["calls"].count("/fapi/v1/order/asyn") == 1
    assert first["result"]["actual_http_attempts"] == 5
    repeated = run(tmp_path, root, pin)
    assert repeated["result"]["failure_category"] == "EXPORT_PRIOR_ATTEMPT_REVIEW_REQUIRED"
    assert repeated["calls"] == []
    status = run(tmp_path, root, pin, mode="StatusOnce")
    assert (
        status["result"]["history_export"] == "ONE_EXISTING_JOB_STATUS_SAVED_NO_ARCHIVE_DOWNLOADED"
    ), status
    assert status["calls"] == ["/fapi/v1/time", "/fapi/v1/order/asyn/id"]
    assert snapshot(root) == before
    parent = tmp_path / "data/WBKANG-DEMO-Exports"
    for file in parent.rglob("*.json"):
        assert KEY not in file.read_text() and SECRET not in file.read_text()
    assert not first["result"]["orders_enabled"] and not first["result"]["baseline_adopted"]


@pytest.mark.parametrize(
    "fault,code",
    [
        ("anchor", "EXPORT_ACCOUNT_ANCHOR_MISMATCH"),
        ("headroom", "EXPORT_INSUFFICIENT_WEIGHT_HEADROOM"),
        ("missing_weight", "EXPORT_CURRENT_RATE_HEADER_REQUIRED"),
        ("leak", "EXPORT_RESPONSE_CONTAINS_CREDENTIAL_VALUE"),
        ("clock", "EXPORT_SERVER_CLOCK_INVALID"),
    ],
)
def test_preconditions_refuse_before_job_creation(tmp_path, fault, code):
    root, _, pin = setup(tmp_path)
    value = run(tmp_path, root, pin, fault=fault)
    assert value["result"]["failure_category"] == code, value
    assert "/fapi/v1/order/asyn" not in value["calls"]
    assert not value["result"]["export_creation_attempted"]


@pytest.mark.parametrize("fault", ["timeout", "missing_id"])
def test_ambiguous_creation_is_durably_blocked_from_regeneration(tmp_path, fault):
    root, _, pin = setup(tmp_path)
    first = run(tmp_path, root, pin, fault=fault)
    assert first["result"]["history_export"] == "STOPPED_NO_AUTOMATIC_RETRY"
    assert first["result"]["export_creation_attempted"]
    assert first["calls"].count("/fapi/v1/order/asyn") == 1
    second = run(tmp_path, root, pin)
    assert second["calls"] == [] and not second["result"]["export_creation_attempted"]


def test_live_context_refused_before_credentials_or_io(tmp_path):
    root, _, pin = setup(tmp_path)
    value = run(
        tmp_path,
        root,
        pin,
        extra="$env:LIVE_TRADING_ENABLED='true';function Export-Credentials {throw 'CREDENTIALS_MUST_NOT_RUN'}",
    )
    assert value["result"]["failure_category"] == "EXPORT_LIVE_CONTEXT_FORBIDDEN"
    assert value["calls"] == []


def test_windows_acl_is_required_without_test_substitution(tmp_path):
    root, _, pin = setup(tmp_path)
    value = run(tmp_path, root, pin, production_storage=True)
    assert value["result"]["failure_category"] == "REFERENCE_WINDOWS_ACL_REQUIRED"
    assert value["calls"] == []


def test_native_hmac_matches_standard_vector_and_rejects_expired_session(tmp_path):
    for name, data in package().items():
        (tmp_path / name).write_bytes(data)
    result = launch(
        tmp_path,
        f"""
. '{tmp_path}/READ_CORE.ps1'
. '{tmp_path}/EXPORT_CORE.ps1'
$c=[pscustomobject]@{{watch=[pscustomobject]@{{ElapsedMilliseconds=1000}};server_time=1794877200000L;clock_observed_ms=0;credentials=[pscustomobject]@{{secret='{SECRET}'}}}}
Export-SignedQuery $c @{{symbol='BTCUSDT';orderId='123'}}
$c.watch.ElapsedMilliseconds=60000
try {{Export-SignedQuery $c @{{}}}}catch{{'EXPECTED_EXPIRED'}}
""",
    )
    assert result.returncode == 0 and not result.stderr
    unsigned = "orderId=123&recvWindow=5000&symbol=BTCUSDT&timestamp=1794877201000"
    expected = hmac.new(SECRET.encode(), unsigned.encode(), hashlib.sha256).hexdigest()
    assert result.stdout.splitlines() == [unsigned + "&signature=" + expected, "EXPECTED_EXPIRED"]


@pytest.mark.parametrize(
    "path,params",
    [
        ("/fapi/v1/order", "@{symbol='BTCUSDT';side='BUY'}"),
        ("/fapi/v1/trade/asyn", "@{}"),
        ("https://fapi.binance.com/fapi/v1/order", "@{}"),
        ("/fapi/v1/order/asyn", "@{startTime='1';endTime='1794877200000'}"),
    ],
)
def test_route_parameter_bypass_blocked_before_network(tmp_path, path, params):
    for name, data in package().items():
        (tmp_path / name).write_bytes(data)
    result = launch(
        tmp_path,
        f". '{tmp_path}/READ_CORE.ps1'\n. '{tmp_path}/EXPORT_CORE.ps1'\ntry{{Export-ValidateRoute '{path}' {params};throw 'NOT_BLOCKED'}}catch{{if($_.Exception.Message -cne 'EXPORT_ROUTE_OR_PARAMETERS_FORBIDDEN'){{throw}};'BLOCKED'}}",
    )
    assert result.returncode == 0 and not result.stderr and result.stdout.strip() == "BLOCKED"


@pytest.mark.parametrize("file", ["EXPORT_CORE.ps1", "PLAN_CORE.ps1", "PRIVATE_STORAGE.ps1"])
def test_sealed_launcher_denies_tamper_before_credentials(tmp_path, file):
    for name, data in package().items():
        (tmp_path / name).write_bytes(data)
    pin = digest((tmp_path / "TRANSFER_MANIFEST.json").read_bytes())
    (tmp_path / file).write_text("SENSITIVE_MUST_NOT_RUN")
    result = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_EXPORT.ps1' -ManifestSHA256 '{pin}' -RequestExportOnce\nexit $LASTEXITCODE",
    )
    assert result.returncode == 2 and not result.stderr and "SENSITIVE" not in result.stdout
    assert json.loads(result.stdout)["failure_category"] == "INSPECT_TRANSFER_INVALID"


def test_check_command_reads_only_pinned_sources(tmp_path):
    root, _, pin = setup(tmp_path)
    path = tmp_path / "RUN_EXPORT.ps1"
    path.write_text(
        path.read_text().replace(
            "2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d", pin
        )
    )
    manifest = json.loads((tmp_path / "TRANSFER_MANIFEST.json").read_bytes())
    manifest["files"][path.name] = digest(path.read_bytes())
    data = json.dumps(manifest).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(data)
    before = snapshot(root)
    result = launch(
        tmp_path,
        f"function tar.exe {{& tar @args}}\n& '{path}' -ManifestSHA256 '{digest(data)}' -CheckOnly\nexit $LASTEXITCODE",
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert "HISTORY_EXPORT_CHECK_PASSED_NO_HTTP" in result.stdout
    assert snapshot(root) == before
    assert not (tmp_path / "data/WBKANG-DEMO-Exports").exists()


def test_status_wrong_key_or_response_id_never_creates_a_job(tmp_path):
    root, _, pin = setup(tmp_path)
    run(tmp_path, root, pin)
    wrong = run(
        tmp_path,
        root,
        pin,
        mode="StatusOnce",
        extra="function Export-Credentials {return [pscustomobject]@{key='other-synthetic-key';secret='other-synthetic-secret'}}",
    )
    assert wrong["result"]["failure_category"] == "EXPORT_JOB_KEY_MISMATCH"
    assert wrong["calls"] == []
    bad_id = run(tmp_path, root, pin, mode="StatusOnce", fault="wrong_status_id")
    assert bad_id["result"]["failure_category"] == "EXPORT_STATUS_ID_MISMATCH"
    assert "/fapi/v1/order/asyn" not in bad_id["calls"]


def test_guard_write_failure_prevents_request_and_job_write_failure_keeps_guard(tmp_path):
    root, _, pin = setup(tmp_path)
    blocked = run(
        tmp_path,
        root,
        pin,
        extra="""
$script:saveJson=${function:Export-WriteJson}
function Export-WriteJson {param($Path,$Value) if((Split-Path $Path -Leaf) -ceq 'ORDER_EXPORT_ATTEMPT_20261117.json'){throw 'EXPORT_TEST_DISK_FAILURE'};& $script:saveJson $Path $Value}
""",
    )
    assert "/fapi/v1/order/asyn" not in blocked["calls"]
    failed = run(
        tmp_path,
        root,
        pin,
        extra="""
$script:saveJson=${function:Export-WriteJson}
function Export-WriteJson {param($Path,$Value) if((Split-Path $Path -Leaf) -ceq 'JOB.json'){throw 'EXPORT_TEST_DISK_FAILURE'};& $script:saveJson $Path $Value}
""",
    )
    assert failed["calls"].count("/fapi/v1/order/asyn") == 1
    assert not failed["result"]["job_id_recorded"]
    assert run(tmp_path, root, pin)["calls"] == []


def test_existing_guard_readback_failure_does_not_allow_recreation(tmp_path):
    root, _, pin = setup(tmp_path)
    failed = run(
        tmp_path,
        root,
        pin,
        extra="""
$script:saveJson=${function:Export-WriteJson}
function Export-WriteJson {param($Path,$Value) & $script:saveJson $Path $Value;if((Split-Path $Path -Leaf) -ceq 'ORDER_EXPORT_ATTEMPT_20261117.json'){throw 'EXPORT_FILE_READBACK_FAILED'}}
""",
    )
    assert failed["result"]["failure_category"] == "EXPORT_FILE_READBACK_FAILED"
    assert "/fapi/v1/order/asyn" not in failed["calls"]
    assert run(tmp_path, root, pin)["calls"] == []


def test_archive_corruption_blocks_plan_before_credentials(tmp_path):
    _, folder, pin = setup(tmp_path)
    archive = folder / "runtime.tar"
    archive.write_bytes(archive.read_bytes() + b"CORRUPTED")
    result = launch(
        tmp_path,
        f"""
. '{tmp_path}/READ_CORE.ps1'
. '{tmp_path}/PLAN_CORE.ps1'
function tar.exe {{throw 'MUST_NOT_EXTRACT'}}
try {{Invoke-ExportPlan '{folder.parent}' '{pin}'|Out-Null;throw 'NOT_BLOCKED'}}catch{{if($_.Exception.Message -cne 'INSPECT_ARCHIVE_HASH_MISMATCH'){{throw}};'BLOCKED'}}
""",
    )
    assert result.returncode == 0 and not result.stderr and result.stdout.strip() == "BLOCKED"


def test_response_stream_limit_is_enforced_without_printing_body(tmp_path):
    for name, data in package().items():
        (tmp_path / name).write_bytes(data)
    result = launch(
        tmp_path,
        f"""
. '{tmp_path}/READ_CORE.ps1'
. '{tmp_path}/EXPORT_CORE.ps1'
$s=New-Object IO.MemoryStream(,[Text.Encoding]::UTF8.GetBytes('SENSITIVE_BODY_TOO_LONG'))
try {{Export-ReadBody $s 2}}catch{{if($_.Exception.Message -cne 'EXPORT_RESPONSE_TOO_LARGE'){{throw}};'BLOCKED'}}finally{{$s.Dispose()}}
""",
    )
    assert result.returncode == 0 and not result.stderr and result.stdout.strip() == "BLOCKED"


def test_cmds_require_explicit_collection_and_scripts_parse_without_mutation_commands(tmp_path):
    files = package()
    assert b"-NonInteractive" in files["RUN_CHECK.cmd"]
    assert b"-NonInteractive" not in files["RUN_REQUEST_EXPORT.cmd"]
    assert b"-RequestExportOnce" in files["RUN_REQUEST_EXPORT.cmd"]
    assert b"-StatusOnce" in files["RUN_STATUS.cmd"]
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    result = launch(
        tmp_path,
        f"""
foreach($file in @('READ_CORE.ps1','PLAN_CORE.ps1','EXPORT_CORE.ps1','PRIVATE_STORAGE.ps1','RUN_EXPORT.ps1')) {{
 $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path '{tmp_path}' $file),[ref]$tokens,[ref]$errors)
 if($errors.Count -gt 0){{throw 'PARSE_ERROR'}}
 foreach($node in $ast.FindAll({{param($n) $n -is [Management.Automation.Language.CommandAst]}},$true)) {{
  if($node.GetCommandName() -in @('docker','Start-Process','Invoke-Expression','Invoke-WebRequest','Invoke-RestMethod','Remove-Item','Move-Item','Copy-Item')){{throw 'FORBIDDEN_COMMAND'}}
 }}
}}
'PARSED'
""",
    )
    assert result.returncode == 0 and not result.stderr and result.stdout.strip() == "PARSED"


@pytest.mark.parametrize("fault", ["missing_id", "numeric_id"])
def test_unexpected_creation_response_is_retained_before_id_validation(tmp_path, fault):
    root, _, pin = setup(tmp_path)
    value = run(tmp_path, root, pin, fault=fault)
    assert value["result"]["failure_category"] == "EXPORT_CREATION_OUTCOME_UNRESOLVED"
    parent = tmp_path / "data/WBKANG-DEMO-Exports"
    saved = list(parent.glob("history-export-*/CREATION_RESPONSE.json"))
    assert len(saved) == 1
    data = json.loads(saved[0].read_bytes())
    assert data == (dict(code=-1) if fault == "missing_id" else dict(downloadId=123456789))
    assert not list(parent.glob("history-export-*/JOB.json"))
    assert run(tmp_path, root, pin)["calls"] == []
