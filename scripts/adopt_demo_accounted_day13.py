"""One explicit attested maintenance transaction, with durable read evidence and no orders."""

import copy
import fcntl
import hashlib
import json
import os
import re
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import inspect, select, text, update

from integrations.binance_usdm.demo import DemoClient, DemoCredentials, DemoSettings
from packages.persistence.demo import accounts, events, outbox
from packages.persistence.demo_read_audit import AuditedDemoReadJournal, ReadAuditStore
from packages.persistence.demo_read_recovery import (
    PREFIX,
    _clock,
    _file,
    _operation,
    _private_directory,
    _raw,
    _sync_directory,
    _write_new,
    recover_completed_read_clock,
)
from packages.persistence.demo_shared_checkpoint import SharedWeightAnchorStore
from services.execution.demo_models import AttestedDayBaseline, DemoLedger
from services.execution.demo_runtime_read_transport import AccountedDemoReadTransport
from services.execution.demo_runtime_storage import RuntimeClocks

POLICY = "OPERATOR_ATTESTED_ACCOUNTED_DAY13_ADOPTION_V1"
KIND = "DEMO_ATTESTED_DAY13_ADOPTED_ACCOUNTED_V1"


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def paths(directory: Path, journal: UUID) -> tuple[str, Path]:
    operation = str(uuid5(journal, POLICY))
    return operation, directory.parent / ("accounted-day13-adoption-" + operation)


def file_witness(directory: Path, shared_file: Path, own: Path, common: Any) -> str:
    _private_directory(directory)
    values: dict[str, str] = {}
    total = 0
    for p in sorted(directory.rglob("*")):
        common.require(not p.is_symlink(), "DAY13_APPLY_STORAGE_SYMLINK")
        if p == own or own in p.parents:
            continue
        if p.is_dir():
            _private_directory(p)
            continue
        if p == shared_file:
            continue
        common.require(
            not p.name.endswith(("-wal", "-shm", "-journal")), "DAY13_APPLY_STORAGE_BUSY"
        )
        body = _file(p)
        total += len(body)
        common.require(
            len(values) < 128 and total <= 128 * 1024 * 1024, "DAY13_APPLY_STORAGE_LIMIT"
        )
        values[str(p.relative_to(directory))] = hashlib.sha256(body).hexdigest()
    return digest(values)


def financial_witness(
    c: Any,
    old_row: dict[str, Any],
    event_id: str,
    expected_state: dict[str, Any],
    expected_fence: int,
    common: Any,
) -> str:
    """Normalize only the reviewed account delta and the one independently checked event/outbox."""
    names = sorted(
        n
        for n in inspect(c).get_table_names()
        if n.startswith("demo_") and n not in common.RESOURCE_TABLES
    )
    common.require(1 <= len(names) <= 40, "DAY13_APPLY_DATABASE_LIMIT")
    tables = {}
    deadline = time.monotonic() + 60
    for name in names:
        pk = inspect(c).get_pk_constraint(name)["constrained_columns"]
        common.require(bool(pk), "DAY13_APPLY_PRIMARY_KEY_REQUIRED")

        def q(n: str) -> str:
            return '"' + n.replace('"', '""') + '"'

        last = None
        h = hashlib.sha256()
        count = 0
        scanned = 0
        while True:
            common.require(time.monotonic() <= deadline, "DAY13_APPLY_SNAPSHOT_TIMEOUT")
            params: dict[str, Any] = (
                {} if last is None else {"p" + str(i): v for i, v in enumerate(last)}
            )
            where = (
                ""
                if last is None
                else " WHERE ROW("
                + ",".join(map(q, pk))
                + ") > ROW("
                + ",".join(":p" + str(i) for i in range(len(pk)))
                + ")"
            )
            raw = c.execute(
                text(
                    "SELECT row_to_json(t)::text FROM (SELECT * FROM "
                    + q(name)
                    + where
                    + " ORDER BY "
                    + ",".join(map(q, pk))
                    + " LIMIT 1) t"
                ),
                params,
            ).scalar_one_or_none()
            if raw is None:
                break
            common.require(len(raw.encode()) <= 16 * 1024 * 1024, "DAY13_APPLY_ROW_SIZE_LIMIT")
            row = json.loads(raw)
            last = tuple(row[k] for k in pk)
            scanned += 1
            common.require(scanned <= 1_000_000, "DAY13_APPLY_ROW_COUNT_LIMIT")
            if name in ("demo_events", "demo_outbox") and row["event_id"] == event_id:
                continue
            if name == "demo_accounts" and row["account_id"] == old_row["account_id"]:
                expected = copy.deepcopy(old_row)
                expected["state"] = expected_state
                expected["fence"] = expected_fence
                common.require(row == expected, "DAY13_APPLY_UNREVIEWED_ACCOUNT_CHANGE")
                row = old_row
            encoded = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
            h.update(len(encoded).to_bytes(8, "big"))
            h.update(encoded)
            count += 1
        tables[name] = dict(rows=count, sha256=h.hexdigest())
    return digest(tables)


