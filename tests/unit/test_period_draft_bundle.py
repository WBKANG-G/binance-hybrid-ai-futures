"""Full JSONB draft calculations remain read-only, scoped and explicitly unapproved."""

import hashlib
import json
import os
import subprocess
import time
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_period_draft_bundle as builder
from tests.unit import test_copy_financial_bundle as financial
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch

pytestmark = pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")


def package():
    release = os.getenv("PERIOD_DRAFT_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git
    ref = real("rev-parse", "HEAD").decode().strip()

    def read(*args):
        if args[0] == "show" and args[1].startswith(ref + ":tools/windows_demo_period_draft/"):
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with patch.object(builder, "git", read):
        return builder.package_files("HEAD")


@pytest.fixture(scope="module")
def native_database():
    image = subprocess.check_output(
        ["docker", "image", "ls", "--filter", "reference=postgres", "--format", "{{.ID}}"],
        text=True,
    ).splitlines()[0]
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
    try:
        for _ in range(80):
            leader = subprocess.run(
                ["docker", "exec", ident, "cat", "/proc/1/comm"], capture_output=True, text=True
            )
            ready = subprocess.run(
                ["docker", "exec", ident, "pg_isready", "-U", "dev", "-d", "hybrid"],
                capture_output=True,
            )
            if ready.returncode == 0 and leader.stdout.strip() == "postgres":
                break
            time.sleep(0.25)
        else:
            raise AssertionError("Test PostgreSQL startup failed")
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
        state, *_ = financial.fixture()
        state["operations"] = {"existing": "PRIVATE_OLD_AUDIT"}
        state["future_unknown"] = {"nested": [1, {"private": "PRIVATE_FUTURE_FIELD"}]}
        state["reconciled"] = state["stream_ready"] = True
        sql = (
            "CREATE TABLE demo_accounts(account_id text primary key,state jsonb,fence bigint NOT NULL DEFAULT 73); INSERT INTO demo_accounts(account_id,state) VALUES ('DEMO-WINDOWS-ISOLATED-BTC-V1','"
            + json.dumps(state).replace("'", "''")
            + "'::jsonb);"
        )
        result = subprocess.run(args, input=sql, text=True, capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr
        yield args
    finally:
        subprocess.run(["docker", "rm", "-f", ident], check=True, capture_output=True)


def query(args, sql=None, day="2026-11-18", evidence="e" * 64):
    params = [
        "-v",
        "draft_day=" + day,
        "-v",
        "draft_week=2026-11-16",
        "-v",
        "draft_amount=4998.74183608",
        "-v",
        "draft_evidence=" + evidence,
    ]
    value = subprocess.run(
        args + params,
        input=sql or package()["account_check.sql"].decode(),
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert value.returncode == 0, value.stderr
    assert "PRIVATE_" not in value.stdout
    return json.loads(value.stdout)


def assert_result(tmp_path, account):
    for n, b in package().items():
        (tmp_path / n).write_bytes(b)
    (tmp_path / "account.json").write_text(json.dumps(account))
    value = launch(
        tmp_path,
        f"""
. '{tmp_path}/READ_CORE.ps1'
. '{tmp_path}/PURE_HELPERS.ps1'
. '{tmp_path}/DRAFT_EXT.ps1'
function Assert-CopyFinancial {{param($Account) if($null -ne $Account.PSObject.Properties['transition_draft']){{throw 'OLD_SCHEMA_NOT_PROJECTED'}}}}
function Trades-VerifySaved {{param($Source)}}
$script:DraftEvidence=[pscustomobject]@{{day='2026-11-18';week='2026-11-16';amount='4998.74183608';sha256=('e'*64)}}
$account=Get-Content -Raw '{tmp_path}/account.json'|ConvertFrom-Json
try{{Assert-PeriodDraft $account;'VERIFIED_DRAFT'}}catch{{$_.Exception.Message}}
""",
    )
    assert value.returncode == 0 and not value.stderr, value.stdout + value.stderr
    return value.stdout.strip()


def test_native_full_json_draft_is_deterministic_preserves_unknowns_and_does_not_update(
    native_database, tmp_path
):
    before = subprocess.check_output(
        native_database,
        input="SELECT encode(sha256(convert_to(state::text,'UTF8')),'hex') FROM demo_accounts;",
        text=True,
    ).strip()
    first = query(native_database)
    second = query(native_database)
    assert first == second
    d = first["transition_draft"]
    assert d["before_state_sha256"] == before
    assert d["before_state_sha256"] != d["candidate_draft_state_sha256"]
    assert d["candidate_financial_state_sha256"] != d["candidate_draft_state_sha256"]
    assert set(d["changed_top_level_fields"]) == {
        "daily_key",
        "weekly_key",
        "reconciled",
        "stream_ready",
        "operations",
    }
    for flag in (
        "only_allowed_top_level_changes",
        "prior_operations_preserved",
        "audit_key_was_absent",
        "audit_entry_is_string",
        "audit_entry_matches_draft",
        "orders_preserved",
        "requests_preserved",
        "valuation_preserved",
        "high_water_preserved",
        "paused_preserved",
        "fresh_reconciliation_required",
    ):
        assert d[flag] is True
    assert d["audit_draft"]["source_accepted"] is False and d["audit_draft"]["applied"] is False
    assert assert_result(tmp_path, first) == "VERIFIED_DRAFT"
    after = subprocess.check_output(
        native_database,
        input="SELECT encode(sha256(convert_to(state::text,'UTF8')),'hex') FROM demo_accounts;",
        text=True,
    ).strip()
    assert before == after
    denied = subprocess.run(
        native_database,
        input="BEGIN READ ONLY; UPDATE demo_accounts SET state='{}'; COMMIT;",
        text=True,
        capture_output=True,
    )
    assert denied.returncode != 0 and "read-only" in denied.stderr


@pytest.mark.parametrize("field", ["high_water", "orders", "requests", "value", "future_unknown"])
def test_native_unreviewed_change_is_detected(native_database, tmp_path, field):
    sql = (
        package()["account_check.sql"]
        .decode()
        .replace(
            "state || fields AS proposed",
            "(state || fields) || jsonb_build_object('" + field + "','UNREVIEWED') AS proposed",
        )
    )
    account = query(native_database, sql)
    assert account["transition_draft"]["only_allowed_top_level_changes"] is False
    assert assert_result(tmp_path, account) == "DRAFT_PRESERVATION_CHECK_FAILED"


@pytest.mark.parametrize(
    "field",
    [
        "only_allowed_top_level_changes",
        "prior_operations_preserved",
        "audit_key_was_absent",
        "audit_entry_matches_draft",
        "requests_preserved",
        "high_water_preserved",
        "fresh_reconciliation_required",
    ],
)
def test_false_preservation_claim_is_blocked(native_database, tmp_path, field):
    account = query(native_database)
    account["transition_draft"][field] = False
    assert assert_result(tmp_path, account) == "DRAFT_PRESERVATION_CHECK_FAILED"


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("day", "DRAFT_EVIDENCE_OR_STATE_MISMATCH"),
        ("evidence", "DRAFT_EVIDENCE_OR_STATE_MISMATCH"),
        ("audit-accepted", "DRAFT_AUDIT_INVALID"),
        ("audit-applied", "DRAFT_AUDIT_INVALID"),
        ("extra-field", "DRAFT_CHANGED_FIELD_SCOPE_INVALID"),
        ("claim-typed", "DRAFT_UNSUPPORTED_CLAIM"),
    ],
)
def test_unaccepted_source_cannot_be_promoted_by_draft_output(
    native_database, tmp_path, fault, expected
):
    account = query(native_database)
    d = account["transition_draft"]
    if fault == "day":
        d["candidate_day"] = "2026-11-19"
    elif fault == "evidence":
        d["evidence_sha256"] = "0" * 64
    elif fault == "audit-accepted":
        d["audit_draft"]["source_accepted"] = True
    elif fault == "audit-applied":
        d["audit_draft"]["applied"] = True
    elif fault == "extra-field":
        d["changed_top_level_fields"].append("high_water")
    elif fault == "claim-typed":
        d["typed_ledger_model_validated"] = True
    assert assert_result(tmp_path, account) == expected


def test_evidence_changes_bind_to_another_review_id(native_database):
    a = query(native_database)["transition_draft"]
    b = query(native_database, evidence="f" * 64)["transition_draft"]
    assert a["review_id"] != b["review_id"]
    assert a["candidate_draft_state_sha256"] != b["candidate_draft_state_sha256"]
    assert a["before_state_sha256"] == b["before_state_sha256"]


def test_restore_control_commands_are_pinned_and_sql_adds_only_readonly_ctes(tmp_path):
    files = package()
    from scripts.build_windows_copy_financial_bundle import package_files as prior

    old = prior(builder.PIN)
    for n in (
        "SUPPORT.ps1",
        "SOURCE_SCOPE.ps1",
        "COMPARE_ADMISSION.ps1",
        "restore_worker.py",
        "cold_archive.py",
    ):
        assert files[n] == old[n]
    for n, b in [("NEW.ps1", files["COPY_CORE.ps1"]), ("OLD.ps1", old["COPY_CORE.ps1"])]:
        (tmp_path / n).write_bytes(b)
    result = launch(
        tmp_path,
        f"""
function Controls($Path) {{
 $t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile($Path,[ref]$t,[ref]$e)
 if($e.Count -ne 0){{throw 'PARSE_ERROR'}}
 return @($a.FindAll({{param($n) $n -is [Management.Automation.Language.CommandAst] -and $n.GetCommandName() -in @('Backup-Docker','Stop-OwnedClone','Assert-RestoreSourceSnapshot','Assert-InputHashes','Read-CopySources')}},$true)|ForEach-Object {{$_.Extent.Text}})
}}
if((Controls '{tmp_path}/NEW.ps1'|ConvertTo-Json -Compress) -cne (Controls '{tmp_path}/OLD.ps1'|ConvertTo-Json -Compress)){{throw 'RESTORE_CONTROL_CHANGED'}}
'UNCHANGED'
""",
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    sql = files["account_check.sql"].upper()
    for forbidden in (b"UPDATE ", b"INSERT ", b"DELETE ", b"ALTER ", b"CREATE "):
        assert forbidden not in sql
    assert b"READ ONLY" in sql
    assert "draft_only_not_a_commit_payload" in files["DRAFT_EXT.ps1"].decode()


def test_seal_refuses_mutated_sql_before_access(tmp_path):
    files = package()
    for n, b in files.items():
        (tmp_path / n).write_bytes(b)
    pin = hashlib.sha256(files["TRANSFER_MANIFEST.json"]).hexdigest()
    (tmp_path / "account_check.sql").write_text("PRIVATE_MUST_NOT_RUN")
    result = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_FINANCIAL.ps1' -ManifestSHA256 '{pin}' -ExecuteRead\nexit $LASTEXITCODE",
    )
    assert result.returncode == 2 and not result.stderr
    assert "PRIVATE_MUST_NOT_RUN" not in result.stdout


@pytest.mark.parametrize(
    "fault,expected",
    [("next-fence", "DRAFT_FENCE_INVALID"), ("audit-fence", "DRAFT_AUDIT_INVALID")],
)
def test_row_fence_and_audit_fence_must_be_bound(native_database, tmp_path, fault, expected):
    account = query(native_database)
    assert account["transition_draft"]["expected_fence"] == 73
    assert account["transition_draft"]["proposed_fence"] == 74
    if fault == "next-fence":
        account["transition_draft"]["proposed_fence"] = 75
    else:
        account["transition_draft"]["audit_draft"]["expected_fence"] = 72
    assert assert_result(tmp_path, account) == expected


def test_same_state_with_another_fence_requires_another_review_id(native_database):
    first = query(native_database)["transition_draft"]
    try:
        changed = subprocess.run(
            native_database,
            input="UPDATE demo_accounts SET fence=74;",
            capture_output=True,
            text=True,
        )
        assert changed.returncode == 0, changed.stderr
        second = query(native_database)["transition_draft"]
        assert first["before_state_sha256"] == second["before_state_sha256"]
        assert first["review_id"] != second["review_id"]
        assert first["candidate_draft_state_sha256"] != second["candidate_draft_state_sha256"]
        fence = subprocess.check_output(
            native_database, input="SELECT fence FROM demo_accounts;", text=True
        ).strip()
        assert fence == "74"
    finally:
        subprocess.run(
            native_database,
            input="UPDATE demo_accounts SET fence=73;",
            capture_output=True,
            text=True,
            check=True,
        )


def test_sealed_check_loads_evidence_extensions_without_mutating_resources(tmp_path):
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
    ext = tmp_path / "DRAFT_EXT.ps1"
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
    m = json.loads(files["TRANSFER_MANIFEST.json"])
    for name in ("CONFIG.json", "DRAFT_EXT.ps1"):
        m["files"][name] = hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
    raw = json.dumps(m).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(raw)
    value = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_FINANCIAL.ps1' -ManifestSHA256 '{hashlib.sha256(raw).hexdigest()}' -CheckOnly\nexit $LASTEXITCODE",
    )
    assert value.returncode == 0 and not value.stderr, value.stdout + value.stderr
    assert "PERIOD_DRAFT_CHECK_PASSED_NO_CLONE_NO_SOURCE_START" in value.stdout
    assert "MUTATION_ATTEMPT" not in value.stdout


