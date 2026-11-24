"""Copy-only ownership projection, protected JSONB and unapproved protocol gates."""

import copy
import hashlib
import json
import os
import select
import subprocess
import time
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_owner_review_bundle as builder
from tests.unit import test_copy_financial_bundle as prior
from tests.unit.test_cold_backup_executor import PWSH, ROOT


def package():
    release = os.getenv("OWNER_REVIEW_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git

    def read(*args):
        if args[0] == "show":
            path = args[1].split(":", 1)[-1]
            if path.startswith("tools/windows_demo_owner_review/") or path in {
                "docs/reviews/20261124/CONDITIONAL_CURRENT_WINDOWS_OBSERVATION.json",
                "docs/reviews/20261124/SOURCE_ACCEPTANCE_REVIEW.json",
                "docs/reviews/20261124/SOURCE_ACCEPTANCE_RECEIPT.json",
            }:
                return (ROOT / path).read_bytes()
        return real(*args)

    with patch.object(builder, "git", read):
        return builder.package_files("HEAD")


def owner_account(owner=True, lease="EXPIRED"):
    *_, a = prior.fixture()
    a["owner_review"] = dict(
        fence=110,
        owner_present=owner,
        owner_sha256="b" * 64 if owner else None,
        lease_present=lease != "ABSENT",
        lease_until_utc=None if lease == "ABSENT" else "2026-11-01T00:00:00.000000Z",
        lease_state=lease,
        ownership_row_sha256="c" * 64,
        lease_clock_scope="COPY_DATABASE_STATEMENT_TIMESTAMP",
        original_owner_verified=False,
        row_lock_acquired=False,
    )
    a["period_candidate"] = dict(
        context="UNAPPROVED_ORIGINAL_RECOVERY_DESIGN_ONLY",
        proposed_fields=dict(
            daily_key="2026-11-24",
            weekly_key="2026-11-23",
            daily_base="4998.74183608",
            weekly_base="4998.74183608",
            reconciled=False,
            stream_ready=False,
        ),
        before_state_pg_text_sha256=a["account_state_sha256"],
        candidate_financial_pg_text_sha256="d" * 64,
        changed_top_level_fields=["daily_key", "weekly_key"],
        protected_jsonb_preserved=True,
        orders_requests_operations_preserved=True,
        paused_preserved=True,
        candidate_paused=True,
        audit_record_created=False,
        commit_payload_verified=False,
        apply_token_created=False,
    )
    return a


def validate(tmp_path, a, plan=False, before_hash=None):
    real = prior.launch

    def run(path, body):
        body = body.replace(
            f". '{tmp_path}/FINANCIAL_EXT.ps1'",
            f". '{tmp_path}/FINANCIAL_EXT.ps1'\n. '{tmp_path}/OWNER_EXT.ps1'",
        )
        if plan:
            body = body.replace(
                ";'ACCEPTED'", ";$script:OwnerProtocolReview|ConvertTo-Json -Depth 12 -Compress"
            )
        if before_hash:
            assert len(before_hash) == 64 and all(c in "0123456789abcdef" for c in before_hash)
            body = body.replace(
                "account_state_sha256=('a'*64)", f"account_state_sha256='{before_hash}'"
            )
        return real(path, body)

    with patch.object(prior, "package", package), patch.object(prior, "launch", run):
        return prior.validate(tmp_path, a)


def test_seal_and_original_restore_admission_are_preserved():
    f = package()
    old = builder.financial_files(builder.PIN)
    for n in (
        "CONTEXT.ps1",
        "SUPPORT.ps1",
        "SOURCE_SCOPE.ps1",
        "COPY_EXT.ps1",
        "COMPARE_ADMISSION.ps1",
        "FINANCIAL_EXT.ps1",
        "restore_worker.py",
        "cold_archive.py",
        "READ_CORE.ps1",
        "BASELINE_CORE.ps1",
        "PLAN_CORE.ps1",
    ):
        assert f[n] == old[n]
    m = json.loads(f["TRANSFER_MANIFEST.json"])
    assert all(hashlib.sha256(f[n]).hexdigest() == h for n, h in m["files"].items())
    sql = f["account_check.sql"].decode()
    assert "REPEATABLE READ READ ONLY" in sql and "statement_timestamp()" in sql
    for forbidden in (
        "UPDATE ",
        "INSERT ",
        "DELETE ",
        "FOR UPDATE",
        "pg_advisory_lock",
        "json_build_object('owner',owner",
    ):
        assert forbidden not in sql
    assert "jsonb_build_object('owner',owner" in sql  # only input to a digest
    assert "original_owner_verified" in sql and "row_lock_acquired" in sql
    oldcore = old["COPY_CORE.ps1"].decode()
    core = f["COPY_CORE.ps1"].decode()
    for start, end in [
        (
            "        $stage='CREATE_FRESH_CLONE_VOLUMES'",
            "        $stage='READ_ONLY_ACCOUNT_ON_COPY'",
        ),
        ("        Stop-OwnedClone $clone", "        $result=[ordered]@{"),
    ]:
        if start.startswith("        Stop-"):
            # Only append the private draft after the existing stop/reverification checks.
            expected = oldcore.split(start, 1)[1].split(end, 1)[0]
            actual = core.split(start, 1)[1].split(
                "        Write-BackupJson (Join-Path $directory 'OWNER_PROTOCOL_DRAFT.json')", 1
            )[0]
            assert actual == expected
        else:
            assert (
                core.split(start, 1)[1].split(end, 1)[0]
                == oldcore.split(start, 1)[1].split(end, 1)[0]
            )


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "owner,lease,classification",
    [
        (False, "ABSENT", "UNOWNED_NO_LEASE"),
        (False, "EXPIRED", "RELEASED_EXPIRED_LEASE"),
        (True, "EXPIRED", "EXPIRED_OWNER_REQUIRES_EXPLICIT_TAKEOVER"),
        (True, "ACTIVE", "ACTIVE_LEASE_BLOCKED"),
        (False, "ACTIVE", "ACTIVE_LEASE_BLOCKED"),
        (True, "ABSENT", "OWNER_WITHOUT_LEASE_BLOCKED"),
    ],
)
def test_all_owner_states_produce_drafts_without_authority(tmp_path, owner, lease, classification):
    a = owner_account(owner, lease)
    original = copy.deepcopy(a)
    r = json.loads(validate(tmp_path, a, True))
    assert r["classification"] == classification and a == original
    assert r["reference_fence"] == 110 and r["proposed_offline_recovery_fence"] == 111
    assert len(r["blockers"]) >= 6
    for k in (
        "expired_lease_alone_authorizes_takeover",
        "current_original_sql_queried",
        "current_original_fence_verified",
        "original_owner_verified",
        "original_row_lock_acquired",
        "owner_lease_takeover_authorized",
        "original_database_start_authorized",
        "original_database_write_authorized",
        "commit_payload_verified",
        "apply_token_created",
        "baseline_adopted",
        "original_apply_ready",
        "request_resolution_authorized",
        "resend_authorized",
        "execution_approved",
        "orders_enabled",
        "live_trading_enabled",
    ):
        assert r[k] is False


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "fault,code",
    [
        ("fence", "OWNER_REFERENCE_FENCE_CHANGED"),
        ("bool-fence", "OWNER_REFERENCE_FENCE_CHANGED"),
        ("owner-hash", "OWNER_IDENTITY_HASH_INVALID"),
        ("owner-null", "OWNER_IDENTITY_HASH_INVALID"),
        ("lease", "OWNER_LEASE_SCHEMA_INVALID"),
        ("lease-date", "OWNER_LEASE_SCHEMA_INVALID"),
        ("scope", "OWNER_SQL_SCOPE_INVALID"),
        ("original-claim", "OWNER_SQL_SCOPE_INVALID"),
        ("lock-claim", "OWNER_SQL_SCOPE_INVALID"),
        ("owner-extra", "OWNER_SQL_SCHEMA_INVALID"),
        ("missing", "OWNER_SQL_SCHEMA_INVALID"),
        ("before-hash", "OWNER_CANDIDATE_HASH_INVALID"),
        ("candidate-same", "OWNER_CANDIDATE_HASH_INVALID"),
        ("protected", "OWNER_PROTECTED_STATE_CHANGED"),
        ("requests", "OWNER_PROTECTED_STATE_CHANGED"),
        ("unpause", "OWNER_PROTECTED_STATE_CHANGED"),
        ("token", "OWNER_DRAFT_AUTHORITY_INVALID"),
        ("period", "OWNER_CANDIDATE_FIELDS_INVALID"),
        ("amount", "OWNER_CANDIDATE_FIELDS_INVALID"),
        ("reconciled", "OWNER_CANDIDATE_FIELDS_INVALID"),
        ("changed", "OWNER_PROTECTED_STATE_CHANGED"),
        ("changes-missing", "OWNER_CHANGED_FIELDS_INVALID"),
        ("changes-duplicate", "OWNER_CHANGED_FIELDS_INVALID"),
        ("financial", "FINANCIAL_PERIOD_OR_HIGH_WATER_MISMATCH"),
        ("initial", "FINANCIAL_VALUE_COMPONENT_MISMATCH"),
        ("request-state", "FINANCIAL_REQUEST_SCOPE_CHANGED"),
        ("paused", "FINANCIAL_SQL_STATE_MISMATCH"),
        ("readonly", "FINANCIAL_SQL_STATE_MISMATCH"),
        ("root-extra", "FINANCIAL_SQL_SCHEMA_INVALID"),
    ],
)
def test_changed_values_or_authority_claims_fail_closed(tmp_path, fault, code):
    a = owner_account()
    o = a["owner_review"]
    p = a["period_candidate"]
    if fault == "fence":
        o["fence"] = 111
    elif fault == "bool-fence":
        o["fence"] = True
    elif fault == "owner-hash":
        o["owner_sha256"] = "PRIVATE_RAW_OWNER"
    elif fault == "owner-null":
        o["owner_present"] = False
    elif fault == "lease":
        o["lease_state"] = "ABSENT"
    elif fault == "lease-date":
        o["lease_until_utc"] = "2026-99-99T00:00:00.000000Z"
    elif fault == "scope":
        o["lease_clock_scope"] = "CURRENT_ORIGINAL"
    elif fault == "original-claim":
        o["original_owner_verified"] = True
    elif fault == "lock-claim":
        o["row_lock_acquired"] = True
    elif fault == "owner-extra":
        o["raw_owner"] = "PRIVATE_RAW_OWNER"
    elif fault == "missing":
        del a["owner_review"]
    elif fault == "before-hash":
        p["before_state_pg_text_sha256"] = "b" * 64
    elif fault == "candidate-same":
        p["candidate_financial_pg_text_sha256"] = a["account_state_sha256"]
    elif fault == "protected":
        p["protected_jsonb_preserved"] = False
    elif fault == "requests":
        p["orders_requests_operations_preserved"] = False
    elif fault == "unpause":
        p["candidate_paused"] = False
    elif fault == "token":
        p["apply_token_created"] = True
    elif fault == "period":
        p["proposed_fields"]["daily_key"] = "2026-11-25"
    elif fault == "amount":
        p["proposed_fields"]["weekly_base"] = "5000"
    elif fault == "reconciled":
        p["proposed_fields"]["reconciled"] = True
    elif fault == "changed":
        p["changed_top_level_fields"].append("requests")
    elif fault == "changes-missing":
        p["changed_top_level_fields"] = []
    elif fault == "changes-duplicate":
        p["changed_top_level_fields"].append("weekly_key")
    elif fault == "financial":
        a["financial"]["high_water"] = "1"
    elif fault == "initial":
        a["financial"]["initial"] = "1"
    elif fault == "request-state":
        a["request_states"][0]["request_state"] = "NOT_SENT"
    elif fault == "paused":
        a["paused"] = False
    elif fault == "readonly":
        a["transaction_read_only"] = "off"
    elif fault == "root-extra":
        a["raw_owner"] = "PRIVATE_OWNER_SENTINEL"
    assert validate(tmp_path, a) == code


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_offline_check_keeps_no_create_no_start_boundary(tmp_path):
    real = prior.launch

    def run(path, body):
        v = real(path, body)
        assert "OWNER_REVIEW_CHECK_PASSED_NO_CLONE_NO_ORIGINAL_START" in v.stdout, (
            v.stdout + v.stderr
        )
        v.stdout = v.stdout.replace(
            "OWNER_REVIEW_CHECK_PASSED_NO_CLONE_NO_ORIGINAL_START",
            "COPY_FINANCIAL_CHECK_PASSED_NO_CLONE_NO_SOURCE_START",
        )
        return v

    with patch.object(prior, "package", package), patch.object(prior, "launch", run):
        prior.test_sealed_check_loads_new_scope_without_creating_or_starting(tmp_path)


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "name",
    [
        "OWNER_EXT.ps1",
        "account_check.sql",
        "SOURCE_ACCEPTANCE.json",
        "CONDITIONAL_CURRENT_OBSERVATION.json",
    ],
)
def test_tamper_blocks_before_docker_access(tmp_path, name):
    f = package()
    for n, b in f.items():
        (tmp_path / n).write_bytes(b)
    (tmp_path / name).write_text("PRIVATE_DO_NOT_RUN")
    v = prior.launch(
        tmp_path,
        f"function docker {{throw 'MUST_NOT_CALL_DOCKER'}}\n& '{tmp_path}/RUN_FINANCIAL.ps1' -ManifestSHA256 '{hashlib.sha256(f['TRANSFER_MANIFEST.json']).hexdigest()}' -ExecuteRead\nexit $LASTEXITCODE",
    )
    assert v.returncode == 2 and not v.stderr
    assert "PRIVATE_DO_NOT_RUN" not in v.stdout and "MUST_NOT_CALL_DOCKER" not in v.stdout


