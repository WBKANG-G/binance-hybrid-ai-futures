"""One explicit two-GET sample using the already installed audited transport.

The bundle loader places only /app on the project import path. No credential,
signed query, response body, financial value or exception body is published.
"""

import fcntl
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

POLICY = "INSTALLED_ACCOUNTED_TWO_GET_PROBE_V1"
RESOURCE_TABLES = {
    "demo_read_audit_events": "sequence",
    "demo_shared_weight_checkpoints": "revision",
}
ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"
SCOPE = "DEMO-WINDOWS-TP-MANAGED-WRITERS-V1"
STORAGE = Path("/app/demo-export-state/demo-runtime-20261111/shared-weight")
SETUP_KEY = "MANAGED_RUNTIME:STORAGE_SETUP_20261111_V1"
BASELINE = "e03b6ba683732cd5872c30cf0d245a5b1fb6d325"


class ProbeBlocked(ValueError):
    pass


def require(ok: bool, code: str) -> None:
    if not ok:
        raise ProbeBlocked(code)


def verify_files(root: Path, values: dict[str, str]) -> None:
    require(1 <= len(values) <= 512, "PROBE_SOURCE_MANIFEST_INVALID")
    for name, expected in values.items():
        relative = Path(name)
        require(
            not relative.is_absolute() and ".." not in relative.parts,
            "PROBE_SOURCE_MANIFEST_INVALID",
        )
        path = root / relative
        require(
            path.resolve().is_relative_to(root.resolve())
            and path.is_file()
            and not path.is_symlink(),
            "PROBE_INSTALLED_SOURCE_MISSING",
        )
        require(
            hashlib.sha256(path.read_bytes()).hexdigest() == expected,
            "PROBE_INSTALLED_SOURCE_CHANGED",
        )


def protected_snapshot(
    engine: Any,
    directory: Path,
    shared_file: Path,
    marker: Path,
    fingerprint: Any,
    *,
    excluded_folder: Path | None = None,
) -> str:
    from sqlalchemy import inspect, text

    from packages.persistence.demo_order_quota import canonical
    from packages.persistence.demo_read_recovery import _file, _private_directory

    deadline = time.monotonic() + 60
    tables: dict[str, Any] = {}
    with engine.connect() as c, c.begin():
        c.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        inspector = inspect(c)
        names = sorted(n for n in inspector.get_table_names() if n.startswith("demo_"))
        require(1 <= len(names) <= 40, "PROBE_DATABASE_LIMIT")
        for name in names:
            if name not in RESOURCE_TABLES:
                tables[name] = fingerprint._table_snapshot(c, inspector, name, deadline)
    _private_directory(directory)
    files: dict[str, str] = {}
    total = 0
    for path in sorted(directory.rglob("*")):
        require(not path.is_symlink(), "PROBE_STORAGE_SYMLINK")
        if excluded_folder is not None and (
            path == excluded_folder or excluded_folder in path.parents
        ):
            continue
        if path == marker or marker in path.parents:
            continue
        if path.is_dir():
            _private_directory(path)
            continue
        if path == shared_file:
            continue
        require(not path.name.endswith(("-wal", "-shm", "-journal")), "PROBE_STORAGE_BUSY")
        body = _file(path)
        total += len(body)
        require(len(files) < 128 and total <= 128 * 1024 * 1024, "PROBE_STORAGE_LIMIT")
        files[str(path.relative_to(directory))] = hashlib.sha256(body).hexdigest()
    fingerprint._budget(deadline)
    return hashlib.sha256(
        canonical(dict(database=tables, protected_files=files)).encode()
    ).hexdigest()