@pytest.mark.parametrize(
    "fault,expected",
    [("valid", "CANDIDATE_BOUND"), ("tampered-current", "PREVIEW_RAW_HASH_MISMATCH")],
)
def test_candidate_uses_actual_saved_evidence_chain_before_copy_creation(tmp_path, fault, expected):
    from tests.unit.test_private_receipt_review_v2 import snapshot
    from tests.unit.test_recovery_preview_bundle import setup as evidence_setup

    root, pin = evidence_setup(tmp_path)
    for name, body in package().items():
        (tmp_path / name).write_bytes(body)
    if fault == "tampered-current":
        path = next((tmp_path / "data/WBKANG-DEMO-CurrentReads").glob("*/MODE_AFTER.json"))
        path.write_bytes(path.read_bytes() + b" ")
    before = snapshot(tmp_path / "data")
    value = launch(
        tmp_path,
        f"""
foreach($name in @('CONTEXT.ps1','READ_CORE.ps1','PLAN_CORE.ps1','ARRAY_CORE.ps1','PRIVATE_READ.ps1','PURE_HELPERS.ps1','SAVED_INCOME_CORE.ps1','SAVED_TRADE_CORE.ps1','PREVIEW_CORE.ps1','DRAFT_EXT.ps1')){{. (Join-Path '{tmp_path}' $name)}}
function Reference-AssertPrivateDirectory {{param($Path) if(-not [IO.Directory]::Exists($Path)){{throw 'REFERENCE_TEST_DIRECTORY_MISSING'}}}}
function tar.exe {{& tar @args}}
try{{$plan=Invoke-IncomePlan '{root}' '{pin}';Initialize-PeriodDraftEvidence $plan;if($script:DraftEvidence.day -cne '2026-11-18' -or $script:DraftEvidence.week -cne '2026-11-16' -or $script:DraftEvidence.amount -cne '4998.74183608' -or $script:DraftEvidence.sha256 -cnotmatch '^[0-9a-f]{{64}}$'){{throw 'BAD_CANDIDATE'}};'CANDIDATE_BOUND'}}catch{{$_.Exception.Message}}finally{{if($null -ne $script:DraftSource){{Trades-CloseSaved $script:DraftSource}}}}
""",
    )
    assert value.returncode == 0 and not value.stderr, value.stdout + value.stderr
    assert value.stdout.strip() == expected
    assert before == snapshot(tmp_path / "data")