def preview_evidence(
    engine: Any,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    shared: Any,
    view: Any,
    fingerprint: Any,
    common: Any,
) -> dict[str, Any]:
    _, folder = common.day13_paths(directory, journal)
    _private_directory(folder)
    common.require(
        {p.name for p in folder.iterdir()} == {"intent.json", "complete.json"},
        "DAY13_APPLY_PREVIEW_INCOMPLETE",
    )
    body = _file(folder / "complete.json", 65536)
    result = json.loads(body)
    intent = json.loads(_file(folder / "intent.json", 16384))
    common.require(
        intent.get("policy") == common.DAY13_POLICY
        and intent.get("journal") == str(journal)
        and intent.get("installation") == str(installation)
        and intent.get("scope") == scope,
        "DAY13_APPLY_PREVIEW_BINDING_INVALID",
    )
    common.require(
        result.get("accounted_read_probe") == "DAY13_PREVIEW_NO_FINANCIAL_CHANGE"
        and result.get("operation_id") == str(uuid5(journal, common.DAY13_POLICY))
        and result.get("http_attempts") == result.get("completed_reads") == 8
        and result.get("modeled_weight_charged") == 131
        and result.get("audit_events_added") == result.get("resource_checkpoints_added") == 16,
        "DAY13_APPLY_PREVIEW_INVALID",
    )
    common.require(
        all(
            result.get(k) is True
            for k in (
                "financial_ledger_unchanged_verified",
                "operator_attestation_used",
                "wallet_rechecked",
                "income_since_prior_day_empty",
                "positions_empty_at_read",
                "regular_orders_empty_at_read",
                "algo_orders_empty_at_read",
            )
        )
        and all(
            result.get(k) is False
            for k in (
                "day_adopted",
                "baseline_adoption_approved",
                "financial_database_changed_by_preview",
                "orders_enabled",
                "live_trading_enabled",
                "execution_approved",
                "exact_day_midnight_valuation_verified",
                "account_wide_trade_export_verified",
            )
        ),
        "DAY13_APPLY_PREVIEW_INVALID",
    )
    common.require(
        result.get("operator_attested_day_kst") == result.get("proposed_day_kst") == "2026-11-13"
        and result.get("old_day_kst") == "2026-11-12"
        and result.get("proposed_daily_base_usdt")
        == result.get("weekly_base_usdt_preserved")
        == "4998.74183608"
        and result.get("week_kst_preserved") == "2026-11-09"
        and result.get("high_water_usdt_preserved") == "5000.00000000",
        "DAY13_APPLY_REVIEWED_VALUES_CHANGED",
    )
    common.require(
        common.protected_snapshot(engine, directory.parent, shared.path, folder, fingerprint)
        == result.get("protected_snapshot_sha256")
        == intent.get("protected_snapshot_sha256"),
        "DAY13_APPLY_PREVIEW_STATE_CHANGED",
    )
    _, actions = common.resource_rows(engine, scope)
    reads = [a for a in actions.values() if a["kind"] != "CLOCK_REBASE"]
    routes = common.BASIC_ROUTES + common.DAY13_ROUTES
    common.require(
        len(reads) == 20 and view["shared_total_weight"] == 137, "DAY13_APPLY_PRIOR_AUDIT_INVALID"
    )
    for i, (path, cost) in enumerate(routes):
        for phase, kind in enumerate(("RESERVE", "FINISH")):
            a = reads[i * 2 + phase]
            common.require(
                a["kind"] == kind
                and a["path"] == path
                and a["cost"] == cost
                and (not phase or (a["sent"] is True and a["status"] == 200)),
                "DAY13_APPLY_PRIOR_AUDIT_INVALID",
            )
    return dict(
        receipt_sha256=hashlib.sha256(body).hexdigest(), operation_id=result["operation_id"]
    )


CLOCK_POLICY = "DAY13_ADOPTION_AUDITED_CLOCK_PREPARATION_V1"


def clock_folder(directory: Path, journal: UUID) -> Path:
    return directory.parent / (
        "accounted-day13-adoption-clock-" + str(uuid5(journal, CLOCK_POLICY))
    )


def prepared_evidence(
    engine: Any,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    account: str,
    shared: Any,
    view: Any,
    fingerprint: Any,
    common: Any,
) -> dict[str, Any]:
    folder = clock_folder(directory, journal)
    _private_directory(folder)
    common.require(
        {p.name for p in folder.iterdir()} == {"intent.json", "complete.json"},
        "DAY13_CLOCK_PREPARATION_INCOMPLETE",
    )
    intent = json.loads(_file(folder / "intent.json", 1024 * 1024))
    record = json.loads(_file(folder / "complete.json", 16384))
    for value in (intent, record):
        common.require(
            isinstance(value, dict)
            and value.get("policy") == CLOCK_POLICY
            and value.get("scope") == scope
            and value.get("journal") == str(journal)
            and value.get("installation") == str(installation)
            and value.get("account") == account,
            "DAY13_CLOCK_PREPARATION_BINDING_INVALID",
        )
    common.require(
        record.get("intent_sha256") == digest(intent)
        and record.get("preview") == intent.get("preview"),
        "DAY13_CLOCK_PREPARATION_INTENT_CHANGED",
    )
    _, preview_folder = common.day13_paths(directory, journal)
    body = _file(preview_folder / "complete.json", 65536)
    common.require(
        hashlib.sha256(body).hexdigest() == record["preview"]["receipt_sha256"],
        "DAY13_CLOCK_PREVIEW_RECEIPT_CHANGED",
    )
    common.require(
        common.protected_snapshot(engine, directory.parent, shared.path, folder, fingerprint)
        == record["protected_snapshot_sha256"],
        "DAY13_CLOCK_PROTECTED_STATE_CHANGED",
    )
    common.require(
        common.resource_witness(engine, scope) == record["resource_witness_sha256"]
        and view["shared_total_weight"] == 137
        and view["local_read_activity_complete"]
        and view["audit_events"] == record["audit_events_after"],
        "DAY13_CLOCK_RESOURCE_STATE_CHANGED",
    )
    return dict(record)


