"""New consent is source-only; native GET collection and pure binder fail closed."""

import copy
import hashlib
import json
import os
import zipfile
from datetime import datetime
from unittest.mock import patch

import pytest

from scripts import build_windows_conditional_current_bundle as builder
from services.execution.demo_conditional_source_binding import bind_fresh_source
from services.execution.demo_source_reuse_review import SourceReuseBlocked
from tests.unit import test_current_read_bundle as prior
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch
from tests.unit.test_source_reuse_review import fixture


def package():
    release = os.getenv("CONDITIONAL_CURRENT_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git

    def read(*args):
        if args[0] == "show":
            path = args[1].split(":", 1)[-1]
            if path.startswith("tools/windows_demo_conditional_current/") or path in {
                "docs/reviews/20261124/SOURCE_ACCEPTANCE_REVIEW.json",
                "docs/reviews/20261124/SOURCE_ACCEPTANCE_RECEIPT.json",
                "docs/reviews/20261124/SOURCE_REFRESH_WINDOWS_OBSERVATION.json",
            }:
                return (ROOT / path).read_bytes()
        return real(*args)

    with patch.object(builder, "git", read):
        return builder.package_files("HEAD")


def test_seal_and_existing_guards_unchanged_except_one_public_clock_budget():
    f = package()
    old = builder.prior_files(builder.PIN)
    for n in (
        "PLAN_CORE.ps1",
        "SAVED_INCOME_CORE.ps1",
        "SAVED_TRADE_CORE.ps1",
        "READ_CORE.ps1",
        "ARRAY_CORE.ps1",
        "TRADE_HELPERS.ps1",
        "INCOME_HELPERS.ps1",
    ):
        assert f[n] == old[n]
    assert f["READ_TRANSPORT.ps1"] == old["READ_TRANSPORT.ps1"].replace(
        b"$Context.http_attempts -lt 18", b"$Context.http_attempts -lt 19"
    )
    m = json.loads(f["TRANSFER_MANIFEST.json"])
    assert all(hashlib.sha256(f[n]).hexdigest() == h for n, h in m["files"].items())
    assert b"604800000L" in f["CURRENT_CORE.ps1"]
    assert b"1795490093574L" in f["CURRENT_CORE.ps1"]
    text = b"\n".join(v for n, v in f.items() if n.endswith(".ps1"))
    assert b"docker" not in text.lower().replace(b"docker_used", b"")
    assert b"-Method POST" not in text and b"WriteAllText" not in text


def setup(tmp_path):
    with patch.object(prior, "package", package):
        return prior.setup(tmp_path)


def collect(tmp_path, root, pin, day="2026-11-24", fault="", extra=""):
    real = prior.launch
    # Synthetic income cutoff is earlier than the user's real saved cutoff.
    # Keep the day-25 positive case inside its actual 7-day reference limit.
    hour = "00:30:00" if day == "2026-11-25" else "03:30:00"
    at = int(datetime.fromisoformat(day + "T" + hour + "+00:00").timestamp() * 1000)

    def reader(path, script):
        script = script.replace(
            "$script:configCalls=0", "$script:configCalls=0;$script:clockCalls=0"
        )
        script = script.replace(
            "$reply=@{serverTime=1794978000000L}",
            f"$script:clockCalls++;$reply=@{{serverTime=({at}L+5000L*$script:clockCalls)}}",
        )
        script = script.replace(
            "WBKANG-DEMO-CurrentReads/current-read-20261118-",
            "WBKANG-DEMO-ConditionalReads/conditional-current-20261124-",
        )
        return real(path, script)

    binding = (
        f". '{tmp_path}/ACCEPTANCE_CORE.ps1'\n. '{tmp_path}/BINDING_CORE.ps1'\nAssert-AcceptedSource '{tmp_path}' $source\n"
        + extra
    )
    with patch.object(prior, "launch", reader):
        return prior.run(tmp_path, root, pin, extra=binding, fault=fault)


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("day", ["2026-11-24", "2026-11-25"])
def test_real_get_mock_collection_binds_new_period_without_any_apply(tmp_path, day):
    root, parent, pin = setup(tmp_path)
    before = (
        prior.snapshot(root),
        prior.snapshot(parent),
        prior.snapshot(tmp_path / "data/WBKANG-DEMO-IncomeReads"),
    )
    v = collect(tmp_path, root, pin, day)
    r = v["result"]
    assert r["current_read"] == "CURRENT_ACCOUNT_OBSERVATIONS_SAVED_NO_CHANGES", r
    assert r["conditional_source_bound"] and r["fresh_raw_evidence_hashes_rechecked"], r
    assert r["candidate_day_kst"] == day and r["candidate_week_kst"] == "2026-11-23"
    assert (
        r["accepted_day_key_kst"] == "2026-11-24"
        and r["saved_reference_day_key_kst"] == "2026-11-18"
    )
    assert r["actual_http_attempts"] == 19 and v["calls"].count("/fapi/v1/time") == 2
    assert len(r["raw_evidence"]) == 14
    for k in (
        "apply_token_created",
        "original_database_start_authorized",
        "original_database_write_authorized",
        "original_owner_lease_takeover_authorized",
        "original_apply_ready",
        "baseline_adopted",
        "request_states_changed",
        "orders_enabled",
        "live_trading_enabled",
        "docker_used",
        "services_started",
    ):
        assert r[k] is False
    assert before == (
        prior.snapshot(root),
        prior.snapshot(parent),
        prior.snapshot(tmp_path / "data/WBKANG-DEMO-IncomeReads"),
    )
    receipt = list((tmp_path / "data/WBKANG-DEMO-ConditionalReads").rglob("RECEIPT.json"))
    assert len(receipt) == 1 and json.loads(receipt[0].read_bytes())["conditional_source_bound"]


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "fault",
    [
        "regular",
        "algo",
        "position",
        "hedge",
        "multi",
        "leverage",
        "margin",
        "wallet_change",
        "mode_change",
        "income_gap",
        "trade_gap",
        "gap_full",
        "timeout",
        "anchor",
        "non_array",
        "missing_weight",
        "headroom",
        "leak",
    ],
)
def test_get_faults_cannot_create_source_binding(tmp_path, fault):
    root, _, pin = setup(tmp_path)
    r = collect(tmp_path, root, pin, fault=fault)["result"]
    assert not r["conditional_source_bound"] and not r["source_accepted"], r
    assert not r["original_apply_ready"] and not r["baseline_adopted"]


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("day", ["2026-11-23", "2026-11-26", "2026-11-30"])
def test_period_or_old_reference_rejected_after_only_public_time(tmp_path, day):
    root, _, pin = setup(tmp_path)
    v = collect(tmp_path, root, pin, day)
    assert v["calls"] == ["/fapi/v1/time"]
    assert not v["result"]["conditional_source_bound"]


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "fault", ["raw-hash", "saved-hash", "raw-scope", "clock-stale", "clock-backward"]
)
def test_binding_rechecks_raw_saved_hashes_and_fresh_clock(tmp_path, fault):
    root, _, pin = setup(tmp_path)
    extra = "$script:OriginalRaw=(Get-Item Function:Assert-ConditionalRaw).ScriptBlock\n"
    if fault == "raw-hash":
        extra += "function Assert-ConditionalRaw {param($R) [IO.File]::WriteAllText((Join-Path $R.private_directory 'ACCOUNT_1.json'),'{}');& $script:OriginalRaw $R}"
    elif fault == "saved-hash":
        extra += "function Trades-VerifySaved {param($Source) if($script:clockCalls -gt 1){throw 'TRADES_SAVED_INPUT_CHANGED'}}"
    elif fault == "raw-scope":
        extra += "function Assert-ConditionalRaw {param($R) $R.raw_evidence=@($R.raw_evidence|Select-Object -First 13);& $script:OriginalRaw $R}"
    else:
        offset = "300001L" if fault == "clock-stale" else "-300001L"
        extra += f"$script:OriginalClock=(Get-Item Function:Export-Clock).ScriptBlock\nfunction Export-Clock {{param($Context) & $script:OriginalClock $Context;if($script:clockCalls -eq 2){{$Context.server_time+={offset}}}}}"
    r = collect(tmp_path, root, pin, extra=extra)["result"]
    assert not r["conditional_source_bound"] and not r["original_apply_ready"], r


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_receipt_write_failure_clears_binding_and_cannot_report_success(tmp_path):
    root, _, pin = setup(tmp_path)
    extra = "$script:OriginalWriter=(Get-Item Function:Export-WriteJson).ScriptBlock\nfunction Export-WriteJson {param($Path,$Value) if($Path.EndsWith('RECEIPT.json')){throw 'EXPORT_TEST_RECEIPT_FAIL'};& $script:OriginalWriter $Path $Value}"
    r = collect(tmp_path, root, pin, extra=extra)["result"]
    assert not r["conditional_source_bound"] and not r["source_accepted"]
    assert r["failure_category"] == "CURRENT_RECEIPT_WRITE_FAILED"


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_extra_twentieth_get_is_rejected_without_retry(tmp_path):
    root, _, pin = setup(tmp_path)
    extra = "$script:OriginalClock=(Get-Item Function:Export-Clock).ScriptBlock\nfunction Export-Clock {param($Context) & $script:OriginalClock $Context;if($script:clockCalls -eq 2){$null=Export-Http $Context '/fapi/v1/time' @{}}}"
    v = collect(tmp_path, root, pin, extra=extra)
    r = v["result"]
    assert len(v["calls"]) == r["actual_http_attempts"] == 19
    assert r["failure_category"] == "EXPORT_REQUEST_BUDGET_EXCEEDED"
    assert not r["conditional_source_bound"]


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "bound,complete,exitcode", [(True, True, 0), (False, True, 2), (True, False, 2)]
)
def test_launcher_success_requires_binding_and_complete_saved_collection(
    tmp_path, bound, complete, exitcode
):
    _, _, pin = setup(tmp_path)
    runner = tmp_path / "RUN_CURRENT.ps1"
    runner.write_text(
        runner.read_text().replace(
            "2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d", pin
        )
    )
    (tmp_path / "PRIVATE_STORAGE.ps1").write_text(
        "function Reference-AssertPrivateDirectory {param($Path)}"
    )
    core = tmp_path / "CURRENT_CORE.ps1"
    state = (
        "CURRENT_ACCOUNT_OBSERVATIONS_SAVED_NO_CHANGES"
        if complete
        else "STOPPED_PARTIAL_RECORDS_RETAINED_NO_AUTOMATIC_RETRY"
    )
    b = "$true" if bound else "$false"
    core.write_text(
        core.read_text()
        + f"\nfunction Invoke-CurrentRead {{param($Plan,$Source) return [pscustomobject]@{{current_read='{state}';conditional_source_bound={b};raw_evidence=@()}}}}"
    )
    m = json.loads((tmp_path / "TRANSFER_MANIFEST.json").read_bytes())
    for n in ("RUN_CURRENT.ps1", "PRIVATE_STORAGE.ps1", "CURRENT_CORE.ps1"):
        m["files"][n] = hashlib.sha256((tmp_path / n).read_bytes()).hexdigest()
    raw = json.dumps(m).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(raw)
    v = launch(
        tmp_path,
        f"function tar.exe {{& tar @args}}\n& '{runner}' -ManifestSHA256 '{hashlib.sha256(raw).hexdigest()}' -CollectOnce\nexit $LASTEXITCODE",
    )
    assert v.returncode == exitcode and not v.stderr, v.stdout + v.stderr
    assert ("CONDITIONAL_SOURCE_BOUND_NO_ORIGINAL_START_NO_APPLY_NO_ORDERS" in v.stdout) == (
        exitcode == 0
    )


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "name",
    [
        "SOURCE_ACCEPTANCE.json",
        "SOURCE_PROPOSAL.json",
        "BINDING_CORE.ps1",
        "SOURCE_REFRESH_OBSERVATION.json",
    ],
)
def test_seal_refuses_changes_before_key_or_network(tmp_path, name):
    f = package()
    for n, b in f.items():
        (tmp_path / n).write_bytes(b)
    (tmp_path / name).write_text("PRIVATE_MUST_NOT_RUN")
    v = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_CURRENT.ps1' -ManifestSHA256 '{hashlib.sha256(f['TRANSFER_MANIFEST.json']).hexdigest()}' -CollectOnce\nexit $LASTEXITCODE",
    )
    assert v.returncode == 2 and not v.stderr
    assert "PRIVATE_MUST_NOT_RUN" not in v.stdout and "INSPECT_TRANSFER_INVALID" in v.stdout


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_offline_check_has_no_credentials_http_or_output_directory(tmp_path):
    real = prior.launch

    def check(path, script):
        v = real(path, script)
        assert "CONDITIONAL_CURRENT_CHECK_PASSED_NO_HTTP" in v.stdout, v.stdout + v.stderr
        v.stdout = v.stdout.replace(
            "CONDITIONAL_CURRENT_CHECK_PASSED_NO_HTTP", "CURRENT_READ_CHECK_PASSED_NO_HTTP"
        )
        return v

    with patch.object(prior, "package", package), patch.object(prior, "launch", check):
        prior.test_sealed_offline_check_links_both_old_runs_and_writes_nothing(tmp_path)
    assert not (tmp_path / "data/WBKANG-DEMO-ConditionalReads").exists()


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "stale",
        "future",
        "amount",
        "orders",
        "bool-count",
        "settings",
        "gap",
        "saturated",
        "authority",
        "missing",
        "period",
    ],
)
def test_native_pure_eligibility_agrees_with_python_policy(tmp_path, fault):
    p, o, args = fixture("2026-11-30" if fault == "period" else "2026-11-24")
    if fault == "stale":
        args["server_now_ms"] = o["observations_end_ms"] + 300001
    elif fault == "future":
        args["server_now_ms"] = o["observations_end_ms"] - 1
    elif fault == "amount":
        o["summary"]["rounds"][0]["account"]["usdt_available"] = "5000.00000000"
    elif fault in {"orders", "bool-count"}:
        o["summary"]["rounds"][1]["algo_orders"]["count"] = True if fault == "bool-count" else 1
    elif fault == "settings":
        o["summary"]["btc_config_after"]["leverage"] = 3
    elif fault == "gap":
        o["summary"]["btc_trade_gap_rows"] = 1
    elif fault == "saturated":
        o["summary"]["income_gap_page_limit_reached"] = True
    elif fault == "authority":
        o["baseline_adopted"] = True
    elif fault == "missing":
        del o["summary"]["mode_after"]
    f = package()
    e, a = json.loads(f["SOURCE_PROPOSAL.json"]), json.loads(f["SOURCE_ACCEPTANCE.json"])
    try:
        bind_fresh_source(e, a, copy.deepcopy(o), **args)
        expected = "ACCEPTED"
    except SourceReuseBlocked:
        expected = "BLOCKED"
    o["observation_started_server_ms"] = o["observations_end_ms"] - 1000
    for n in ("READ_CORE.ps1", "INCOME_HELPERS.ps1", "BINDING_CORE.ps1"):
        (tmp_path / n).write_bytes(f[n])
    (tmp_path / "o.json").write_text(json.dumps(o))
    (tmp_path / "p.json").write_text(json.dumps(p))
    body = f"""
. '{tmp_path}/READ_CORE.ps1'
. '{tmp_path}/INCOME_HELPERS.ps1'
. '{tmp_path}/BINDING_CORE.ps1'
$script:AcceptedSource=[pscustomobject]@{{policy=(Get-Content -Raw '{tmp_path}/p.json'|ConvertFrom-Json).conditional_reuse}}
$r=Get-Content -Raw '{tmp_path}/o.json'|ConvertFrom-Json
$s=[pscustomobject]@{{old_cutoff_ms={args["verified_reference_start_ms"]}L;trade_end_ms={args["verified_reference_start_ms"]}L}}
try{{$null=Assert-ConditionalObservation $r $s {args["server_now_ms"]}L;'ACCEPTED'}}catch{{'BLOCKED'}}
"""
    v = launch(tmp_path, body)
    assert v.returncode == 0 and not v.stderr, v.stdout + v.stderr
    assert v.stdout.strip() == expected
