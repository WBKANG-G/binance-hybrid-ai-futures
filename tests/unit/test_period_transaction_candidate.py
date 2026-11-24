"""Real disposable PostgreSQL rollback, fencing, raw preservation and lost-reply tests."""

import copy
import json
import subprocess
import time
from dataclasses import replace
from datetime import UTC, timedelta
from uuid import uuid4

import psycopg
import pytest

from services.execution import demo_period_transaction_candidate as writer
from services.execution.demo_period_adoption_model import validate_real_completion
from tests.unit.test_typed_draft_bundle import before_state


@pytest.fixture(scope="module")
def database(tmp_path_factory):
    root = tmp_path_factory.mktemp("period-copy-socket")
    root.chmod(0o755)
    socket = root / "socket"
    socket.mkdir()
    socket.chmod(0o777)
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
            "--mount",
            f"type=bind,source={socket},target=/review-socket",
            "-e",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            "-e",
            "POSTGRES_USER=dev",
            "-e",
            "POSTGRES_DB=hybrid",
            image,
            "-c",
            "listen_addresses=",
            "-c",
            "unix_socket_directories=/review-socket,/var/run/postgresql",
        ],
        text=True,
    ).strip()

    def connect():
        return psycopg.connect(
            host=str(socket),
            user="dev",
            dbname="hybrid",
            autocommit=True,
            connect_timeout=2,
            options="-c default_transaction_read_only=on -c timezone=UTC",
        )

    try:
        for _ in range(100):
            leader = subprocess.run(
                ["docker", "exec", ident, "cat", "/proc/1/comm"], capture_output=True, text=True
            )
            if leader.stdout.strip() == "postgres":
                try:
                    with connect() as c:
                        c.execute("SELECT 1")
                    break
                except psycopg.Error:
                    pass
            time.sleep(0.1)
        else:
            raise AssertionError("synthetic postgres unavailable")
        with connect() as c:
            c.execute("SET default_transaction_read_only=off")
            c.execute(
                "CREATE TABLE demo_accounts(account_id text primary key,state jsonb not null,fence bigint not null,owner text,lease_until timestamptz); CREATE TABLE demo_events(event_id text primary key,account_id text references demo_accounts(account_id),fence bigint,as_of timestamptz,kind text,payload text);CREATE TABLE demo_outbox(event_id text primary key references demo_events(event_id),topic text)"
            )
        yield connect
    finally:
        subprocess.run(["docker", "rm", "-f", ident], capture_output=True, check=True)


@pytest.fixture
def case(database):
    state = before_state()
    state["operations"]["opaque-future-operation"] = (
        '{"decimal_literal":"1.200","nested":[null,false,{"x":7}]}'
    )
    with database() as c:
        c.execute("SET default_transaction_read_only=off")
        c.execute("DELETE FROM demo_outbox;DELETE FROM demo_events;DELETE FROM demo_accounts")
        c.execute(
            "INSERT INTO demo_accounts VALUES(%s,%s::jsonb,110,NULL,'2020-01-01T00:00:00Z')",
            (writer.ACCOUNT, json.dumps(state)),
        )
        c.execute(
            "UPDATE demo_accounts SET state=jsonb_set(state,'{isolated_wallets,SYNTHETIC}','1.200'::jsonb)"
        )
        text, fence, owner, lease, now = c.execute(
            "SELECT state::text,fence,owner,lease_until,clock_timestamp() FROM demo_accounts"
        ).fetchone()
        at = now.astimezone(UTC) + timedelta(hours=9)
        fields = dict(
            daily_key=str(at.date()),
            weekly_key=str((at - timedelta(days=at.weekday())).date()),
            daily_base="4998.74183608",
            weekly_base="4998.74183608",
            reconciled=False,
            stream_ready=False,
        )
        candidate = c.execute(
            "SELECT (%s::jsonb || %s::jsonb)::text", (text, json.dumps(fields))
        ).fetchone()[0]
        with c.cursor() as cur:
            meta = writer._meta(cur, text, fence, owner, lease)
    nowms = int(now.timestamp() * 1000)
    plan = writer.Plan(
        str(uuid4()), writer.sha(text), meta, writer.sha(candidate), 110, fields, "e" * 64
    )
    witness = writer.SyntheticCopyWitness(nowms - 1000, nowms, nowms - 60000, time.monotonic())
    return database, plan, witness


def snapshot(connect):
    with connect() as c:
        row = c.execute("SELECT state::text,fence,owner,lease_until FROM demo_accounts").fetchone()
        return (
            row,
            c.execute("SELECT count(*) FROM demo_events").fetchone()[0],
            c.execute("SELECT count(*) FROM demo_outbox").fetchone()[0],
        )


