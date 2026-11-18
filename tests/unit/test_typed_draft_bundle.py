"""Typed ledger/audit checks, strict source boundaries, and real isolated worker."""

import base64
import copy
import hashlib
import json
import os
import subprocess
import time
import zipfile
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from scripts import build_windows_typed_draft_bundle as builder
from services.execution.demo_models import AttestedPeriodBaseline, DemoLedger
from services.execution.demo_period_draft_model_review import DraftModelBlocked, review_models
from services.execution.models import PaperOrder
from services.risk.paper import AccountValue
from tests.integration.test_demo import settings
from tests.integration.test_paper import signal, snapshot
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch


def package():
    release = os.getenv("TYPED_DRAFT_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git
    ref = real("rev-parse", "HEAD").decode().strip()

    def read(*args):
        if (
            args[0] == "show"
            and args[1].startswith(ref + ":")
            and args[1].split(":", 1)[1]
            in {
                "services/execution/demo_period_draft_model_review.py",
                "tools/windows_demo_typed_draft/MODEL_EXT.ps1",
                "tools/windows_demo_typed_draft/worker.py",
                "tools/windows_demo_typed_draft/README_KO.md",
            }
        ):
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with patch.object(builder, "git", read):
        return builder.package_files("HEAD")


def before_state():
    orders = {}
    for i in range(34):
        orders[str(i)] = PaperOrder(
            client_id=f"PRIVATE_CLIENT_{i}",
            signal=signal(environment="DEMO", purpose="DEMO_VALIDATION", strategy=None),
            snapshot=snapshot(),
            group="BTC",
            quantity="0.0007",
            quantity_step="0.0001",
            price_tick="0.1",
            loss_per_unit="1",
            budget="1",
            remaining="0",
            state="CANCELED",
            position_state="CLOSED",
        )
    ledger = DemoLedger(
        policy=settings().policy,
        value=AccountValue(
            as_of=datetime(2026, 11, 6, tzinfo=UTC), initial="5000", realized="-1.25816392"
        ),
        daily_base="4998.74183608",
        weekly_base="4998.74183608",
        high_water="5000",
        daily_key="2026-11-13",
        weekly_key="2026-11-09",
        risk_state="NORMAL",
        paused=True,
        available_balance="4998.74183608",
        orders=orders,
        requests={
            "e0": "UNKNOWN",
            "e1": "UNKNOWN",
            "sl": "UNKNOWN",
            "ex": "SUBMITTING",
            "resolved_entry": "FILLED",
            "resolved_stop": "CANCELED",
        },
        operations={"old": "PRIVATE_OLD_AUDIT"},
    )
    return ledger.model_dump(mode="json")


def draft():
    before = before_state()
    after = copy.deepcopy(before)
    fields = dict(
        daily_key="2026-11-18",
        weekly_key="2026-11-16",
        daily_base="4998.74183608",
        weekly_base="4998.74183608",
        reconciled=False,
        stream_ready=False,
    )
    after.update(fields)
    audit = dict(
        policy="UNAPPROVED_PERIOD_REVIEW_DRAFT_V1",
        review_id="period-review-draft-" + "d" * 64,
        expected_fence=110,
        proposed_fence=111,
        before_state_sha256="a" * 64,
        candidate_financial_state_sha256="b" * 64,
        evidence_sha256="e" * 64,
        proposed_fields=fields,
        source_accepted=False,
        applied=False,
        execution_approved=False,
    )
    after["operations"][audit["review_id"]] = json.dumps(audit, sort_keys=True)
    report = dict(
        audit_draft=audit,
        candidate_draft_state_sha256="c" * 64,
        candidate_financial_state_sha256="b" * 64,
    )
    expected = {
        n: audit[n]
        for n in (
            "review_id",
            "before_state_sha256",
            "evidence_sha256",
            "expected_fence",
            "proposed_fence",
            "proposed_fields",
        )
    }
    expected["candidate_draft_state_sha256"] = "c" * 64
    return before, after, report, expected


def test_typed_models_accept_valid_draft_preserve_resolved_and_unresolved_history():
    args = draft()
    before = copy.deepcopy(args)
    result = review_models(*args)
    assert (
        result["before_model_validated"]
        and result["candidate_model_validated"]
        and result["audit_model_validated"]
    )
    assert result["completed_adoption_schema_rejected_draft"]
    assert all(
        result[k] is False
        for k in (
            "database_changed",
            "audit_record_written",
            "source_accepted",
            "baseline_adopted",
            "apply_token_created",
            "orders_enabled",
            "live_trading_enabled",
        )
    )
    assert args == before
    assert "PRIVATE_" not in json.dumps(result)
    assert len(args[0]["requests"]) == 6


@pytest.mark.parametrize(
    "fault,code",
    [
        ("before-extra", "MODEL_BEFORE_LEDGER_SCHEMA_INVALID"),
        ("after-extra", "MODEL_CANDIDATE_LEDGER_SCHEMA_INVALID"),
        ("candidate-type", "MODEL_CANDIDATE_LEDGER_SCHEMA_INVALID"),
        ("audit-accepted", "MODEL_AUDIT_SCHEMA_INVALID"),
        ("audit-applied", "MODEL_AUDIT_SCHEMA_INVALID"),
        ("audit-extra", "MODEL_AUDIT_SCHEMA_INVALID"),
        ("audit-coerced-bool", "MODEL_AUDIT_SCHEMA_INVALID"),
        ("high-water", "MODEL_PROTECTED_STATE_CHANGED"),
        ("request", "MODEL_PROTECTED_STATE_CHANGED"),
        ("audit-record", "MODEL_DRAFT_RECORD_MISMATCH"),
        ("old-audit", "MODEL_PRIOR_OPERATIONS_CHANGED"),
        ("fence", "MODEL_EXPECTED_DRAFT_MISMATCH"),
        ("candidate-value", "MODEL_CANDIDATE_VALUE_CHANGED"),
        ("financial-digest", "MODEL_FINANCIAL_DIGEST_MISMATCH"),
    ],
)
def test_schema_and_audit_failures_are_safe_codes_without_identifiers(fault, code):
    b, a, r, e = draft()
    if fault == "before-extra":
        b["PRIVATE_UNKNOWN_FIELD"] = 1
    elif fault == "after-extra":
        a["PRIVATE_UNKNOWN_FIELD"] = 1
    elif fault == "candidate-type":
        a["daily_base"] = "PRIVATE_NOT_A_NUMBER"
    elif fault == "audit-accepted":
        r["audit_draft"]["source_accepted"] = True
    elif fault == "audit-applied":
        r["audit_draft"]["applied"] = True
    elif fault == "audit-extra":
        r["audit_draft"]["PRIVATE_FIELD"] = 1
    elif fault == "audit-coerced-bool":
        r["audit_draft"]["applied"] = 0
    elif fault == "high-water":
        a["high_water"] = "4000"
    elif fault == "request":
        a["requests"]["resolved_entry"] = "NOT_SENT"
    elif fault == "audit-record":
        a["operations"][e["review_id"]] = "{}"
    elif fault == "old-audit":
        a["operations"]["old"] = "OTHER"
    elif fault == "fence":
        e["expected_fence"] = 109
    elif fault == "candidate-value":
        a["daily_base"] = "4000"
    elif fault == "financial-digest":
        r["candidate_financial_state_sha256"] = "0" * 64
    with pytest.raises(DraftModelBlocked) as caught:
        review_models(b, a, r, e)
    assert str(caught.value) == code
    assert "PRIVATE_" not in str(caught.value)


def test_real_completed_adoption_schema_is_distinct_from_draft():
    receipt = AttestedPeriodBaseline(
        review_token="a" * 64,
        source="OPERATOR_ATTESTED_FLAT_PERIODS_WITH_SIGNED_USDT_EVIDENCE",
        day_boundary_utc=datetime(2026, 11, 17, 15, tzinfo=UTC),
        week_boundary_utc=datetime(2026, 11, 15, 15, tzinfo=UTC),
        baseline_usdt="4998.74183608",
        old_daily_key="2026-11-13",
        old_daily_base="4998.74183608",
        old_weekly_key="2026-11-09",
        old_weekly_base="4998.74183608",
        anchor_utc=datetime(2026, 11, 6, tzinfo=UTC),
        signed_evidence_until_utc=datetime(2026, 11, 18, tzinfo=UTC),
        attested_until_utc=datetime(2026, 11, 18, tzinfo=UTC),
        signed_evidence_hash="b" * 64,
        exchange_fingerprint="c" * 64,
    )
    assert receipt.baseline_usdt > 0
    b, a, r, e = draft()
    r["audit_draft"] = receipt.model_dump(mode="json")
    with pytest.raises(DraftModelBlocked, match="MODEL_AUDIT_SCHEMA_INVALID"):
        review_models(b, a, r, e)


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_all_ps_scripts_parse_and_transfer_tampering_refused(tmp_path):
    files = package()
    for n, b in files.items():
        (tmp_path / n).write_bytes(b)
    result = launch(
        tmp_path,
        f"""
foreach($file in Get-ChildItem -LiteralPath '{tmp_path}' -Filter '*.ps1'){{$t=$null;$e=$null;$null=[Management.Automation.Language.Parser]::ParseFile($file.FullName,[ref]$t,[ref]$e);if($e.Count -ne 0){{throw 'PARSE_ERROR'}}}}
'PARSED'
""",
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    pin = hashlib.sha256(files["TRANSFER_MANIFEST.json"]).hexdigest()
    (tmp_path / "model_review.py").write_text("PRIVATE_MUST_NOT_EXECUTE")
    result = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_FINANCIAL.ps1' -ManifestSHA256 '{pin}' -ExecuteRead\nexit $LASTEXITCODE",
    )
    assert result.returncode == 2 and not result.stderr
    assert "PRIVATE_MUST_NOT_EXECUTE" not in result.stdout


def test_original_restore_files_and_sql_pinned_worker_has_no_writes():
    files = package()
    from scripts.build_windows_period_draft_bundle import package_files as old

    oldfiles = old(builder.PIN)
    for n in (
        "account_check.sql",
        "DRAFT_EXT.ps1",
        "SOURCE_SCOPE.ps1",
        "SUPPORT.ps1",
        "restore_worker.py",
        "cold_archive.py",
    ):
        assert files[n] == oldfiles[n]
    worker = files["typed_worker.py"]
    for forbidden in (
        b"UPDATE ",
        b"INSERT ",
        b"DELETE ",
        b"write_bytes",
        b"write_text",
        b"FileAccess]::Write",
    ):
        assert forbidden not in worker
    assert b"SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY" in worker
    config = json.loads(files["CONFIG.json"])
    assert len(config["baseline_files"]) == 89
    assert config["candidate_sha256"] == hashlib.sha256(files["model_review.py"]).hexdigest()
    assert config["sql_sha256"] == hashlib.sha256(files["account_check.sql"]).hexdigest()


@pytest.fixture(scope="module")
def isolated_model_worker_database():
    import uuid

    socket = "test-typed-draft-socket-" + uuid.uuid4().hex
    api = "hybrid-read-code:fb371aeabcc9-ea6d3f52e6bb"
    subprocess.run(["docker", "volume", "create", socket], capture_output=True, check=True)
    db = None
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--network",
                "none",
                "--read-only",
                "--user",
                "0:0",
                "--mount",
                f"type=volume,source={socket},target=/review-socket",
                "--entrypoint",
                "python",
                api,
                "-I",
                "-B",
                "-c",
                'import os; os.chmod("/review-socket",0o777)',
            ],
            check=True,
            capture_output=True,
        )
        image = subprocess.check_output(
            ["docker", "image", "ls", "--filter", "reference=postgres", "--format", "{{.ID}}"],
            text=True,
        ).splitlines()[0]
        db = subprocess.check_output(
            [
                "docker",
                "run",
                "-d",
                "--pull=never",
                "--network",
                "none",
                "--tmpfs",
                "/var/lib/postgresql:rw,mode=1777",
                "--mount",
                f"type=volume,source={socket},target=/var/run/postgresql",
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
            leader = subprocess.run(
                ["docker", "exec", db, "cat", "/proc/1/comm"], capture_output=True, text=True
            )
            ready = subprocess.run(
                ["docker", "exec", db, "pg_isready", "-U", "dev", "-d", "hybrid"],
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
            db,
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
        state = before_state()
        sql = (
            "CREATE TABLE demo_accounts(account_id text primary key,state jsonb,fence bigint not null default 110); INSERT INTO demo_accounts(account_id,state) VALUES ('DEMO-WINDOWS-ISOLATED-BTC-V1','"
            + json.dumps(state).replace("'", "''")
            + "'::jsonb);"
        )
        result = subprocess.run(args, input=sql, text=True, capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr
        yield socket, api, args
    finally:
        if db:
            subprocess.run(["docker", "rm", "-f", db], capture_output=True, check=True)
        subprocess.run(["docker", "volume", "rm", socket], capture_output=True, check=True)


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("valid", None),
        ("module-hash", "MODEL_SOURCE_MISMATCH"),
        ("baseline", "MODEL_BASELINE_MISMATCH"),
        ("expected-digest", "MODEL_EXPECTED_DRAFT_MISMATCH"),
    ],
)
def test_real_nonroot_networkless_worker_reads_models_and_sql_without_writes(
    isolated_model_worker_database, fault, expected
):
    socket, api, args = isolated_model_worker_database
    files = package()
    production = json.loads(files["CONFIG.json"])
    params = dict(
        draft_day="2026-11-18",
        draft_week="2026-11-16",
        draft_amount="4998.74183608",
        draft_evidence="e" * 64,
    )
    cli = []
    for k, v in params.items():
        cli += ["-v", k + "=" + v]
    projected = subprocess.run(
        args + cli,
        input=files["account_check.sql"].decode(),
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert projected.returncode == 0, projected.stderr
    row = json.loads(projected.stdout)
    d = row["transition_draft"]
    expect = {
        n: d[n]
        for n in (
            "review_id",
            "before_state_sha256",
            "candidate_draft_state_sha256",
            "evidence_sha256",
            "expected_fence",
            "proposed_fence",
        )
    }
    expect["proposed_fields"] = d["audit_draft"]["proposed_fields"]
    cfg = dict(
        baseline_files=production["baseline_files"],
        candidate_sha256=production["candidate_sha256"],
        sql_sha256=production["sql_sha256"],
        params=params,
        expected=expect,
    )
    if fault == "module-hash":
        cfg["candidate_sha256"] = "0" * 64
    elif fault == "baseline":
        cfg["baseline_files"][next(iter(cfg["baseline_files"]))] = "0" * 64
    elif fault == "expected-digest":
        cfg["expected"]["candidate_draft_state_sha256"] = "0" * 64
    before = subprocess.check_output(
        args,
        input="SELECT encode(sha256(convert_to(state::text,'UTF8')),'hex'),fence FROM demo_accounts;",
        text=True,
    ).strip()
    bodies = [
        json.dumps(cfg).encode(),
        files["model_review.py"],
        files["account_check.sql"],
        files["typed_worker.py"],
    ]
    c, m, s, w = [base64.b64encode(b).decode() for b in bodies]
    payload = f"import base64,json\nCONFIG=json.loads(base64.b64decode('{c}'))\nMODULE_BODY=base64.b64decode('{m}')\nSQL_BODY=base64.b64decode('{s}')\nexec(compile(base64.b64decode('{w}'),'<typed-worker>','exec'))"
    value = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--interactive",
            "--network",
            "none",
            "--read-only",
            "--memory",
            "768m",
            "--pids-limit",
            "32",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "10001:10001",
            "--mount",
            f"type=volume,source={socket},target=/review-socket,readonly,volume-nocopy",
            "--entrypoint",
            "python",
            api,
            "-I",
            "-B",
            "-",
        ],
        input=payload,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert not value.stderr, value.stderr
    result = json.loads(value.stdout)
    assert value.returncode == (0 if expected is None else 2), result
    assert "PRIVATE_" not in value.stdout
    if expected:
        assert result["failure_category"] == expected
    else:
        assert (
            result["before_model_validated"]
            and result["candidate_model_validated"]
            and result["audit_model_validated"]
        )
        assert (
            result["completed_adoption_schema_rejected_draft"]
            and result["copy_sql_read_only"]
            and result["repeated_read_equal"]
        )
        assert result["baseline_files_verified"] == 89
        assert (
            not result["commit_payload_verified"]
            and not result["source_accepted"]
            and not result["audit_record_written"]
        )
    after = subprocess.check_output(
        args,
        input="SELECT encode(sha256(convert_to(state::text,'UTF8')),'hex'),fence FROM demo_accounts;",
        text=True,
    ).strip()
    assert before == after


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_actual_powershell_helper_admission_payload_and_result(
    isolated_model_worker_database, tmp_path
):
    socket, api, args = isolated_model_worker_database
    files = package()
    for n, b in files.items():
        (tmp_path / n).write_bytes(b)
    params = dict(
        draft_day="2026-11-18",
        draft_week="2026-11-16",
        draft_amount="4998.74183608",
        draft_evidence="e" * 64,
    )
    cli = []
    for k, v in params.items():
        cli += ["-v", k + "=" + v]
    value = subprocess.run(
        args + cli,
        input=files["account_check.sql"].decode(),
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert value.returncode == 0, value.stderr
    (tmp_path / "sql-account.json").write_text(value.stdout)
    api_id = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", api], text=True
    ).strip()
    report = tmp_path / "review"
    report.mkdir()
    script = f"""
foreach($name in @('CONTEXT.ps1','COMMON.ps1','SUPPORT.ps1','MODEL_EXT.ps1')){{. (Join-Path '{tmp_path}' $name)}}
function Assert-RestoreSourceSnapshot {{param($Original,$Helpers,$Frozen) if($Frozen -or $Helpers.Count -ne 1){{throw 'MODEL_TEST_SNAPSHOT_SCOPE_INVALID'}}}}
$script:DraftEvidence=[pscustomobject]@{{day='2026-11-18';week='2026-11-16';amount='4998.74183608';sha256=('e'*64)}}
$config=Get-Content -Raw '{tmp_path}/CONFIG.json'|ConvertFrom-Json
$account=Get-Content -Raw '{tmp_path}/sql-account.json'|ConvertFrom-Json
try{{$id=Invoke-TypedDraftReview '{tmp_path}' $config ([pscustomobject]@{{canonical_images=@('{api_id}')}}) '{socket}' @([pscustomobject]@{{id=('b'*64)}}) @() '{report}' $account;[pscustomobject]@{{helper=$id;review=$script:TypedReview}}|ConvertTo-Json -Depth 5 -Compress}}finally{{if($null -ne $script:CopyDiagnosticId){{$null=& docker rm $script:CopyDiagnosticId}}}}
"""
    result = launch(tmp_path, script)
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["review"]["candidate_model_validated"] and value["review"]["audit_model_validated"]
    assert value["review"]["completed_adoption_schema_rejected_draft"]
    assert (report / "TYPED_DRAFT_REVIEW.json").exists()
    assert "PRIVATE_" not in result.stdout


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_sealed_check_loads_model_extension_and_cannot_start_resources(tmp_path):
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
    ext = tmp_path / "MODEL_EXT.ps1"
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
    for name in ("CONFIG.json", "MODEL_EXT.ps1"):
        m["files"][name] = hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
    raw = json.dumps(m).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(raw)
    value = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_FINANCIAL.ps1' -ManifestSHA256 '{hashlib.sha256(raw).hexdigest()}' -CheckOnly\nexit $LASTEXITCODE",
    )
    assert value.returncode == 0 and not value.stderr, value.stdout + value.stderr
    assert "TYPED_DRAFT_CHECK_PASSED_NO_CLONE_NO_SOURCE_START" in value.stdout
    assert "MUTATION_ATTEMPT" not in value.stdout
