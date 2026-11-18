"""Read-only SQL financial projection and frozen-helper admission, no source starts."""

import copy
import hashlib
import json
import os
import subprocess
import time
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_copy_financial_bundle as builder
from tests.unit.test_baseline_reference_bundle import records
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch

pytestmark = pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")


def package():
    release = os.getenv("COPY_FINANCIAL_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git
    ref = real("rev-parse", "HEAD").decode().strip()

    def read(*args):
        if args[0] == "show" and args[1].startswith(ref + ":tools/windows_demo_copy_financial/"):
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with patch.object(builder, "git", read):
        return builder.package_files("HEAD")


def fixture():
    adoption, complete, commit = records()
    state = copy.deepcopy(adoption["old_row"]["state"])
    state.update(
        risk_state="NORMAL",
        daily_key="2026-11-13",
        reconciled=False,
        stream_ready=False,
        last_reconciled=state["value"]["as_of"],
    )
    requests = [
        {"request_reference_sha256": hashlib.sha256(k.encode()).hexdigest(), "request_state": v}
        for k, v in state["requests"].items()
    ]
    account = dict(
        paused=True,
        daily_key="2026-11-13",
        weekly_key="2026-11-09",
        orders=34,
        unresolved_requests=4,
        account_state_sha256="a" * 64,
        transaction_read_only="on",
        risk_state="NORMAL",
        reconciled=False,
        stream_ready=False,
        last_reconciled=state["last_reconciled"],
        request_states=requests,
        financial={
            k: state[k] for k in ("daily_base", "weekly_base", "high_water", "available_balance")
        },
    )
    account["financial"].update({k: v for k, v in state["value"].items() if k != "as_of"})
    account["financial"]["value_as_of"] = state["value"]["as_of"]
    return state, adoption, complete, commit, account


def validate(tmp_path, account):
    _, a, c, k, _ = fixture()
    for name, body in package().items():
        (tmp_path / name).write_bytes(body)
    for name, obj in [("adoption", a), ("complete", c), ("commit", k), ("account", account)]:
        (tmp_path / (name + ".json")).write_text(json.dumps(obj))
    value = launch(
        tmp_path,
        f"""
. '{tmp_path}/READ_CORE.ps1'
. '{tmp_path}/BASELINE_CORE.ps1'
. '{tmp_path}/FINANCIAL_EXT.ps1'
$a=Get-Content -Raw '{tmp_path}/adoption.json'|ConvertFrom-Json
$c=Get-Content -Raw '{tmp_path}/complete.json'|ConvertFrom-Json
$k=Get-Content -Raw '{tmp_path}/commit.json'|ConvertFrom-Json
$account=Get-Content -Raw '{tmp_path}/account.json'|ConvertFrom-Json
$script:FinancialBaseline=Baseline-Review $a $c $k
$script:FinancialPrior=[pscustomobject]@{{account_state_sha256=('a'*64)}}
$script:FinancialPlan=[pscustomobject]@{{anchors=@($a.old_row.state.requests.PSObject.Properties|ForEach-Object {{[pscustomobject]@{{request_reference_sha256=(Inspect-Hash ([Text.Encoding]::UTF8.GetBytes($_.Name)));request_state=$_.Value}}}});targets=@()}}
try{{Assert-CopyFinancial $account;'ACCEPTED'}}catch{{$_.Exception.Message}}
""",
    )
    assert value.returncode == 0 and not value.stderr, value.stdout + value.stderr
    assert "PRIVATE_CLIENT" not in value.stdout
    return value.stdout.strip()


def test_exact_sql_financial_values_and_four_original_request_hashes_match(tmp_path):
    *_, account = fixture()
    assert validate(tmp_path, account) == "ACCEPTED"


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("digest", "FINANCIAL_SQL_STATE_MISMATCH"),
        ("readonly", "FINANCIAL_SQL_STATE_MISMATCH"),
        ("initial", "FINANCIAL_VALUE_COMPONENT_MISMATCH"),
        ("realized", "FINANCIAL_VALUE_COMPONENT_MISMATCH"),
        ("fees", "FINANCIAL_VALUE_COMPONENT_MISMATCH"),
        ("high_water", "FINANCIAL_PERIOD_OR_HIGH_WATER_MISMATCH"),
        ("daily_base", "FINANCIAL_PERIOD_OR_HIGH_WATER_MISMATCH"),
        ("timestamp", "FINANCIAL_VALUATION_TIME_CHANGED"),
        ("request_state", "FINANCIAL_REQUEST_SCOPE_CHANGED"),
        ("request_duplicate", "FINANCIAL_REQUEST_SCOPE_CHANGED"),
        ("paused", "FINANCIAL_SQL_STATE_MISMATCH"),
        ("extra", "FINANCIAL_SQL_SCHEMA_INVALID"),
    ],
)
def test_unexpected_projection_cannot_be_reported_as_a_matching_ledger(tmp_path, fault, expected):
    *_, account = fixture()
    if fault == "digest":
        account["account_state_sha256"] = "b" * 64
    elif fault == "readonly":
        account["transaction_read_only"] = "off"
    elif fault == "paused":
        account["paused"] = False
    elif fault == "extra":
        account["wire_id"] = "PRIVATE_CLIENT_SENTINEL"
    elif fault == "timestamp":
        account["financial"]["value_as_of"] = "2026-11-18T00:00:00Z"
    elif fault == "request_state":
        account["request_states"][0]["request_state"] = "NOT_SENT"
    elif fault == "request_duplicate":
        account["request_states"][1] = account["request_states"][0]
    else:
        account["financial"][fault] = "1.00000000"
    assert validate(tmp_path, account) == expected


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("valid", "OK"),
        ("missing", "FINANCIAL_REVIEWED_HELPER_MISSING"),
        ("writable", "COMPARE_HELPER_MOUNT_INVALID"),
        ("running", "COMPARE_HELPER_SCOPE_INVALID"),
        ("exit", "FINANCIAL_REVIEWED_HELPER_EXIT_CHANGED"),
    ],
)
def test_only_exact_stopped_readonly_comparison_helper_is_filtered(tmp_path, fault, expected):
    files = package()
    for n, b in files.items():
        (tmp_path / n).write_bytes(b)
    config = json.loads(files["CONFIG.json"])
    helper = config["comparison_helper_id"]
    body = f"""
. '{tmp_path}/FINANCIAL_EXT.ps1'
$script:FinancialConfig=[pscustomobject]@{{comparison_helper_id='{helper}';api_container_id=('a'*64);database_container_id=('b'*64)}}
$script:FinancialState=[pscustomobject]@{{}}
function Financial-AllContainerIds {{return @('UNKNOWN_OTHER_CONTAINER'{",'" + helper + "'" if fault != "missing" else ""})}}
function Assert-CompareHelper {{param($Id,$State,$Original) if($Id -cne '{helper}'){{throw 'BAD_ID'}};if('{fault}' -ceq 'writable'){{throw 'COMPARE_HELPER_MOUNT_INVALID'}};if('{fault}' -ceq 'running'){{throw 'COMPARE_HELPER_SCOPE_INVALID'}}}}
function Backup-Docker {{param($Arguments,$Maximum) return $(if('{fault}' -ceq 'exit'){{'137 false'}}else{{'0 false'}})}}
try{{$ids=@(Container-Ids);if(($ids -join '|') -cne 'UNKNOWN_OTHER_CONTAINER'){{throw 'OTHER_CONTAINER_HIDDEN'}};'OK'}}catch{{$_.Exception.Message}}
"""
    value = launch(tmp_path, body)
    assert value.returncode == 0 and not value.stderr, value.stdout + value.stderr
    assert value.stdout.strip() == expected