def test_original_entry_is_disabled_before_connection_or_any_caller_supplied_approval():
    def forbidden():
        raise AssertionError("must not connect")

    with pytest.raises(
        writer.PeriodWriteBlocked, match="ORIGINAL_PERIOD_WRITE_APPROVAL_AND_HOST_BOUNDARY_REQUIRED"
    ):
        writer.apply_original_period(
            forbidden, authorization={"original_database_write_authorized": True}
        )


@pytest.mark.parametrize("fault", ["AFTER_ACCOUNT", "AFTER_EVENT", "AFTER_OUTBOX"])
def test_each_partial_failure_rolls_back_state_fence_owner_lease_events_outbox(case, fault):
    connect, p, w = case
    before = snapshot(connect)
    with connect() as c, pytest.raises(writer.PeriodWriteBlocked, match="PERIOD_INJECTED_FAILURE"):
        writer.rehearse_copy_transaction(c, p, w, copy_only=True, fault=fault)
    assert snapshot(connect) == before


def test_commit_preserves_supported_opaque_raw_fields_and_owner_lease_and_is_idempotent(case):
    connect, p, w = case
    before = snapshot(connect)
    with connect() as c:
        assert writer.rehearse_copy_transaction(c, p, w, copy_only=True) == "COMMITTED"
        assert writer.rehearse_copy_transaction(c, p, w, copy_only=True) == "ALREADY_COMMITTED"
    after = snapshot(connect)
    assert after[0][1] == 111 and after[0][2:] == before[0][2:] and after[1:] == (1, 1)
    assert "1.200" in after[0][0]
    state = json.loads(after[0][0])
    record = json.loads(state["operations"][p.operation_id])
    assert (
        state["high_water"] == json.loads(before[0][0])["high_water"]
        and state["value"] == json.loads(before[0][0])["value"]
    )
    assert (
        state["orders"] == json.loads(before[0][0])["orders"]
        and state["requests"] == json.loads(before[0][0])["requests"]
    )
    assert state["paused"] and not state["reconciled"] and not state["stream_ready"]
    with pytest.raises(ValueError, match="MODEL_REHEARSAL_CANNOT_AUTHORIZE"):
        validate_real_completion(record, verified_acceptance_sha256=writer.COPY_ACCEPTANCE)
    with connect() as c:
        assert writer.inspect_copy_outcome(c, p)["status"] == "COPY_COMMIT_VERIFIED"


def test_unknown_top_level_fields_are_refused_without_silently_dropping_them(case):
    from pydantic import ValidationError

    connect, p, w = case
    with connect() as c:
        c.execute("SET default_transaction_read_only=off")
        c.execute("UPDATE demo_accounts SET state=state || '{\"future_unknown\":1.200}'::jsonb")
        text, fence, owner, lease = c.execute(
            "SELECT state::text,fence,owner,lease_until FROM demo_accounts"
        ).fetchone()
        with c.cursor() as cur:
            meta = writer._meta(cur, text, fence, owner, lease)
        candidate = c.execute(
            "SELECT (%s::jsonb || %s::jsonb)::text", (text, json.dumps(p.proposed_fields))
        ).fetchone()[0]
    p = replace(
        p,
        before_state_sha256=writer.sha(text),
        ownership_row_sha256=meta,
        candidate_financial_state_sha256=writer.sha(candidate),
    )
    before = snapshot(connect)
    with connect() as c, pytest.raises(ValidationError):
        writer.rehearse_copy_transaction(c, p, w, copy_only=True)
    assert snapshot(connect) == before


@pytest.mark.parametrize(
    "fault",
    [
        "before",
        "fence",
        "meta",
        "candidate",
        "owner",
        "active",
        "lease-changed",
        "paused",
        "stale-source",
        "clock",
        "reference-gap",
        "period",
        "copy-scope",
        "bool-fence",
    ],
)
def test_guards_reject_stale_mismatched_or_unauthorized_inputs_without_writes(case, fault):
    connect, p, w = case
    if fault == "before":
        p = replace(p, before_state_sha256="f" * 64)
    elif fault == "fence":
        p = replace(p, expected_fence=111)
    elif fault == "meta":
        p = replace(p, ownership_row_sha256="f" * 64)
    elif fault == "candidate":
        p = replace(p, candidate_financial_state_sha256="f" * 64)
    elif fault == "bool-fence":
        p = replace(p, expected_fence=True)
    elif fault in {"owner", "active", "lease-changed", "paused"}:
        with connect() as c:
            c.execute("SET default_transaction_read_only=off")
            if fault == "owner":
                c.execute("UPDATE demo_accounts SET owner='expired-but-not-released'")
            elif fault == "active":
                c.execute("UPDATE demo_accounts SET lease_until='2099-01-01T00:00:00Z'")
            elif fault == "lease-changed":
                c.execute("UPDATE demo_accounts SET lease_until='2021-01-01T00:00:00Z'")
            else:
                c.execute("UPDATE demo_accounts SET state=jsonb_set(state,'{paused}','false')")
        if fault == "paused":
            with connect() as c:
                text, fe, ow, le = c.execute(
                    "SELECT state::text,fence,owner,lease_until FROM demo_accounts"
                ).fetchone()
                with c.cursor() as cur:
                    meta = writer._meta(cur, text, fe, ow, le)
                p = replace(p, before_state_sha256=writer.sha(text), ownership_row_sha256=meta)
    elif fault == "stale-source":
        w = replace(w, observation_end_ms=w.server_now_ms - 300001)
    elif fault == "clock":
        w = replace(w, server_now_ms=w.server_now_ms + 6000)
    elif fault == "reference-gap":
        w = replace(w, reference_start_ms=w.server_now_ms - 604800001)
    elif fault == "period":
        fields = copy.deepcopy(p.proposed_fields)
        fields.update(daily_key="2099-01-01", weekly_key="2098-12-29")
        p = replace(p, proposed_fields=fields)
    before = snapshot(connect)
    with connect() as c, pytest.raises((writer.PeriodWriteBlocked, ValueError)):
        writer.rehearse_copy_transaction(c, p, w, copy_only=fault != "copy-scope")
    assert snapshot(connect) == before