@pytest.fixture(scope="module")
def database():
    images = subprocess.check_output(
        ["docker", "image", "ls", "--filter", "reference=postgres", "--format", "{{.ID}}"],
        text=True,
    ).splitlines()
    assert images
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
            images[0],
        ],
        text=True,
    ).strip()
    try:
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
            raise AssertionError("synthetic PostgreSQL did not start")
        state, *_ = prior.fixture()
        state["future_extension"] = {"opaque": "PRIVATE_JSONB_SENTINEL", "array": [1, False, None]}
        setup = (
            "CREATE TABLE demo_accounts(account_id text primary key,state jsonb not null,fence bigint not null,owner text,lease_until timestamptz); INSERT INTO demo_accounts VALUES ('DEMO-WINDOWS-ISOLATED-BTC-V1','"
            + json.dumps(state).replace("'", "''")
            + "'::jsonb,110,NULL,NULL);"
        )
        v = subprocess.run(args, input=setup, capture_output=True, text=True, timeout=10)
        assert v.returncode == 0, v.stderr
        yield args
    finally:
        subprocess.run(["docker", "rm", "-f", ident], check=True, capture_output=True)


def sql(args, text):
    v = subprocess.run(args, input=text, text=True, capture_output=True, timeout=10)
    assert v.returncode == 0, v.stderr
    return v.stdout.strip()