def test_restore_control_commands_are_unchanged_except_the_removed_extra_diagnostic(tmp_path):
    files = package()
    from scripts.build_windows_copy_transition_bundle import package_files as prior

    base = prior(builder.PIN)
    for n in ("SUPPORT.ps1", "SOURCE_SCOPE.ps1", "restore_worker.py", "cold_archive.py"):
        assert files[n] == base[n]
    for name, body in [("NEW.ps1", files["COPY_CORE.ps1"]), ("OLD.ps1", base["COPY_CORE.ps1"])]:
        (tmp_path / name).write_bytes(body)
    value = launch(
        tmp_path,
        f"""
function Controls($Path) {{
 $tokens=$null;$errors=$null;$a=[Management.Automation.Language.Parser]::ParseFile($Path,[ref]$tokens,[ref]$errors)
 if($errors.Count -ne 0){{throw 'PARSE_ERROR'}}
 return @($a.FindAll({{param($n) $n -is [Management.Automation.Language.CommandAst] -and $n.GetCommandName() -in @('docker','Backup-Docker','Stop-OwnedClone','Assert-RestoreSourceSnapshot','Assert-InputHashes','Read-CopySources')}},$true)|ForEach-Object {{$_.Extent.Text}})
}}
if((Controls '{tmp_path}/NEW.ps1'|ConvertTo-Json -Compress) -cne (Controls '{tmp_path}/OLD.ps1'|ConvertTo-Json -Compress)){{throw 'RESTORE_CONTROL_CHANGED'}}
'UNCHANGED'
""",
    )
    assert value.returncode == 0 and not value.stderr, value.stdout + value.stderr
    assert value.stdout.strip() == "UNCHANGED"
    for forbidden in (b"UPDATE ", b"INSERT ", b"DELETE ", b"ALTER ", b"CREATE "):
        assert forbidden not in files["account_check.sql"].upper()