def resource_rows(
    engine: Any, scope: str
) -> tuple[dict[tuple[str, str, int], str], dict[int, dict[str, Any]]]:
    from sqlalchemy import text

    result: dict[tuple[str, str, int], str] = {}
    events: dict[int, dict[str, Any]] = {}
    total = 0
    deadline = time.monotonic() + 20
    with engine.connect() as c, c.begin():
        c.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        for name, sequence in RESOURCE_TABLES.items():
            last = None
            count = 0
            while True:
                require(time.monotonic() <= deadline, "PROBE_RESOURCE_REVIEW_TIMEOUT")
                where = (
                    ""
                    if last is None
                    else 'WHERE ROW(scope,"' + sequence + '") > ROW(:scope,:number) '
                )
                query = (
                    'SELECT row_to_json(t)::text FROM (SELECT * FROM "'
                    + name
                    + '" '
                    + where
                    + 'ORDER BY scope,"'
                    + sequence
                    + '" LIMIT 1) t'
                )
                raw = c.execute(
                    text(query), {} if last is None else dict(scope=last[0], number=last[1])
                ).scalar_one_or_none()
                if raw is None:
                    break
                total += len(raw.encode())
                count += 1
                require(count <= 2048 and total <= 16 * 1024 * 1024, "PROBE_RESOURCE_REVIEW_LIMIT")
                row = json.loads(raw)
                last = (row["scope"], row[sequence])
                key = (name, *last)
                require(key not in result, "PROBE_RESOURCE_KEY_REPEATED")
                result[key] = hashlib.sha256(raw.encode()).hexdigest()
                if name == "demo_read_audit_events" and row["scope"] == scope:
                    events[row["sequence"]] = row["action"]
    return result, events