@pytest.mark.parametrize(
    "owner,lease",
    [
        (False, "ABSENT"),
        (False, "EXPIRED"),
        (False, "ACTIVE"),
        (True, "ABSENT"),
        (True, "EXPIRED"),
        (True, "ACTIVE"),
    ],
)
def test_real_postgresql_two_reads_preserve_all_account_bytes_and_metadata(
    database, tmp_path, owner, lease
):
    args = database
    own = "'PRIVATE_OWNER_SENTINEL'" if owner else "NULL"
    until = {
        "ABSENT": "NULL",
        "EXPIRED": "'2020-01-01T00:00:00Z'",
        "ACTIVE": "'2099-01-01T00:00:00Z'",
    }[lease]
    sql(args, f"UPDATE demo_accounts SET owner={own},lease_until={until};")
    snapshot = "SELECT jsonb_build_object('state',state::text,'fence',fence,'owner',owner,'lease',lease_until)::text FROM demo_accounts;"
    before = sql(args, snapshot)
    query = package()["account_check.sql"].decode()
    rows = [sql(args, query) for _ in range(2)]
    assert rows[0] == rows[1] and before == sql(args, snapshot)
    r = json.loads(rows[0])
    o = r["owner_review"]
    p = r["period_candidate"]
    assert o["owner_present"] is owner and o["lease_state"] == lease
    assert o["fence"] == 110 and not o["original_owner_verified"] and not o["row_lock_acquired"]
    assert p["changed_top_level_fields"] == ["daily_key", "weekly_key"]
    assert p["protected_jsonb_preserved"] and p["orders_requests_operations_preserved"]
    assert p["proposed_fields"]["daily_key"] == "2026-11-24"
    assert p["candidate_financial_pg_text_sha256"] != p["before_state_pg_text_sha256"]
    assert (
        r["transaction_read_only"] == "on" and r["orders"] == 34 and r["unresolved_requests"] == 4
    )
    assert "PRIVATE_OWNER_SENTINEL" not in rows[0] and "PRIVATE_JSONB_SENTINEL" not in rows[0]
    if owner:
        assert o["owner_sha256"] == hashlib.sha256(b"PRIVATE_OWNER_SENTINEL").hexdigest()
    if PWSH.exists():
        review = json.loads(validate(tmp_path, r, True, before_hash=r["account_state_sha256"]))
        assert review["copied_ownership"]["fence"] == 110
        assert not review["original_apply_ready"]