def test_transfer_seal_refuses_tampering_before_access(tmp_path):
    files = package()
    for n, b in files.items():
        (tmp_path / n).write_bytes(b)
    pin = hashlib.sha256(files["TRANSFER_MANIFEST.json"]).hexdigest()
    (tmp_path / "account_check.sql").write_text("PRIVATE_MUST_NOT_RUN")
    value = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_FINANCIAL.ps1' -ManifestSHA256 '{pin}' -ExecuteRead\nexit $LASTEXITCODE",
    )
    assert value.returncode == 2 and not value.stderr
    assert "PRIVATE_MUST_NOT_RUN" not in value.stdout
    assert json.loads(value.stdout)["copy_financial_review"] == "BLOCKED_TRANSFER_OR_LAUNCHER"


def test_projection_runs_on_real_networkless_postgres_and_cannot_write(tmp_path):
    images = subprocess.check_output(
        ["docker", "image", "ls", "--filter", "reference=postgres", "--format", "{{.ID}}"],
        text=True,
    ).splitlines()
    assert images, "Cached PostgreSQL required for this safety test"
    image = images[0]
    ident = None
    try:
        ident = subprocess.check_output(
            [
                "docker",
                "run",
                "-d",
                "--pull=never",
                "--network",
                "none",
                "--tmpfs",
                "/var/lib/postgresql:rw,mode=1777",
                "-e",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                "-e",
                "POSTGRES_USER=dev",
                "-e",
                "POSTGRES_DB=hybrid",
                image,
            ],
            text=True,
        ).strip()
        for _ in range(80):
            ready = subprocess.run(
                ["docker", "exec", ident, "pg_isready", "-U", "dev", "-d", "hybrid"],
                capture_output=True,
            )
            leader = subprocess.run(
                ["docker", "exec", ident, "cat", "/proc/1/comm"], capture_output=True, text=True
            )
            if ready.returncode == 0 and leader.stdout.strip() == "postgres":
                break
            time.sleep(0.25)
        else:
            raise AssertionError("Test PostgreSQL startup failed")
        state, *_ = fixture()
        setup = (
            "CREATE TABLE demo_accounts(account_id text primary key,state jsonb); INSERT INTO demo_accounts VALUES ('DEMO-WINDOWS-ISOLATED-BTC-V1','"
            + json.dumps(state).replace("'", "''")
            + "'::jsonb);"
        )
        args = [
            "docker",
            "exec",
            "-i",
            ident,
            "psql",
            "-X",
            "-q",
            "-t",
            "-A",
            "--no-password",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "dev",
            "-d",
            "hybrid",
        ]
        prepared = subprocess.run(args, input=setup, text=True, capture_output=True, timeout=10)
        assert prepared.returncode == 0, prepared.stderr
        sql = package()["account_check.sql"].decode()
        rows = []
        for _ in range(2):
            value = subprocess.run(args, input=sql, text=True, capture_output=True, timeout=10)
            assert value.returncode == 0, value.stderr
            rows.append(json.loads(value.stdout))
        assert rows[0] == rows[1]
        account = rows[0]
        assert account["transaction_read_only"] == "on" and account["orders"] == 34
        assert account["financial"]["realized"] == state["value"]["realized"]
        assert account["financial"]["high_water"] == state["high_water"]
        assert "PRIVATE_CLIENT" not in value.stdout
        denied = subprocess.run(
            args,
            input="BEGIN READ ONLY; UPDATE demo_accounts SET state='{}'; COMMIT;",
            text=True,
            capture_output=True,
            timeout=10,
        )
        assert denied.returncode != 0 and "read-only" in denied.stderr
    finally:
        if ident:
            subprocess.run(["docker", "rm", "-f", ident], check=True, capture_output=True)