def run_probe(
    engine: Any,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    account: str,
    settings: Any,
    credential_loader: Any,
    fingerprint: Any,
    *,
    apply: bool = False,
    clocks: Any = None,
    sender: Any = None,
    source_reference: str = "",
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    require(apply is True, "PROBE_EXPLICIT_RUN_REQUIRED")
    from sqlalchemy import select, text

    from integrations.binance_usdm.demo import DemoClient
    from packages.persistence.demo import accounts
    from packages.persistence.demo_read_audit import AuditedDemoReadJournal, ReadAuditStore
    from packages.persistence.demo_read_recovery import (
        _private_directory,
        _sync_directory,
        _write_new,
    )
    from packages.persistence.demo_shared_checkpoint import SharedWeightAnchorStore
    from services.execution.demo_read_audit_bootstrap import (
        prepare_read_audit,
        validate_account_row,
    )
    from services.execution.demo_runtime_read_transport import AccountedDemoReadTransport
    from services.execution.demo_runtime_storage import RuntimeClocks

    state = {} if state is None else state
    state.update(stage="LOCAL_PRECHECK", intent_created=False)
    AccountedDemoReadTransport._context()
    require(
        settings.mode == "DEMO"
        and settings.demo_trading_enabled is False
        and settings.live_trading_enabled is False
        and settings.margin_mode == "ISOLATED"
        and settings.symbols == ("BTCUSDT",),
        "PROBE_SETTINGS_INVALID",
    )
    current = clocks or RuntimeClocks()
    anchor, audit = SharedWeightAnchorStore(engine), ReadAuditStore(engine)
    require(anchor.installation_id() == installation, "PROBE_INSTALLATION_MISMATCH")
    operation = str(uuid5(journal, POLICY))
    marker = directory.parent / ("accounted-read-probe-" + operation)
    _private_directory(directory.parent)
    root_fd = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    transport = None
    try:
        try:
            fcntl.flock(root_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ProbeBlocked("PROBE_ALREADY_RUNNING") from None
        require(
            not marker.exists() and not marker.is_symlink(),
            "PROBE_ALREADY_ATTEMPTED_REVIEW_REQUIRED",
        )
        plan = prepare_read_audit(
            engine, account, directory, scope, journal, installation, clocks=current
        )
        require(
            not plan["audit_schema_required"] and not plan["audit_binding_required"],
            "PROBE_AUDIT_NOT_CONFIGURED",
        )
        shared = AuditedDemoReadJournal(
            directory,
            scope,
            journal,
            anchors=anchor,
            audit=audit,
            monotonic=current.monotonic,
            wall_clock=current.wall_clock,
            boot=current.boot,
        )
        original = audit.review(shared)
        require(
            original["startup_ready"]
            and original["local_read_activity_complete"]
            and original["audit_events"] + 4 <= 1024,
            "PROBE_AUDIT_NOT_READY",
        )
        state["stage"] = "PROTECTED_STATE_BEFORE"
        before = protected_snapshot(engine, directory.parent, shared.path, marker, fingerprint)
        state["stage"] = "PRIVATE_CREDENTIAL_SESSION"
        try:
            credentials = credential_loader()
        except Exception:
            raise ProbeBlocked("PROBE_CREDENTIAL_SESSION_UNAVAILABLE") from None
        # No marker or resource charge exists until credentials and local checks pass.
        with engine.connect() as control, control.begin():
            control.execute(text("SET LOCAL lock_timeout='2s'"))
            control.execute(text("SET LOCAL statement_timeout='2s'"))
            row = (
                control.execute(
                    select(accounts)
                    .where(accounts.c.account_id == account)
                    .with_for_update(read=True)
                )
                .mappings()
                .one()
            )
            validate_account_row(row, scope, journal, installation, current)
            resources_before, _ = resource_rows(engine, scope)
            kwargs = {} if sender is None else dict(sender=sender)
            transport = AccountedDemoReadTransport(
                settings,
                shared,
                enable_reads=True,
                monotonic=current.monotonic,
                max_requests=2,
                max_seconds=30,
                **kwargs,
            )
            client = DemoClient(
                settings,
                credentials,
                send=transport,
                clock_ms=lambda: int(current.wall_clock().timestamp() * 1000),
            )
            os.mkdir(marker, mode=0o700)
            state["intent_created"] = True
            intent = dict(
                policy=POLICY,
                operation_id=operation,
                source_reference=source_reference,
                scope=scope,
                journal=str(journal),
                installation=str(installation),
                protected_snapshot_sha256=before,
                planned_gets=2,
                modeled_weight=6,
            )
            _write_new(marker / "intent.json", (json.dumps(intent, sort_keys=True) + "\n").encode())
            _sync_directory(marker)
            _sync_directory(directory.parent)
            state["stage"] = "ACCOUNTED_PUBLIC_TIME_GET"
            client.sync_clock()
            state["stage"] = "ACCOUNTED_SIGNED_CONFIG_GET"
            config = client.request("GET", "/fapi/v1/accountConfig")
            require(
                isinstance(config, dict)
                and config.get("dualSidePosition") is False
                and config.get("multiAssetsMargin") is False,
                "PROBE_ACCOUNT_MODE_MISMATCH",
            )
            state["stage"] = "AUDIT_AND_CHECKPOINT_POSTCHECK"
            final = audit.review(shared)
            resources_after, actions = resource_rows(engine, scope)
            require(
                all(resources_after.get(k) == v for k, v in resources_before.items()),
                "PROBE_PRIOR_RESOURCE_ROWS_CHANGED",
            )
            additions = set(resources_after) - set(resources_before)
            old_revision = max(
                k[2]
                for k in resources_before
                if k[0] == "demo_shared_weight_checkpoints" and k[1] == scope
            )
            first_sequence = original["audit_events"]
            expected = {
                ("demo_shared_weight_checkpoints", scope, old_revision + i) for i in range(1, 5)
            } | {("demo_read_audit_events", scope, first_sequence + i) for i in range(4)}
            require(additions == expected, "PROBE_RESOURCE_DELTA_INVALID")
            for offset, (path, cost) in enumerate(
                [("/fapi/v1/time", 1), ("/fapi/v1/accountConfig", 5)]
            ):
                for phase, kind in enumerate(["RESERVE", "FINISH"]):
                    action = actions[first_sequence + offset * 2 + phase]
                    require(
                        action["kind"] == kind
                        and action["path"] == path
                        and action["cost"] == cost,
                        "PROBE_AUDIT_ROUTE_MISMATCH",
                    )
                    if phase:
                        require(
                            action["sent"] is True and action["status"] == 200,
                            "PROBE_RESPONSE_UNCONFIRMED",
                        )
            require(
                final["audit_events"] == original["audit_events"] + 4
                and final["shared_total_weight"] == original["shared_total_weight"] + 6
                and final["local_read_activity_complete"],
                "PROBE_AUDIT_DELTA_INVALID",
            )
        transport.close()
        metrics = transport.diagnostics()
        require(
            metrics["http_attempts"] == metrics["completed_reads"] == 2
            and metrics["modeled_weight_charged"] == 6,
            "PROBE_TRANSPORT_DELTA_INVALID",
        )
        state["stage"] = "PROTECTED_STATE_AFTER"
        after = protected_snapshot(engine, directory.parent, shared.path, marker, fingerprint)
        require(after == before, "PROBE_PROTECTED_STATE_CHANGED")
        result = dict(
            accounted_read_probe="TWO_GETS_AUDITED_KEEP_PAUSED",
            operation_id=operation,
            http_attempts=2,
            completed_reads=2,
            modeled_weight_charged=6,
            audit_events_added=4,
            resource_checkpoints_added=4,
            prior_resource_rows_preserved=True,
            protected_snapshot_sha256=after,
            financial_ledger_unchanged_verified=True,
            account_mode_fields_verified=True,
            exchange_account_identity_verified=False,
            positions_or_orders_checked=False,
            account_order_usage_verified=False,
            actual_ip_capacity_reserved=False,
            request_cost_upper_bounds_proven=False,
            financial_proof_verified=False,
            trading_readiness_checked=False,
            execution_approved=False,
            orders_enabled=False,
            live_trading_enabled=False,
            order_writes=0,
            synthetic_sender_configured=sender is not None,
            next_action="KEEP_PAUSED_REVIEW_AUDIT_NO_AUTOMATIC_RETRY",
        )
        _write_new(marker / "complete.json", (json.dumps(result, sort_keys=True) + "\n").encode())
        _sync_directory(marker)
        state["stage"] = "COMPLETE"
        return result
    finally:
        if transport is not None:
            transport.close()
            state["transport"] = transport.diagnostics()
        os.close(root_fd)


def recover_probe_clock(
    engine: Any,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    account: str,
    fingerprint: Any,
    *,
    apply: bool = False,
    expected: str | None = None,
    clocks: Any = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recover only an unused, audited scope; review polls never rebase again."""
    require(apply is True or expected is not None, "PROBE_CLOCK_EXPLICIT_OPERATION_REQUIRED")
    require(not (apply and expected is not None), "PROBE_CLOCK_FLAGS_INVALID")
    from sqlalchemy import select, text

    from packages.persistence.demo import accounts
    from packages.persistence.demo_read_audit import AuditedDemoReadJournal, ReadAuditStore
    from packages.persistence.demo_read_recovery import (
        PREFIX,
        _file,
        _operation,
        _private_directory,
        _raw,
        recover_completed_read_clock,
    )
    from packages.persistence.demo_shared_checkpoint import SharedWeightAnchorStore
    from services.execution.demo_read_audit_bootstrap import validate_account_row
    from services.execution.demo_runtime_read_transport import AccountedDemoReadTransport
    from services.execution.demo_runtime_storage import RuntimeClocks

    state = {} if state is None else state
    state["stage"] = "AUDITED_CLOCK_PRECHECK"
    AccountedDemoReadTransport._context()
    current = clocks or RuntimeClocks()
    anchor, audit = SharedWeightAnchorStore(engine), ReadAuditStore(engine)
    require(anchor.installation_id() == installation, "PROBE_INSTALLATION_MISMATCH")
    marker = directory.parent / ("accounted-read-probe-" + str(uuid5(journal, POLICY)))
    _private_directory(directory.parent)
    fd = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ProbeBlocked("PROBE_ALREADY_RUNNING") from None
        require(
            not marker.exists() and not marker.is_symlink(),
            "PROBE_ALREADY_ATTEMPTED_REVIEW_REQUIRED",
        )
        with engine.connect() as control, control.begin():
            control.execute(text("SET LOCAL lock_timeout='2s'"))
            control.execute(text("SET LOCAL statement_timeout='2s'"))
            row = (
                control.execute(
                    select(accounts)
                    .where(accounts.c.account_id == account)
                    .with_for_update(read=True)
                )
                .mappings()
                .one()
            )
            validate_account_row(row, scope, journal, installation, current)
            if apply:
                # Verify complete history without trusting an invalid boot/wall-clock sample.
                with _raw(directory, scope, journal, anchor) as (cp, local, shared_file):
                    history, snapshot = audit._verified_history(cp, local)
                    require(
                        snapshot[1][0][7:] == [0, 0] and not snapshot[2],
                        "PROBE_CLOCK_UNUSED_SCOPE_REQUIRED",
                    )
                    require(
                        all(r["action"]["kind"] == "CLOCK_REBASE" for r in history),
                        "PROBE_CLOCK_UNUSED_SCOPE_REQUIRED",
                    )
                    revision, old_digest = cp.revision, cp.digest
                    body_sha = hashlib.sha256(_file(shared_file)).hexdigest()
                folder = directory / (PREFIX + _operation(str(journal), revision, old_digest))
                require(
                    not folder.exists() and not folder.is_symlink(),
                    "PROBE_CLOCK_BACKUP_ALREADY_PRESENT",
                )
                resources_before, actions_before = resource_rows(engine, scope)
                before = protected_snapshot(
                    engine, directory.parent, shared_file, marker, fingerprint
                )
                state["stage"] = "AUDITED_CLOCK_RECOVERY"
                state["clock_apply_started"] = True
                report = recover_completed_read_clock(
                    directory,
                    scope,
                    journal,
                    anchor=anchor,
                    audit=audit,
                    apply=True,
                    clocks=current,
                )
                changed = report["shared_journal_changed"]
                resources_after, actions_after = resource_rows(engine, scope)
                require(
                    all(resources_after.get(k) == v for k, v in resources_before.items()),
                    "PROBE_PRIOR_RESOURCE_ROWS_CHANGED",
                )
                additions = set(resources_after) - set(resources_before)
                expected_delta = (
                    {
                        ("demo_shared_weight_checkpoints", scope, revision + 1),
                        ("demo_read_audit_events", scope, len(actions_before)),
                    }
                    if changed
                    else set()
                )
                require(additions == expected_delta, "PROBE_CLOCK_RESOURCE_DELTA_INVALID")
                if changed:
                    action = actions_after[len(actions_before)]
                    require(
                        action["kind"] == "CLOCK_REBASE"
                        and action["sent"] is False
                        and action["cost"] is None,
                        "PROBE_CLOCK_RESOURCE_DELTA_INVALID",
                    )
                    require(
                        hashlib.sha256(_file(folder / "original.sqlite3")).hexdigest() == body_sha,
                        "PROBE_CLOCK_BACKUP_CHANGED",
                    )
                    require(
                        set(p.name for p in folder.iterdir())
                        == {"original.sqlite3", "intent.json"},
                        "PROBE_CLOCK_BACKUP_INVALID",
                    )
                after = protected_snapshot(
                    engine,
                    directory.parent,
                    shared_file,
                    marker,
                    fingerprint,
                    excluded_folder=folder if changed else None,
                )
                require(after == before, "PROBE_PROTECTED_STATE_CHANGED")
                state["stage"] = "CLOCK_PROTECTED_STATE_POSTCHECK"
                full_snapshot = protected_snapshot(
                    engine, directory.parent, shared_file, marker, fingerprint
                )
            else:
                require(
                    isinstance(expected, str)
                    and re.fullmatch("[0-9a-f]{64}", expected) is not None,
                    "PROBE_CLOCK_EXPECTED_SNAPSHOT_INVALID",
                )
                shared = AuditedDemoReadJournal(
                    directory,
                    scope,
                    journal,
                    anchors=anchor,
                    audit=audit,
                    read_only=True,
                    monotonic=current.monotonic,
                    wall_clock=current.wall_clock,
                    boot=current.boot,
                )
                report = audit.review(shared)
                full_snapshot = protected_snapshot(
                    engine, directory.parent, shared.path, marker, fingerprint
                )
                require(full_snapshot == expected, "PROBE_PROTECTED_STATE_CHANGED")
                changed = False
            require(
                report["shared_total_weight"] == 0 and report["local_read_activity_complete"],
                "PROBE_CLOCK_UNUSED_SCOPE_REQUIRED",
            )
        return dict(
            accounted_clock_recovery="RECOVERED_KEEP_PAUSED"
            if report["startup_ready"]
            else "WAIT_STARTUP_HOLD",
            startup_ready=report["startup_ready"],
            startup_hold_seconds=70.001,
            recovery_post_snapshot_sha256=full_snapshot,
            shared_journal_changed=changed,
            audit_clock_event_added=changed,
            resource_checkpoint_added=changed,
            prior_resource_rows_preserved=True,
            financial_ledger_unchanged_verified=True,
            exchange_credentials_accessed=False,
            http_attempts=0,
            order_writes=0,
            modeled_weight_charged=0,
            execution_approved=False,
            orders_enabled=False,
            live_trading_enabled=False,
            next_action="WAIT_THEN_SINGLE_TWO_GET_PROBE"
            if not report["startup_ready"]
            else "SINGLE_TWO_GET_PROBE",
        )
    finally:
        os.close(fd)


def main() -> int:
    state: dict[str, Any] = {"stage": "SOURCE_VALIDATION", "intent_created": False}
    engine = None
    safe_types: tuple[type[BaseException], ...] = (ProbeBlocked,)
    try:
        args = sys.argv[2:]
        require(
            args in (["--run-two-gets"], ["--recover-clock"])
            or (
                len(args) == 2
                and args[0] == "--review-clock"
                and re.fullmatch("[0-9a-f]{64}", args[1]) is not None
            ),
            "PROBE_EXPLICIT_RUN_REQUIRED",
        )
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / "PROBE_MANIFEST.json").read_text())
        installed = Path("/app")
        verify_files(installed, manifest["installed_files"])
        verify_files(root, manifest["candidate_files"])
        import yaml
        from sqlalchemy import create_engine, select, text
        from sqlalchemy.pool import NullPool

        from integrations.binance_usdm.demo import DemoSettings
        from packages.domain.config import UniqueKeyLoader
        from packages.persistence.demo import accounts
        from packages.persistence.demo_order_quota import LocalQuotaStopped
        from packages.persistence.demo_shared_weight import SharedWeightStopped
        from scripts.demo_key_session import credentials
        from services.execution.demo_runtime_storage import RUNTIME_STORAGE_KEY

        safe_types = (ProbeBlocked, LocalQuotaStopped, SharedWeightStopped)

        require(not any(str(root) in p for p in sys.path), "PROBE_CANDIDATE_IMPORT_PATH_FORBIDDEN")
        require(len(manifest["runtime_modules"]) == 6, "PROBE_INSTALLED_MODULE_SET_INVALID")
        for name in manifest["runtime_modules"]:
            require(
                name in manifest["installed_files"] and name.endswith(".py"),
                "PROBE_INSTALLED_MODULE_SET_INVALID",
            )
            module = importlib.import_module(name.removesuffix(".py").replace("/", "."))
            require(
                Path(module.__file__ or "").resolve() == (installed / name).resolve(),
                "PROBE_IMPORTED_SOURCE_ORIGIN_INVALID",
            )
        state["stage"] = "ACCOUNT_RECEIPTS"
        url = os.environ.get("DATABASE_URL", "")
        require(url.startswith("postgresql+psycopg://"), "PROBE_DATABASE_CONFIGURATION_REQUIRED")
        engine = create_engine(
            url,
            poolclass=NullPool,
            hide_parameters=True,
            connect_args={
                "connect_timeout": 5,
                "options": "-c statement_timeout=2000 -c lock_timeout=2000",
            },
        )
        with engine.connect() as c, c.begin():
            c.execute(text("SET TRANSACTION READ ONLY"))
            data = c.execute(
                select(accounts.c.state).where(accounts.c.account_id == ACCOUNT)
            ).scalar_one()
        binding = json.loads(data["operations"][RUNTIME_STORAGE_KEY])
        record = json.loads(data["operations"][SETUP_KEY])
        require(
            record.get("stage") == "PREPARED"
            and record.get("source") == BASELINE
            and record.get("scope") == SCOPE
            and record.get("account") == ACCOUNT
            and record.get("directory") == "demo-runtime-20261111"
            and record.get("shared_journal") == binding["journal"]
            and record.get("anchor_installation") == binding["checkpoint_installation"]
            and record.get("expected_runtime_binding") == data["operations"][RUNTIME_STORAGE_KEY],
            "PROBE_STORAGE_RECEIPTS_MISMATCH",
        )
        settings = DemoSettings.model_validate(
            yaml.load(
                (installed / "configs/demo-isolated.example.yaml").read_text(),
                Loader=UniqueKeyLoader,
            )
        )
        spec = importlib.util.spec_from_file_location(
            "_probe_fingerprint", root / "probe_fingerprint.py"
        )
        require(spec is not None and spec.loader is not None, "PROBE_FINGERPRINT_MODULE_INVALID")
        assert spec is not None and spec.loader is not None
        fingerprint = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fingerprint)
        if args[0] == "--run-two-gets":
            result = run_probe(
                engine,
                STORAGE,
                SCOPE,
                UUID(binding["journal"]),
                UUID(binding["checkpoint_installation"]),
                ACCOUNT,
                settings,
                lambda: credentials(prompt=False),
                fingerprint,
                apply=True,
                source_reference=manifest["source_reference"],
                state=state,
            )
            result["installed_transport_verified"] = True
        else:
            result = recover_probe_clock(
                engine,
                STORAGE,
                SCOPE,
                UUID(binding["journal"]),
                UUID(binding["checkpoint_installation"]),
                ACCOUNT,
                fingerprint,
                apply=args[0] == "--recover-clock",
                expected=args[1] if len(args) == 2 else None,
                state=state,
            )
            result["installed_recovery_verified"] = True
        code = 0
    except Exception as error:
        reason = (
            str(error)
            if isinstance(error, safe_types) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,100}", str(error))
            else "PROBE_REVIEW_UNAVAILABLE"
        )
        metrics = state.get("transport", {})
        result = dict(
            accounted_read_probe="BLOCKED_KEEP_PAUSED",
            stage=state["stage"],
            stop_reason=reason,
            intent_created=state["intent_created"],
            clock_apply_started=state.get("clock_apply_started", False),
            clock_resource_state="REVIEW_REQUIRED"
            if state.get("clock_apply_started")
            else "NO_CLOCK_APPLY_STARTED",
            http_attempts=metrics.get("http_attempts", 0),
            completed_reads=metrics.get("completed_reads", 0),
            modeled_weight_charged=metrics.get("modeled_weight_charged", 0),
            resource_state="REVIEW_REQUIRED"
            if state["intent_created"]
            else "NO_PROBE_REQUEST_STARTED",
            financial_ledger_writes_by_probe=0,
            financial_ledger_unchanged_verified=False,
            order_writes=0,
            execution_approved=False,
            orders_enabled=False,
            live_trading_enabled=False,
            automatic_retry=False,
            next_action="REVIEW_OUTPUT_NO_REPEAT_AFTER_INTENT",
        )
        code = 1
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                result["database_cleanup_failed"] = True
                if code == 0:
                    result.update(
                        accounted_read_probe="BLOCKED_KEEP_PAUSED",
                        stop_reason="PROBE_DATABASE_CLEANUP_FAILED",
                        automatic_retry=False,
                    )
                    code = 1
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