def test_real_readonly_projection_connection_cannot_mutate_or_lock_rows(database):
    for statement in [
        "UPDATE demo_accounts SET fence=fence+1",
        "SELECT fence FROM demo_accounts FOR UPDATE",
    ]:
        v = subprocess.run(
            database,
            input="BEGIN READ ONLY;" + statement + ";COMMIT;",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert v.returncode != 0 and "read-only" in v.stderr


def test_synthetic_two_session_row_and_advisory_lock_prototype(database):
    # Feasibility test only; no original writer is included in the delivered bundle.
    first = subprocess.Popen(
        database,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        first.stdin.write(
            "BEGIN;\n\\o /dev/null\nSELECT pg_advisory_xact_lock(241124110); SELECT fence FROM demo_accounts FOR UPDATE;\n\\o\n\\echo LOCK_HELD\n"
        )
        first.stdin.flush()
        deadline = time.monotonic() + 5
        seen = False
        while time.monotonic() < deadline:
            readable, _, _ = select.select([first.stdout], [], [], 0.25)
            if readable and first.stdout.readline().strip() == "LOCK_HELD":
                seen = True
                break
        assert seen, "synthetic first session did not hold locks"
        assert sql(database, "BEGIN;SELECT pg_try_advisory_xact_lock(241124110);ROLLBACK;") == "f"
        blocked = subprocess.run(
            database,
            input="BEGIN;SET LOCAL lock_timeout='200ms';SELECT fence FROM demo_accounts FOR UPDATE;ROLLBACK;",
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert blocked.returncode != 0 and "lock timeout" in blocked.stderr
    finally:
        first.communicate("ROLLBACK;\n", timeout=5)