def test_sealed_check_loads_new_scope_without_creating_or_starting(tmp_path):
    files = package()
    for n, b in files.items():
        (tmp_path / n).write_bytes(b)
    backup = tmp_path / "backup"
    backup.mkdir()
    for n in (
        "BACKUP_RECEIPT.json",
        "BACKUP_STATE.json",
        "manifest.json",
        "database.tar",
        "runtime.tar",
        "images.tar",
    ):
        (backup / n).write_bytes(b"{}")
    ext = tmp_path / "FINANCIAL_EXT.ps1"
    ext.write_text(
        ext.read_text()
        + f"""
function Find-RetainedBackup {{param($Config) return '{backup}'}}
function Assert-InputHashes {{param($Directory,$Config,$Receipt)}}
function Read-RestoreJson {{param($Path,$Maximum) if($Path.EndsWith('BACKUP_STATE.json')){{return [pscustomobject]@{{canonical_images=@(('sha256:'+('8'*64)),('sha256:'+('9'*64)))}}}};return Get-Content -Raw $Path|ConvertFrom-Json}}
function Read-CopySources {{param($Config,$Receipt,$State) $script:CopyRestoreHandle=New-Object IO.FileStream('{backup}/runtime.tar',[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);return ,@([pscustomobject]@{{id=('b'*64)}})}}
function Backup-Docker {{param($Arguments,$Maximum) if($Arguments[0] -cne 'image' -or $Arguments[1] -cne 'inspect'){{throw 'MUTATION_ATTEMPT'}};return $Arguments[-1]}}
"""
    )
    config = json.loads(files["CONFIG.json"])
    config["receipt_sha256"] = hashlib.sha256(
        (backup / "BACKUP_RECEIPT.json").read_bytes()
    ).hexdigest()
    (tmp_path / "CONFIG.json").write_text(json.dumps(config))
    manifest = json.loads(files["TRANSFER_MANIFEST.json"])
    manifest["files"]["CONFIG.json"] = hashlib.sha256(
        (tmp_path / "CONFIG.json").read_bytes()
    ).hexdigest()
    manifest["files"]["FINANCIAL_EXT.ps1"] = hashlib.sha256(ext.read_bytes()).hexdigest()
    raw = json.dumps(manifest).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(raw)
    value = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_FINANCIAL.ps1' -ManifestSHA256 '{hashlib.sha256(raw).hexdigest()}' -CheckOnly\nexit $LASTEXITCODE",
    )
    assert value.returncode == 0 and not value.stderr, value.stdout + value.stderr
    assert "COPY_FINANCIAL_CHECK_PASSED_NO_CLONE_NO_SOURCE_START" in value.stdout
    assert "MUTATION_ATTEMPT" not in value.stdout


def test_all_sql_output_fields_are_bounded_and_only_safe_fields_selected():
    files = package()
    text = files["account_check.sql"].decode()
    assert "state::text" in text and "sha256(convert_to(state::text,'UTF8'))" in text
    assert "sha256(convert_to(key,'UTF8'))" in text
    assert "json_build_object('client_id'" not in text
    assert "state->>'operations'" not in text
    assert "Assert-CopyFinancial $account" in files["COPY_CORE.ps1"].decode()
    assert "Invoke-CopyDiagnostic" not in files["COPY_CORE.ps1"].decode()