def clock_report(view: Any, record: Any, changed: bool) -> dict[str, Any]:
    return dict(
        accounted_day13_clock_preparation="READY_TO_APPLY_KEEP_PAUSED"
        if view["startup_ready"]
        else "WAIT_STARTUP_HOLD",
        startup_ready=view["startup_ready"],
        startup_hold_seconds=70.001,
        preparation_snapshot_sha256=record["protected_snapshot_sha256"],
        shared_journal_changed=changed,
        audit_clock_event_added=changed,
        resource_checkpoint_added=changed,
        initial_clock_reason=record["initial_clock_reason"],
        existing_modeled_weight_preserved=137,
        prior_preview_receipt_verified=True,
        prior_resource_rows_preserved=True,
        financial_ledger_unchanged_verified=True,
        http_attempts=0,
        modeled_weight_charged=0,
        order_writes=0,
        orders_enabled=False,
        live_trading_enabled=False,
        execution_approved=False,
        next_action="APPLY_ONCE_AFTER_READ_ONLY_STARTUP_WAIT",
    )


def _clock_status(
    engine: Any,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    account: str,
    fingerprint: Any,
    common: Any,
    clock: Any,
    expected: str | None = None,
) -> dict[str, Any]:
    _, application = paths(directory, journal)
    common.require(
        not application.exists() and not application.is_symlink(),
        "DAY13_APPLY_ALREADY_ATTEMPTED_RUN_STATUS",
    )
    anchor, audit = SharedWeightAnchorStore(engine), ReadAuditStore(engine)
    common.require(anchor.installation_id() == installation, "PROBE_INSTALLATION_MISMATCH")
    with engine.connect() as c, c.begin():
        c.execute(text("SET LOCAL lock_timeout='2s'"))
        c.execute(text("SET LOCAL statement_timeout='2s'"))
        row = (
            c.execute(
                select(accounts).where(accounts.c.account_id == account).with_for_update(read=True)
            )
            .mappings()
            .one()
        )
        common.validate_day13_row(row, scope, journal, installation, clock)
        shared = AuditedDemoReadJournal(
            directory,
            scope,
            journal,
            anchors=anchor,
            audit=audit,
            read_only=True,
            monotonic=clock.monotonic,
            wall_clock=clock.wall_clock,
            boot=clock.boot,
        )
        view = audit.review(shared)
        record = prepared_evidence(
            engine,
            directory,
            scope,
            journal,
            installation,
            account,
            shared,
            view,
            fingerprint,
            common,
        )
        if expected is not None:
            common.require(
                re.fullmatch("[0-9a-f]{64}", expected) is not None
                and expected == record["protected_snapshot_sha256"],
                "DAY13_CLOCK_EXPECTED_SNAPSHOT_CHANGED",
            )
        return clock_report(view, record, False)


def clock_status(
    engine: Any,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    account: str,
    fingerprint: Any,
    common: Any,
    *,
    clocks: Any = None,
    expected: str | None = None,
) -> dict[str, Any]:
    AccountedDemoReadTransport._context()
    _private_directory(directory.parent)
    fd = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            common.require(False, "PROBE_ALREADY_RUNNING")
        return _clock_status(
            engine,
            directory,
            scope,
            journal,
            installation,
            account,
            fingerprint,
            common,
            clocks or RuntimeClocks(),
            expected,
        )
    finally:
        os.close(fd)


