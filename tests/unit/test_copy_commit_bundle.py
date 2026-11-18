"""Real clone-schema commit/rollback tests and sealed Windows host admission."""

import base64
import hashlib
import json
import os
import subprocess
import time
import uuid
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_copy_commit_bundle as builder
from tests.unit import test_typed_draft_bundle as prior
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch


def package():
    release = os.getenv("COPY_COMMIT_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git

    def read(*args):
        if args[0] == "show" and args[1].split(":", 1)[-1] in {
            "services/execution/demo_period_adoption_model.py",
            "tools/windows_demo_copy_commit/worker.py",
            "tools/windows_demo_copy_commit/COMMIT_BODY.ps1",
            "tools/windows_demo_copy_commit/README_KO.md",
        }:
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with patch.object(builder, "git", read):
        return builder.package_files("HEAD")


@pytest.fixture(scope="module")
def database():
    socket = "test-copy-commit-socket-" + uuid.uuid4().hex
    volume = "test-copy-commit-data-" + uuid.uuid4().hex
    api = "hybrid-read-code:fb371aeabcc9-ea6d3f52e6bb"
    db = None
    try:
        for name in (socket, volume):
            subprocess.run(["docker", "volume", "create", name], capture_output=True, check=True)
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
            capture_output=True,
            check=True,
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
                "--restart",
                "no",
                "--label",
                "com.docker.compose.project=demo-copy-review",
                "--mount",
                f"type=volume,source={volume},target=/var/lib/postgresql",
                "--mount",
                f"type=volume,source={socket},target=/review-socket",
                "-e",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                "-e",
                "POSTGRES_USER=dev",
                "-e",
                "POSTGRES_DB=hybrid",
                "-e",
                "PGHOST=/review-socket",
                image,
                "-c",
                "unix_socket_directories=/review-socket,/var/run/postgresql",
                "-c",
                "listen_addresses=",
            ],
            text=True,
        ).strip()
        for _ in range(100):
            leader = subprocess.run(
                ["docker", "exec", db, "cat", "/proc/1/comm"], capture_output=True, text=True
            )
            ready = subprocess.run(
                [
                    "docker",
                    "exec",
                    db,
                    "pg_isready",
                    "-h",
                    "/review-socket",
                    "-U",
                    "dev",
                    "-d",
                    "hybrid",
                ],
                capture_output=True,
            )
            if leader.stdout.strip() == "postgres" and ready.returncode == 0:
                break
            time.sleep(0.25)
        else:
            raise AssertionError(
                "Synthetic clone startup failed: "
                + subprocess.run(["docker", "logs", db], capture_output=True, text=True).stderr
            )
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
            "-h",
            "/review-socket",
            "-U",
            "dev",
            "-d",
            "hybrid",
        ]
        sql = (
            """
        CREATE TABLE demo_accounts(account_id text primary key,state jsonb not null,fence bigint not null,owner text,lease_until timestamptz);
        CREATE TABLE demo_events(event_id varchar(36) primary key,account_id text references demo_accounts(account_id),fence bigint not null,as_of timestamptz not null,kind varchar(80) not null,payload text not null);
        CREATE TABLE demo_outbox(event_id varchar(36) primary key references demo_events(event_id),topic varchar(80) not null);
        """
            + "INSERT INTO demo_accounts(account_id,state,fence) VALUES ('DEMO-WINDOWS-ISOLATED-BTC-V1','"
            + json.dumps(prior.before_state()).replace("'", "''")
            + "'::jsonb,110);"
        )
        result = subprocess.run(args, input=sql, text=True, capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr
        yield socket, api, args
    finally:
        if db:
            subprocess.run(["docker", "rm", "-f", db], capture_output=True, check=True)
        for name in (socket, volume):
            subprocess.run(["docker", "volume", "rm", name], capture_output=True, check=True)


def reset(args):
    sql = (
        "DELETE FROM demo_outbox;DELETE FROM demo_events;UPDATE demo_accounts SET state='"
        + json.dumps(prior.before_state()).replace("'", "''")
        + "'::jsonb,fence=110,owner='expired-owner',lease_until='2026-01-01T00:00:00Z';"
    )
    value = subprocess.run(args, input=sql, text=True, capture_output=True, timeout=10)
    assert value.returncode == 0, value.stderr


def config_for(files, args):
    params = dict(
        draft_day="2026-11-18",
        draft_week="2026-11-16",
        draft_amount="4998.74183608",
        draft_evidence="e" * 64,
    )
    cli = [part for k, v in params.items() for part in ("-v", k + "=" + v)]
    value = subprocess.run(
        args + cli,
        input=files["account_check.sql"].decode(),
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert value.returncode == 0, value.stderr
    row = json.loads(value.stdout)
    d = row["transition_draft"]
    expected = {
        n: d[n]
        for n in (
            "review_id",
            "before_state_sha256",
            "candidate_draft_state_sha256",
            "candidate_financial_state_sha256",
            "evidence_sha256",
            "expected_fence",
            "proposed_fence",
        )
    }
    expected["proposed_fields"] = d["audit_draft"]["proposed_fields"]
    cfg = json.loads(files["CONFIG.json"])
    return dict(
        baseline_files=cfg["baseline_files"],
        candidate_sha256=cfg["commit_candidate_sha256"],
        sql_sha256=cfg["sql_sha256"],
        read_worker_sha256=cfg["read_worker_sha256"],
        params=params,
        expected=expected,
    ), row


def worker_payload(files, cfg):
    values = [
        ("CONFIG", json.dumps(cfg).encode()),
        ("MODULE_BODY", files["commit_model.py"]),
        ("SQL_BODY", files["account_check.sql"]),
        ("READ_WORKER", files["typed_worker.py"]),
    ]
    text = "import base64,json\n"
    for name, body in values:
        encoded = base64.b64encode(body).decode()
        text += (
            name
            + "="
            + ("json.loads(" if name == "CONFIG" else "")
            + f"base64.b64decode('{encoded}')"
            + (")" if name == "CONFIG" else "")
            + "\n"
        )
    worker = base64.b64encode(files["copy_commit_worker.py"]).decode()
    return text + f"exec(compile(base64.b64decode('{worker}'),'<copy-commit-worker>','exec'))"


def launch_worker(database, files, cfg):
    socket, api, _ = database
    return subprocess.run(
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
        input=worker_payload(files, cfg),
        text=True,
        capture_output=True,
        timeout=30,
    )


@pytest.mark.parametrize(
    "fault",
    ["valid", "module-hash", "read-worker-hash", "active-lease", "stale-fence", "event-constraint"],
)
def test_real_worker_commits_only_copy_and_rolls_back_all_failures(database, fault):
    args = database[2]
    reset(args)
    files = package()
    cfg, _ = config_for(files, args)
    if fault == "module-hash":
        cfg["candidate_sha256"] = "0" * 64
    if fault == "read-worker-hash":
        cfg["read_worker_sha256"] = "0" * 64
    if fault == "active-lease":
        subprocess.run(
            args,
            input="UPDATE demo_accounts SET lease_until=clock_timestamp()+interval '10 minutes';",
            text=True,
            capture_output=True,
            check=True,
        )
    if fault == "stale-fence":
        cfg["expected"]["expected_fence"] = 109
    if fault == "event-constraint":
        seed = hashlib.sha256(
            ("COPY_REHEARSAL_ONLY|" + cfg["expected"]["review_id"]).encode()
        ).digest()
        operation = str(uuid.UUID(bytes=seed[:16], version=4))
        value = subprocess.run(
            args,
            input=f"INSERT INTO demo_events VALUES ('{operation}','DEMO-WINDOWS-ISOLATED-BTC-V1',110,clock_timestamp(),'EXISTING','PRIVATE_EXISTING');",
            text=True,
            capture_output=True,
        )
        assert value.returncode == 0, value.stderr
    before = subprocess.check_output(
        args,
        input="SELECT row_to_json(t) FROM (SELECT state::text,fence,owner,lease_until FROM demo_accounts) t;",
        text=True,
    )
    value = launch_worker(database, files, cfg)
    assert not value.stderr, value.stderr
    result = json.loads(value.stdout)
    assert "PRIVATE_" not in value.stdout
    if fault == "valid":
        assert value.returncode == 0, result
        assert result["rollback_cases_verified"] == 3
        assert result["copy_commit_verified"] and result["copy_idempotency_verified"]
        assert result["independent_postcommit_read_verified"]
        assert result["copy_fence"] == 111 and result["events_added"] == result["outbox_added"] == 1
        assert not result["original_database_changed"] and not result["original_source_accepted"]
        state = subprocess.check_output(
            args,
            input="SELECT json_build_object('state',state,'fence',fence) FROM demo_accounts;",
            text=True,
        )
        state = json.loads(state)
        assert state["state"]["orders"] == json.loads(json.loads(before)["state"])["orders"]
        assert state["state"]["requests"] == json.loads(json.loads(before)["state"])["requests"]
        assert state["state"]["value"] == json.loads(json.loads(before)["state"])["value"]
        assert state["state"]["paused"] is True and state["state"]["daily_key"] == "2026-11-18"
    else:
        assert value.returncode == 2, result
        after = subprocess.check_output(
            args,
            input="SELECT row_to_json(t) FROM (SELECT state::text,fence,owner,lease_until FROM demo_accounts) t;",
            text=True,
        )
        assert after == before
        counts = subprocess.check_output(
            args,
            input="SELECT (SELECT count(*) FROM demo_events),(SELECT count(*) FROM demo_outbox);",
            text=True,
        )
        assert counts.strip() == ("1|0" if fault == "event-constraint" else "0|0")


def test_raw_json_numeric_precision_and_prior_operations_survive(database):
    args = database[2]
    reset(args)
    files = package()
    # Numeric JSON values inside existing operations are not valid strings. Put a
    # high-precision JSON number in an existing plain Decimal map instead.
    sql = "UPDATE demo_accounts SET state=jsonb_set(state,'{verified_leverage,BTCUSDT}','0.123456789012345678901234567890'::jsonb);"
    value = subprocess.run(args, input=sql, text=True, capture_output=True)
    assert value.returncode == 0, value.stderr
    cfg, _ = config_for(files, args)
    result = launch_worker(database, files, cfg)
    assert result.returncode == 0, result.stdout + result.stderr
    after = subprocess.check_output(
        args, input="SELECT state#>>'{verified_leverage,BTCUSDT}' FROM demo_accounts;", text=True
    )
    assert after.strip() == "0.123456789012345678901234567890"


def test_package_keeps_restore_workers_sql_and_copy_only_mount_guards():
    files = package()
    old = builder.model_files(builder.PIN)
    for n in (
        "restore_worker.py",
        "cold_archive.py",
        "account_check.sql",
        "SOURCE_SCOPE.ps1",
        "FINANCIAL_EXT.ps1",
        "COPY_EXT.ps1",
        "typed_worker.py",
        "model_review.py",
    ):
        assert files[n] == old[n]
    text = files["COMMIT_EXT.ps1"].decode()
    for term in (
        "Assert-NewClone $Clone $CloneVolume $Original",
        "$Owned -cnotcontains $Clone",
        "MODEL_COPY_ORIGINAL_VOLUME_DENIED",
        "$Socket -cne $script:CopySocket",
    ):
        assert term in text
    assert b"COPY_ONLY_ATOMIC_TRANSACTION_REHEARSAL" in files["COPY_CORE.ps1"]
    manifest = json.loads(files["TRANSFER_MANIFEST.json"])
    assert all(hashlib.sha256(files[n]).hexdigest() == d for n, d in manifest["files"].items())


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_powershell_parse_and_sealed_tampering_refused(tmp_path):
    with patch.object(prior, "package", return_value=package()):
        prior.test_all_ps_scripts_parse_and_transfer_tampering_refused(tmp_path)


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_sealed_precheck_cannot_create_or_start_resources(tmp_path):
    real = prior.launch

    def check(*args, **kw):
        value = real(*args, **kw)
        marker = "COPY_COMMIT_CHECK_PASSED_NO_CLONE_NO_SOURCE_START"
        assert marker in value.stdout, value.stdout + value.stderr
        return subprocess.CompletedProcess(
            value.args,
            value.returncode,
            value.stdout.replace(marker, "TYPED_DRAFT_CHECK_PASSED_NO_CLONE_NO_SOURCE_START"),
            value.stderr,
        )

    with (
        patch.object(prior, "package", return_value=package()),
        patch.object(prior, "launch", check),
    ):
        prior.test_sealed_check_loads_model_extension_and_cannot_start_resources(tmp_path)


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_actual_powershell_admits_new_clone_and_executes_transaction(database, tmp_path):
    socket, api, args = database
    reset(args)
    files = package()
    _, account = config_for(files, args)
    for n, body in files.items():
        (tmp_path / n).write_bytes(body)
    (tmp_path / "account.json").write_text(json.dumps(account))
    db = args[3]
    info = json.loads(subprocess.check_output(["docker", "inspect", db], text=True))[0]
    volume = next(m["Name"] for m in info["Mounts"] if m["Destination"] == "/var/lib/postgresql")
    api_id = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", api], text=True
    ).strip()
    report = tmp_path / "report"
    report.mkdir()
    script = f"""
foreach($name in @('CONTEXT.ps1','COMMON.ps1','SUPPORT.ps1','COPY_EXT.ps1','COMMIT_EXT.ps1')){{. (Join-Path '{tmp_path}' $name)}}
$script:CopySocket='{socket}'
function Assert-RestoreSourceSnapshot {{param($Original,$Helpers,$Frozen) if($Frozen -or $Helpers.Count -ne 2){{throw 'MODEL_COPY_TEST_SNAPSHOT'}}}}
$script:DraftEvidence=[pscustomobject]@{{day='2026-11-18';week='2026-11-16';amount='4998.74183608';sha256=('e'*64)}}
$config=Get-Content -Raw '{tmp_path}/CONFIG.json'|ConvertFrom-Json
$account=Get-Content -Raw '{tmp_path}/account.json'|ConvertFrom-Json
$state=[pscustomobject]@{{canonical_images=@('{api_id}','{info["Image"]}')}}
$original=@([pscustomobject]@{{id=('b'*64);mounts=@()}})
try{{$null=Invoke-CopyCommitReview '{tmp_path}' $config $state '{socket}' $original @('{db}') '{report}' $account '{db}' '{volume}';$script:CopyCommitReview|ConvertTo-Json -Depth 6 -Compress}}finally{{if($null -ne $script:CopyDiagnosticId){{$null=& docker rm $script:CopyDiagnosticId}}}}
"""
    value = launch(tmp_path, script)
    assert value.returncode == 0 and not value.stderr, value.stdout + value.stderr
    result = json.loads(value.stdout)
    assert result["copy_commit_verified"] and result["copy_idempotency_verified"]
    assert result["rollback_cases_verified"] == 3
    assert (report / "COPY_COMMIT_REVIEW.json").exists()
    assert "PRIVATE_" not in value.stdout


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("case", ["not-owned", "socket-mismatch", "original-volume"])
def test_host_refuses_wrong_clone_scope_before_helper_creation(tmp_path, case):
    files = package()
    (tmp_path / "COMMIT_EXT.ps1").write_bytes(files["COMMIT_EXT.ps1"])
    owned = "@()" if case == "not-owned" else "@('clone')"
    socket = "wrong" if case == "socket-mismatch" else "socket"
    mounts = (
        "@([pscustomobject]@{Type='volume';Name='clone-data'})"
        if case == "original-volume"
        else "@()"
    )
    script = f"""
. '{tmp_path}/COMMIT_EXT.ps1'
$script:CopySocket='socket'
function Backup-Docker {{throw 'TEST_HELPER_CREATION_FORBIDDEN'}}
function Assert-NewClone {{param($Id,$Volume,$Original)}}
function Read-Container {{param($Id) return [pscustomobject]@{{running=$true;image=('sha256:'+('c'*64))}}}}
function Resolve-RestoreImageMatch {{param($Reference,$Expected) return $true}}
try{{$null=Invoke-CopyCommitReview '' $null ([pscustomobject]@{{canonical_images=@('a','b')}}) '{socket}' @([pscustomobject]@{{id='original';mounts={mounts}}}) {owned} '' $null 'clone' 'clone-data';throw 'TEST_GUARD_FAILED'}}catch{{if($_.Exception.Message -notin @('MODEL_COPY_CLONE_BINDING_INVALID','MODEL_COPY_ORIGINAL_VOLUME_DENIED')){{throw}};'REFUSED_BEFORE_CREATE'}}
"""
    result = launch(tmp_path, script)
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert result.stdout.strip() == "REFUSED_BEFORE_CREATE"