def test_freshness_loss_after_outbox_staging_rolls_back_everything(case, monkeypatch):
    connect, p, w = case
    before = snapshot(connect)
    real = writer._fresh
    calls = []

    def check(witness, now, fields):
        calls.append(1)
        return real(witness, now + timedelta(minutes=6) if len(calls) == 2 else now, fields)

    monkeypatch.setattr(writer, "_fresh", check)
    with connect() as c, pytest.raises(writer.PeriodWriteBlocked):
        writer.rehearse_copy_transaction(c, p, w, copy_only=True)
    assert len(calls) == 2 and snapshot(connect) == before


def test_event_id_collision_rolls_back_account_and_fence(case):
    connect, p, w = case
    with connect() as c:
        c.execute("SET default_transaction_read_only=off")
        c.execute(
            "INSERT INTO demo_events VALUES(%s,%s,1,clock_timestamp(),'OTHER','other')",
            (p.operation_id, writer.ACCOUNT),
        )
    before = snapshot(connect)
    with connect() as c, pytest.raises(psycopg.errors.UniqueViolation):
        writer.rehearse_copy_transaction(c, p, w, copy_only=True)
    assert snapshot(connect) == before


def test_advisory_and_row_locks_exclude_other_connections(case):
    connect, p, w = case
    for lock in (
        "SELECT pg_advisory_xact_lock(%s)",
        "SELECT fence FROM demo_accounts WHERE account_id=%s FOR UPDATE",
    ):
        with connect() as first, connect() as second:
            first.execute("SET default_transaction_read_only=off")
            first.execute("BEGIN")
            first.execute(lock, (writer.LOCK_KEY if "advisory" in lock else writer.ACCOUNT,))
            before = snapshot(connect)
            with pytest.raises(psycopg.errors.LockNotAvailable):
                writer.rehearse_copy_transaction(second, p, w, copy_only=True)
            first.execute("ROLLBACK")
        assert snapshot(connect) == before


def test_lost_successful_commit_reply_requires_readonly_resolution_not_retry(case):
    connect, p, w = case
    with connect() as c:

        class Proxy:
            autocommit = True

            def cursor(self):
                return c.cursor()

            def transaction(self):
                real = c.transaction()

                class Lost:
                    def __enter__(self):
                        return real.__enter__()

                    def __exit__(self, *args):
                        result = real.__exit__(*args)
                        if args[0] is None:
                            raise OSError("SYNTHETIC_LOST_COMMIT_REPLY")
                        return result

                return Lost()

        with pytest.raises(writer.CommitOutcomeUnknown):
            writer.rehearse_copy_transaction(Proxy(), p, w, copy_only=True)
    assert snapshot(connect)[1:] == (1, 1)
    with connect() as c:
        r = writer.inspect_copy_outcome(c, p)
    assert r["status"] == "COPY_COMMIT_VERIFIED" and not r["retry_authorized"]
    assert snapshot(connect)[1:] == (1, 1)


def test_not_found_or_partial_evidence_never_authorizes_resubmission(case):
    connect, p, w = case
    with connect() as c:
        assert writer.inspect_copy_outcome(c, p)["status"] == "NOT_FOUND_UNRESOLVED_NO_RETRY"
    with connect() as c:
        writer.rehearse_copy_transaction(c, p, w, copy_only=True)
    with connect() as c:
        c.execute("SET default_transaction_read_only=off")
        c.execute("DELETE FROM demo_outbox")
    with connect() as c:
        r = writer.inspect_copy_outcome(c, p)
    assert r["status"] == "PRESENT_BUT_UNRESOLVED_NO_RETRY" and not r["retry_authorized"]