def prepare_clock(
    engine: Any,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    account: str,
    credential_loader: Any,
    fingerprint: Any,
    common: Any,
    *,
    apply: bool = False,
    clocks: Any = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    common.require(apply is True, "DAY13_CLOCK_EXPLICIT_PREPARATION_REQUIRED")
    AccountedDemoReadTransport._context()
    clock = clocks or RuntimeClocks()
    state = {} if state is None else state
    _, application = paths(directory, journal)
    folder = clock_folder(directory, journal)
    _private_directory(directory.parent)
    fd = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            common.require(False, "PROBE_ALREADY_RUNNING")
        common.require(
            not application.exists() and not application.is_symlink(),
            "DAY13_APPLY_ALREADY_ATTEMPTED_RUN_STATUS",
        )
        if folder.exists() or folder.is_symlink():
            # An interrupted wait resumes with reads only. Never reapply a rebase.
            return _clock_status(
                engine, directory, scope, journal, installation, account, fingerprint, common, clock
            )
        state["stage"] = "DAY13_CLOCK_PRIVATE_CREDENTIAL_PREFLIGHT"
        try:
            key = credential_loader()
        except Exception:
            common.require(False, "PROBE_CREDENTIAL_SESSION_UNAVAILABLE")
            raise
        common.require(isinstance(key, DemoCredentials), "PROBE_CREDENTIAL_SESSION_UNAVAILABLE")
        anchor, audit = SharedWeightAnchorStore(engine), ReadAuditStore(engine)
        common.require(anchor.installation_id() == installation, "PROBE_INSTALLATION_MISMATCH")
        with engine.connect() as c, c.begin():
            c.execute(text("SET LOCAL lock_timeout='2s'"))
            c.execute(text("SET LOCAL statement_timeout='2s'"))
            row = (
                c.execute(
                    select(accounts)
                    .where(accounts.c.account_id == account)
                    .with_for_update(read=True)
                )
                .mappings()
                .one()
            )
            common.validate_day13_row(row, scope, journal, installation, clock)
            state["stage"] = "DAY13_CLOCK_PRIOR_PREVIEW_AND_RAW_AUDIT"
            with _raw(directory, scope, journal, anchor) as (cp, local, path):
                history, before = audit._verified_history(cp, local)
                meta = before[1][0]
                common.require(
                    meta[7:] == [137, 0] and all(r[4] is not None for r in before[2]),
                    "DAY13_CLOCK_COMPLETED_PREVIEW_REQUIRED",
                )
                revision, old_digest = cp.revision, cp.digest
                old_file_sha = hashlib.sha256(_file(path)).hexdigest()
                sample = _clock(clock)
                reason = (
                    "BOOT_CHANGED"
                    if meta[3] != sample[2]
                    else "MONOTONIC_BACKWARDS"
                    if sample[0] < meta[6]
                    else "WALL_MONOTONIC_DRIFT"
                    if abs((sample[1] - meta[5]) - (sample[0] - meta[4])) > 1
                    else "NONE"
                )
            from types import SimpleNamespace

            proof = preview_evidence(
                engine,
                directory,
                scope,
                journal,
                installation,
                SimpleNamespace(path=path),
                dict(shared_total_weight=137),
                fingerprint,
                common,
            )
            old_rows, _ = common.resource_rows(engine, scope)
            protected = common.protected_snapshot(
                engine, directory.parent, path, folder, fingerprint
            )
            backup = directory / (PREFIX + _operation(str(journal), revision, old_digest))
            common.require(
                not backup.exists() and not backup.is_symlink(), "DAY13_CLOCK_BACKUP_ALREADY_EXISTS"
            )
            intent = dict(
                policy=CLOCK_POLICY,
                scope=scope,
                journal=str(journal),
                installation=str(installation),
                account=account,
                preview=proof,
                initial_clock_reason=reason,
                protected_before_sha256=protected,
                prior_resource_rows=[[list(k), v] for k, v in sorted(old_rows.items())],
            )
            encoded = json.dumps(intent, sort_keys=True).encode()
            common.require(len(encoded) <= 1024 * 1024, "DAY13_CLOCK_INTENT_SIZE_LIMIT")
            os.mkdir(folder, mode=0o700)
            state["clock_preparation_created"] = True
            _write_new(folder / "intent.json", encoded)
            _sync_directory(folder)
            _sync_directory(directory.parent)
            state["stage"] = "DAY13_AUDITED_CLOCK_PREPARATION"
            state["clock_apply_started"] = True
            view = recover_completed_read_clock(
                directory, scope, journal, anchor=anchor, audit=audit, apply=True, clocks=clock
            )
            changed = view["shared_journal_changed"]
            after_rows, actions = common.resource_rows(engine, scope)
            common.require(
                all(after_rows.get(k) == v for k, v in old_rows.items()),
                "DAY13_CLOCK_PRIOR_RESOURCE_ROWS_CHANGED",
            )
            expected = (
                {
                    ("demo_shared_weight_checkpoints", scope, revision + 1),
                    ("demo_read_audit_events", scope, len(history)),
                }
                if changed
                else set()
            )
            common.require(
                set(after_rows) - set(old_rows) == expected
                and view["shared_total_weight"] == 137
                and view["local_read_activity_complete"],
                "DAY13_CLOCK_RESOURCE_DELTA_INVALID",
            )
            if changed:
                action = actions[len(history)]
                common.require(
                    action["kind"] == "CLOCK_REBASE"
                    and action["sent"] is False
                    and action["cost"] is None
                    and hashlib.sha256(_file(backup / "original.sqlite3")).hexdigest()
                    == old_file_sha,
                    "DAY13_CLOCK_BACKUP_OR_AUDIT_INVALID",
                )
            common.require(
                common.protected_snapshot(
                    engine,
                    directory.parent,
                    path,
                    folder,
                    fingerprint,
                    excluded_folder=backup if changed else None,
                )
                == protected,
                "DAY13_CLOCK_UNREVIEWED_STATE_CHANGE",
            )
            record = dict(
                policy=CLOCK_POLICY,
                scope=scope,
                journal=str(journal),
                installation=str(installation),
                account=account,
                preview=proof,
                intent_sha256=digest(intent),
                initial_clock_reason=reason,
                audit_events_after=view["audit_events"],
                protected_snapshot_sha256=common.protected_snapshot(
                    engine, directory.parent, path, folder, fingerprint
                ),
                resource_witness_sha256=common.resource_witness(engine, scope),
            )
            _write_new(folder / "complete.json", json.dumps(record, sort_keys=True).encode())
            _sync_directory(folder)
            return clock_report(view, record, changed)
    finally:
        os.close(fd)


def fresh_reads(client: Any, clock: Any, common: Any, state: dict[str, Any]) -> dict[str, Any]:
    observations = {}
    client.sync_clock()
    cutoff = client.clock_ms() + client.offset
    config = client.request("GET", "/fapi/v1/accountConfig")
    common.require(
        isinstance(config, dict)
        and config.get("dualSidePosition") is False
        and config.get("multiAssetsMargin") is False,
        "DAY13_APPLY_ACCOUNT_MODE_CHANGED",
    )
    wallets = []
    for i, (path, _) in enumerate(common.DAY13_ROUTES[2:]):
        state["stage"] = "DAY13_APPLY_ACCOUNTED_READ_" + str(i + 3)
        params = (
            dict(startTime="1794409200000", endTime=str(cutoff), limit="1000", page="1")
            if path.endswith("/income")
            else None
        )
        body = client.request("GET", path, params)
        if path.endswith("/account"):
            wallets.append(common.day13_wallet(body))
        elif path.endswith("/income"):
            common.require(isinstance(body, list) and not body, "DAY13_APPLY_INCOME_NOT_EMPTY")
        else:
            common.verify_flat_payload(path, body)
        observations[path + ":" + str(i)] = clock.wall_clock().isoformat()
    common.require(wallets == ["4998.74183608", "4998.74183608"], "DAY13_APPLY_WALLET_CHANGED")
    return dict(
        cutoff_ms=cutoff,
        observations_utc=observations,
        modeled_weight=131,
        completed_reads=8,
        snapshot_atomic=False,
    )


def check_resource_delta(engine: Any, scope: str, intent: dict[str, Any], common: Any) -> None:
    after, actions = common.resource_rows(engine, scope)
    before = {(r[0], r[1], r[2]): h for r, h in intent["prior_resource_rows"]}
    common.require(
        all(after.get(k) == v for k, v in before.items()), "DAY13_APPLY_PRIOR_RESOURCE_ROWS_CHANGED"
    )
    revision = max(
        k[2] for k in before if k[0] == "demo_shared_weight_checkpoints" and k[1] == scope
    )
    first = intent["first_audit_sequence"]
    expected = {("demo_shared_weight_checkpoints", scope, revision + i) for i in range(1, 17)} | {
        ("demo_read_audit_events", scope, first + i) for i in range(16)
    }
    common.require(set(after) - set(before) == expected, "DAY13_APPLY_RESOURCE_DELTA_INVALID")
    for i, (path, cost) in enumerate(common.DAY13_ROUTES):
        for phase, kind in enumerate(("RESERVE", "FINISH")):
            a = actions[first + i * 2 + phase]
            common.require(
                a["kind"] == kind
                and a["path"] == path
                and a["cost"] == cost
                and (not phase or (a["sent"] is True and a["status"] == 200)),
                "DAY13_APPLY_AUDIT_ROUTE_MISMATCH",
            )


def verified_status(
    engine: Any,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    account: str,
    clock: Any,
    common: Any,
) -> dict[str, Any]:
    operation, folder = paths(directory, journal)
    _private_directory(folder)
    intent = json.loads(_file(folder / "intent.json", 16 * 1024 * 1024))
    common.require(
        {p.name for p in folder.iterdir()}
        in (
            {"intent.json", "commit-intent.json"},
            {"intent.json", "commit-intent.json", "complete.json"},
        ),
        "DAY13_APPLY_COMMIT_WITNESS_REQUIRED",
    )
    commit = json.loads(_file(folder / "commit-intent.json", 16384))
    common.require(
        commit.get("policy") == POLICY and commit.get("operation_id") == operation,
        "DAY13_APPLY_COMMIT_WITNESS_INVALID",
    )
    common.require(
        intent["policy"] == POLICY
        and intent["scope"] == scope
        and intent["journal"] == str(journal)
        and intent["installation"] == str(installation)
        and intent["old_row"]["account_id"] == account,
        "DAY13_APPLY_INTENT_INVALID",
    )
    with engine.connect() as c, c.begin():
        c.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        c.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        row = c.execute(select(accounts).where(accounts.c.account_id == account)).mappings().one()
        ledger = DemoLedger.model_validate(row["state"])
        common.require(
            digest(row["state"]) == commit.get("new_full_ledger_sha256")
            and row["fence"] == commit.get("new_fence"),
            "DAY13_APPLY_COMMIT_STATE_CHANGED",
        )
        from services.execution.demo_read_audit_bootstrap import validate_account_row

        validate_account_row(row, scope, journal, installation, clock)
        record = json.loads(ledger.operations.get(operation, "{}"))
        common.require(
            record.get("policy") == POLICY
            and record.get("operation_id") == operation
            and record.get("preview_receipt_sha256") == intent["preview"]["receipt_sha256"]
            and record.get("prior_full_ledger_sha256") == digest(intent["old_row"]["state"]),
            "DAY13_APPLY_ADOPTION_NOT_VERIFIED",
        )
        receipt = ledger.attested_day_baselines.get(operation)
        common.require(
            receipt is not None
            and digest(receipt.model_dump(mode="json")) == record["typed_receipt_sha256"],
            "DAY13_APPLY_RECEIPT_CHANGED",
        )
        assert receipt is not None
        expected = copy.deepcopy(intent["old_row"]["state"])
        expected["daily_key"] = "2026-11-13"
        expected["attested_day_baselines"][operation] = receipt.model_dump(mode="json")
        expected["operations"][operation] = ledger.operations[operation]
        common.require(
            row["state"] == expected and row["fence"] == intent["old_row"]["fence"] + 1,
            "DAY13_APPLY_UNREVIEWED_ACCOUNT_CHANGE",
        )
        event = c.execute(select(events).where(events.c.event_id == operation)).mappings().one()
        common.require(
            event["account_id"] == account
            and event["fence"] == row["fence"]
            and event["kind"] == KIND
            and json.loads(event["payload"]) == expected
            and event["as_of"].isoformat() == commit["event_as_of_utc"],
            "DAY13_APPLY_EVENT_CHANGED",
        )
        box = c.execute(select(outbox).where(outbox.c.event_id == operation)).mappings().one()
        common.require(box["topic"] == "DEMO_AUDIT", "DAY13_APPLY_OUTBOX_CHANGED")
        common.require(
            financial_witness(c, intent["old_row"], operation, expected, row["fence"], common)
            == intent["financial_witness_sha256"],
            "DAY13_APPLY_PRIOR_FINANCIAL_HISTORY_CHANGED",
        )
    shared = AuditedDemoReadJournal(
        directory,
        scope,
        journal,
        anchors=SharedWeightAnchorStore(engine),
        audit=ReadAuditStore(engine),
        read_only=True,
        monotonic=clock.monotonic,
        wall_clock=clock.wall_clock,
        boot=clock.boot,
    )
    audit = ReadAuditStore(engine).review(shared)
    common.require(
        audit["local_read_activity_complete"]
        and audit["startup_ready"]
        and audit["shared_total_weight"] == 268
        and audit["audit_events"] == intent["first_audit_sequence"] + 16,
        "DAY13_APPLY_FINAL_AUDIT_INVALID",
    )
    check_resource_delta(engine, scope, intent, common)
    common.require(
        file_witness(directory.parent, shared.path, folder, common)
        == intent["file_witness_sha256"],
        "DAY13_APPLY_PRIOR_FILES_CHANGED",
    )
    return dict(
        accounted_day13_adoption="APPLIED_VERIFIED_KEEP_PAUSED",
        operation_id=operation,
        day_kst="2026-11-13",
        week_kst_preserved="2026-11-09",
        daily_base_usdt="4998.74183608",
        weekly_base_usdt_preserved="4998.74183608",
        high_water_usdt_preserved="5000.00000000",
        day_adopted=True,
        operator_attestation_used=True,
        exact_day_midnight_valuation_verified=False,
        account_wide_trade_export_verified=False,
        prior_financial_history_preserved=True,
        prior_receipts_and_files_preserved=True,
        prior_resource_rows_preserved=True,
        only_reviewed_account_fields_changed=True,
        maintenance_fence_incremented=True,
        leader_acquired=False,
        application_http_attempts=8,
        application_modeled_weight_charged=131,
        application_audit_events_added=16,
        application_resource_checkpoints_added=16,
        financial_event_added=1,
        financial_outbox_added=1,
        period_keys_match=True,
        paused=True,
        risk_state="NORMAL",
        http_attempts=0,
        order_writes=0,
        orders_enabled=False,
        live_trading_enabled=False,
        execution_approved=False,
        account_order_usage_verified=False,
        actual_ip_capacity_reserved=False,
        request_cost_upper_bounds_proven=False,
        trading_readiness_checked=False,
        exchange_snapshot_atomic=False,
        financial_change_atomic=True,
        baseline_source="OPERATOR_ATTESTED_FLAT_WITH_SIGNED_USDT_EVIDENCE",
        exchange_account_identity_verified=False,
        next_action="KEEP_PAUSED_READ_ONLY_STATUS_NO_APPLY_RETRY",
    )


def run(
    engine: Any,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    account: str,
    settings: Any,
    credential_loader: Any,
    fingerprint: Any,
    common: Any,
    *,
    apply: bool = False,
    status: bool = False,
    clocks: Any = None,
    sender: Any = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    common.require(
        type(apply) is bool and type(status) is bool and ((apply is True) != (status is True)),
        "DAY13_APPLY_EXPLICIT_OPERATION_REQUIRED",
    )
    common.require(type(apply) is bool and type(status) is bool, "DAY13_APPLY_FLAGS_INVALID")
    AccountedDemoReadTransport._context()
    clock = clocks or RuntimeClocks()
    state = {} if state is None else state
    operation, folder = paths(directory, journal)
    _private_directory(directory.parent)
    fd = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    transport = None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            common.require(False, "PROBE_ALREADY_RUNNING")
        if status:
            state["application_may_have_completed"] = folder.exists()
            return verified_status(
                engine, directory, scope, journal, installation, account, clock, common
            )
        common.require(
            not folder.exists() and not folder.is_symlink(),
            "DAY13_APPLY_ALREADY_ATTEMPTED_RUN_STATUS",
        )
        common.require(
            isinstance(settings, DemoSettings)
            and not settings.demo_trading_enabled
            and not settings.live_trading_enabled
            and settings.margin_mode == "ISOLATED"
            and settings.symbols == ("BTCUSDT",),
            "DAY13_APPLY_SETTINGS_INVALID",
        )
        state["stage"] = "DAY13_APPLY_PRIVATE_CREDENTIAL_PREFLIGHT"
        try:
            key = credential_loader()
        except Exception:
            common.require(False, "PROBE_CREDENTIAL_SESSION_UNAVAILABLE")
            raise
        common.require(isinstance(key, DemoCredentials), "PROBE_CREDENTIAL_SESSION_UNAVAILABLE")
        anchor, audit = SharedWeightAnchorStore(engine), ReadAuditStore(engine)
        common.require(anchor.installation_id() == installation, "PROBE_INSTALLATION_MISMATCH")
        state["stage"] = "DAY13_APPLY_AUDITED_JOURNAL_REVIEW"
        shared = AuditedDemoReadJournal(
            directory,
            scope,
            journal,
            anchors=anchor,
            audit=audit,
            monotonic=clock.monotonic,
            wall_clock=clock.wall_clock,
            boot=clock.boot,
        )
        view = audit.review(shared)
        common.require(
            view["startup_ready"]
            and view["local_read_activity_complete"]
            and view["audit_events"] + 16 <= 1024,
            "DAY13_APPLY_AUDIT_NOT_READY",
        )
        with engine.connect() as c, c.begin():
            c.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            c.execute(text("SET LOCAL statement_timeout='2s'"))
            c.execute(text("SET LOCAL lock_timeout='2s'"))
            row = (
                c.execute(
                    select(accounts).where(accounts.c.account_id == account).with_for_update()
                )
                .mappings()
                .one()
            )
            common.validate_day13_row(row, scope, journal, installation, clock)
            old = json.loads(
                c.execute(
                    text(
                        "SELECT row_to_json(t)::text FROM (SELECT * FROM demo_accounts WHERE account_id=:account) t"
                    ),
                    dict(account=account),
                ).scalar_one()
            )
            common.require(
                operation not in old["state"]["operations"]
                and operation not in old["state"]["attested_day_baselines"],
                "DAY13_APPLY_OPERATION_CONFLICT",
            )
            common.require(
                c.execute(select(events.c.event_id).where(events.c.event_id == operation)).first()
                is None,
                "DAY13_APPLY_EVENT_ALREADY_PRESENT",
            )
            if (
                clock_folder(directory, journal).exists()
                or clock_folder(directory, journal).is_symlink()
            ):
                preparation = prepared_evidence(
                    engine,
                    directory,
                    scope,
                    journal,
                    installation,
                    account,
                    shared,
                    view,
                    fingerprint,
                    common,
                )
                proof = preparation["preview"]
            else:
                proof = preview_evidence(
                    engine,
                    directory,
                    scope,
                    journal,
                    installation,
                    shared,
                    view,
                    fingerprint,
                    common,
                )
            baseline = financial_witness(c, old, operation, old["state"], old["fence"], common)
            resources, _ = common.resource_rows(engine, scope)
            intent = dict(
                policy=POLICY,
                scope=scope,
                journal=str(journal),
                installation=str(installation),
                old_row=old,
                preview=proof,
                financial_witness_sha256=baseline,
                file_witness_sha256=file_witness(directory.parent, shared.path, folder, common),
                prior_resource_rows=[[list(k), v] for k, v in sorted(resources.items())],
                first_audit_sequence=view["audit_events"],
            )
            common.require(
                len(json.dumps(intent, sort_keys=True).encode()) <= 16 * 1024 * 1024,
                "DAY13_APPLY_INTENT_SIZE_LIMIT",
            )
            os.mkdir(folder, mode=0o700)
            state["intent_created"] = True
            _write_new(folder / "intent.json", json.dumps(intent, sort_keys=True).encode())
            _sync_directory(folder)
            _sync_directory(directory.parent)
            kwargs = {} if sender is None else dict(sender=sender)
            transport = AccountedDemoReadTransport(
                settings,
                shared,
                enable_reads=True,
                monotonic=clock.monotonic,
                max_requests=8,
                max_seconds=30,
                **kwargs,
            )
            client = DemoClient(
                settings,
                key,
                send=transport,
                clock_ms=lambda: int(clock.wall_clock().timestamp() * 1000),
            )
            started = clock.wall_clock()
            fresh = fresh_reads(client, clock, common, state)
            common.validate_day13_row(row, scope, journal, installation, clock)
            common.require(
                0 <= (clock.wall_clock() - started).total_seconds() <= 30,
                "DAY13_APPLY_EVIDENCE_STALE",
            )
            check_resource_delta(engine, scope, intent, common)
            metrics = transport.diagnostics()
            common.require(
                metrics["http_attempts"] == metrics["completed_reads"] == 8
                and metrics["modeled_weight_charged"] == 131,
                "DAY13_APPLY_TRANSPORT_DELTA_INVALID",
            )
            common.require(
                financial_witness(c, old, operation, old["state"], old["fence"], common) == baseline
                and file_witness(directory.parent, shared.path, folder, common)
                == intent["file_witness_sha256"],
                "DAY13_APPLY_STATE_CHANGED_BEFORE_COMMIT",
            )
            receipt = AttestedDayBaseline(
                review_token=digest(proof),
                source="OPERATOR_ATTESTED_FLAT_WITH_SIGNED_USDT_EVIDENCE",
                boundary_utc=datetime(2026, 11, 12, 15, tzinfo=UTC),
                baseline_usdt=Decimal("4998.74183608"),
                old_daily_key="2026-11-12",
                old_daily_base=Decimal("4998.74183608"),
                anchor_utc=DemoLedger.model_validate(old["state"]).value.as_of,
                known_exit_utc=datetime.fromtimestamp(1793947973987 / 1000, UTC),
                attested_until_utc=clock.wall_clock(),
                signed_evidence_hash=digest(fresh),
                exchange_fingerprint=digest(
                    dict(
                        wallet="4998.74183608",
                        cutoff=fresh["cutoff_ms"],
                        scope="SEQUENTIAL_GLOBAL_FLAT_READS",
                    )
                ),
            )
            record = dict(
                policy=POLICY,
                operation_id=operation,
                preview_receipt_sha256=proof["receipt_sha256"],
                prior_full_ledger_sha256=digest(old["state"]),
                typed_receipt_sha256=digest(receipt.model_dump(mode="json")),
                signed_evidence_hash=digest(fresh),
                exact_midnight_valuation_verified=False,
                account_wide_exchange_export_verified=False,
            )
            new = copy.deepcopy(old["state"])
            new["daily_key"] = "2026-11-13"
            new["attested_day_baselines"][operation] = receipt.model_dump(mode="json")
            new["operations"][operation] = json.dumps(record, sort_keys=True)
            DemoLedger.model_validate(new)
            common.validate_day13_row(row, scope, journal, installation, clock)
            common.require(
                0 <= (clock.wall_clock() - started).total_seconds() <= 30,
                "DAY13_APPLY_EVIDENCE_STALE",
            )
            adopted_at = clock.wall_clock()
            _write_new(
                folder / "commit-intent.json",
                json.dumps(
                    dict(
                        policy=POLICY,
                        operation_id=operation,
                        new_full_ledger_sha256=digest(new),
                        new_fence=old["fence"] + 1,
                        event_as_of_utc=adopted_at.isoformat(),
                    ),
                    sort_keys=True,
                ).encode(),
            )
            _sync_directory(folder)
            state["application_may_have_completed"] = True
            state["stage"] = "DAY13_ATOMIC_FINANCIAL_ADOPTION"
            c.execute(
                update(accounts)
                .where(accounts.c.account_id == account)
                .values(state=new, fence=old["fence"] + 1)
            )
            c.execute(
                events.insert().values(
                    event_id=operation,
                    account_id=account,
                    fence=old["fence"] + 1,
                    as_of=adopted_at,
                    kind=KIND,
                    payload=json.dumps(new, sort_keys=True),
                )
            )
            c.execute(outbox.insert().values(event_id=operation, topic="DEMO_AUDIT"))
            common.require(
                financial_witness(c, old, operation, new, old["fence"] + 1, common) == baseline,
                "DAY13_APPLY_UNREVIEWED_FINANCIAL_CHANGE",
            )
            common.validate_day13_row(row, scope, journal, installation, clock)
            common.require(
                0 <= (clock.wall_clock() - started).total_seconds() <= 30,
                "DAY13_APPLY_EVIDENCE_STALE",
            )
            state["application_may_have_completed"] = True
        transport.close()
        state["stage"] = "DAY13_INDEPENDENT_POSTCHECK"
        result = verified_status(
            engine, directory, scope, journal, installation, account, clock, common
        )
        result.update(
            http_attempts=8,
            modeled_weight_charged=131,
            database_changed=True,
            synthetic_sender_configured=sender is not None,
        )
        _write_new(folder / "complete.json", json.dumps(result, sort_keys=True).encode())
        _sync_directory(folder)
        return result
    finally:
        if transport is not None:
            transport.close()
            state["transport"] = transport.diagnostics()
        os.close(fd)
