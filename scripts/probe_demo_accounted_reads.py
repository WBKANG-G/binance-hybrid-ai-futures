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
FLAT_POLICY = "INSTALLED_ACCOUNTED_FLAT_READ_REVIEW_V1"
BASIC_ROUTES = (("/fapi/v1/time", 1), ("/fapi/v1/accountConfig", 5))
FLAT_ROUTES = BASIC_ROUTES + (
    ("/fapi/v3/positionRisk", 5),
    ("/fapi/v1/openOrders", 40),
    ("/fapi/v1/openAlgoOrders", 40),
)
DAY13_POLICY = "OPERATOR_ATTESTED_ACCOUNTED_DAY13_PREVIEW_V1"
DAY13_ROUTES = (
    BASIC_ROUTES
    + (("/fapi/v3/account", 5),)
    + FLAT_ROUTES[2:]
    + (("/fapi/v1/income", 30), ("/fapi/v3/account", 5))
)
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


def verify_flat_payload(path: str, value: Any) -> int:
    """Accept explicit complete list shapes; never turn missing/invalid data into zero."""
    from decimal import Decimal

    require(isinstance(value, list) and len(value) <= 4096, "FLAT_RESPONSE_LIST_REQUIRED")
    if path != "/fapi/v3/positionRisk":
        require(path in ("/fapi/v1/openOrders", "/fapi/v1/openAlgoOrders"), "FLAT_ROUTE_INVALID")
        require(not value, "FLAT_OPEN_ORDERS_OBSERVED")
        return 0
    symbols: set[str] = set()
    for row in value:
        require(isinstance(row, dict), "FLAT_POSITION_ROW_INVALID")
        symbol, amount = row.get("symbol"), row.get("positionAmt")
        require(
            isinstance(symbol, str)
            and re.fullmatch("[A-Z0-9_]{1,40}", symbol) is not None
            and symbol not in symbols,
            "FLAT_POSITION_SYMBOL_INVALID",
        )
        symbols.add(symbol)
        require(row.get("positionSide") == "BOTH", "FLAT_POSITION_SIDE_INVALID")
        require(
            isinstance(amount, str)
            and len(amount) <= 48
            and re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", amount) is not None,
            "FLAT_POSITION_AMOUNT_INVALID",
        )
        require(Decimal(amount) == 0, "FLAT_NONZERO_POSITION_OBSERVED")
    return len(value)


def verify_prior_probe(
    engine: Any,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    shared: Any,
    original: Any,
    fingerprint: Any,
) -> None:
    from packages.persistence.demo_read_recovery import _file, _private_directory

    operation = str(uuid5(journal, POLICY))
    folder = directory.parent / ("accounted-read-probe-" + operation)
    _private_directory(folder)
    require(
        {p.name for p in folder.iterdir()} == {"intent.json", "complete.json"},
        "FLAT_PRIOR_PROBE_RECEIPT_REQUIRED",
    )
    intent = json.loads(_file(folder / "intent.json", 16384))
    complete = json.loads(_file(folder / "complete.json", 16384))
    require(
        isinstance(intent, dict) and isinstance(complete, dict), "FLAT_PRIOR_PROBE_RECEIPT_INVALID"
    )
    require(
        intent.get("policy") == POLICY
        and intent.get("operation_id") == operation
        and intent.get("scope") == scope
        and intent.get("journal") == str(journal)
        and intent.get("installation") == str(installation)
        and intent.get("planned_gets") == 2
        and intent.get("modeled_weight") == 6,
        "FLAT_PRIOR_PROBE_RECEIPT_INVALID",
    )
    require(
        complete.get("operation_id") == operation
        and complete.get("accounted_read_probe") == "TWO_GETS_AUDITED_KEEP_PAUSED"
        and complete.get("http_attempts") == complete.get("completed_reads") == 2
        and complete.get("modeled_weight_charged") == 6
        and complete.get("audit_events_added") == complete.get("resource_checkpoints_added") == 4
        and complete.get("account_mode_fields_verified") is True
        and complete.get("financial_ledger_unchanged_verified") is True
        and all(
            complete.get(k) is False
            for k in ("execution_approved", "orders_enabled", "live_trading_enabled")
        ),
        "FLAT_PRIOR_PROBE_RECEIPT_INVALID",
    )
    witness = protected_snapshot(engine, directory.parent, shared.path, folder, fingerprint)
    require(
        witness
        == intent.get("protected_snapshot_sha256")
        == complete.get("protected_snapshot_sha256"),
        "FLAT_PRIOR_PROTECTED_STATE_CHANGED",
    )
    _, actions = resource_rows(engine, scope)
    reads = [a for a in actions.values() if a["kind"] != "CLOCK_REBASE"]
    require(
        len(reads) == 4 and original["shared_total_weight"] == 6, "FLAT_PRIOR_AUDIT_CHAIN_INVALID"
    )
    for offset, (path, cost) in enumerate(BASIC_ROUTES):
        for phase, kind in enumerate(("RESERVE", "FINISH")):
            action = reads[offset * 2 + phase]
            require(
                action["kind"] == kind
                and action["path"] == path
                and action["cost"] == cost
                and (not phase or (action["sent"] is True and action["status"] == 200)),
                "FLAT_PRIOR_AUDIT_CHAIN_INVALID",
            )


def day13_paths(directory: Path, journal: UUID) -> tuple[Path, Path]:
    operation = str(uuid5(journal, DAY13_POLICY))
    return (
        directory.parent / ("accounted-day13-preparation-" + operation),
        directory.parent / ("accounted-day13-preview-" + operation),
    )


def resource_witness(engine: Any, scope: str) -> str:
    rows, _ = resource_rows(engine, scope)
    return hashlib.sha256(
        json.dumps([[list(k), v] for k, v in sorted(rows.items())], separators=(",", ":")).encode()
    ).hexdigest()


def validate_day13_row(row: Any, scope: str, journal: UUID, installation: UUID, clock: Any) -> Any:
    # A preview-only exception for exactly the reviewed November 12 -> 13 boundary.
    # The row remains unchanged and ordinary readiness still rejects it.
    from decimal import Decimal

    from packages.persistence.demo_shared_weight import POLICY as shared_policy
    from services.execution.demo_models import DemoLedger
    from services.execution.demo_runtime_storage import RUNTIME_STORAGE_KEY
    from services.risk.paper import period_keys

    ledger = DemoLedger.model_validate(row["state"])
    require(
        period_keys(clock.wall_clock(), ledger.policy.week_start_kst)
        == ("2026-11-13", "2026-11-09"),
        "DAY13_CURRENT_DAY_REQUIRED",
    )
    require(
        (ledger.daily_key, ledger.weekly_key) == ("2026-11-12", "2026-11-09"),
        "DAY13_REVIEWED_OLD_PERIOD_REQUIRED",
    )
    require(
        row["owner"] is None
        and (row["lease_until"] is None or row["lease_until"] <= clock.wall_clock()),
        "READ_SETUP_ACTIVE_OWNER",
    )
    require(
        ledger.paused and ledger.risk_state.value == "NORMAL", "READ_SETUP_PAUSED_NORMAL_REQUIRED"
    )
    require(
        not any(ledger.positions.values())
        and all(
            not o.remaining and not o.open_quantity and o.position_state.value == "CLOSED"
            for o in ledger.orders.values()
        ),
        "READ_SETUP_LOCAL_FLAT_REQUIRED",
    )
    require(
        ledger.daily_base == ledger.weekly_base == ledger.value.equity == Decimal("4998.74183608")
        and ledger.high_water == Decimal("5000"),
        "DAY13_REVIEWED_BASELINES_CHANGED",
    )
    require(
        ledger.value.as_of.isoformat() == "2026-11-06T07:25:25.109073+00:00",
        "DAY13_VALUATION_ANCHOR_CHANGED",
    )
    require(
        json.loads(ledger.operations.get(RUNTIME_STORAGE_KEY, "{}"))
        == dict(
            version=1,
            scope=scope,
            journal=str(journal),
            checkpoint_installation=str(installation),
            policy=shared_policy,
        ),
        "READ_SETUP_ACCOUNT_BINDING_MISMATCH",
    )
    return ledger


def verify_day13_preparation(
    engine: Any,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    fingerprint: Any,
    shared_file: Path,
) -> dict[str, Any]:
    from packages.persistence.demo_read_recovery import _file, _private_directory

    folder, marker = day13_paths(directory, journal)
    _private_directory(folder)
    require(
        not marker.exists() and not marker.is_symlink(), "PROBE_ALREADY_ATTEMPTED_REVIEW_REQUIRED"
    )
    require(
        {p.name for p in folder.iterdir()} == {"intent.json", "complete.json"},
        "DAY13_PREPARATION_INCOMPLETE",
    )
    record = json.loads(_file(folder / "complete.json", 16384))
    require(
        isinstance(record, dict)
        and record.get("policy") == DAY13_POLICY
        and record.get("scope") == scope
        and record.get("journal") == str(journal)
        and record.get("installation") == str(installation)
        and record.get("operator_attested_day_kst") == "2026-11-13"
        and record.get("prior_two_get_receipt_verified") is True,
        "DAY13_PREPARATION_INVALID",
    )
    require(
        record.get("protected_snapshot_sha256")
        == protected_snapshot(engine, directory.parent, shared_file, folder, fingerprint),
        "PROBE_PROTECTED_STATE_CHANGED",
    )
    require(
        record.get("resource_witness_sha256") == resource_witness(engine, scope),
        "DAY13_RESOURCE_STATE_CHANGED",
    )
    return dict(record)


def day13_wallet(value: Any) -> str:
    from decimal import Decimal

    require(
        isinstance(value, dict) and isinstance(value.get("assets"), list),
        "DAY13_ACCOUNT_RESPONSE_INVALID",
    )
    assets = value["assets"]
    require(
        len(assets) <= 256 and all(isinstance(r, dict) for r in assets),
        "DAY13_ACCOUNT_RESPONSE_INVALID",
    )
    usdt = [r for r in assets if r.get("asset") == "USDT"]
    require(len(usdt) == 1, "DAY13_USDT_ASSET_REQUIRED")

    def amount(raw: Any) -> Any:
        require(
            isinstance(raw, str)
            and len(raw) <= 48
            and re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", raw) is not None,
            "DAY13_AMOUNT_INVALID",
        )
        return Decimal(raw)

    wallet = Decimal("4998.74183608")
    require(
        all(amount(usdt[0].get(k)) == wallet for k in ("walletBalance", "marginBalance"))
        and amount(usdt[0].get("unrealizedProfit")) == 0,
        "DAY13_USDT_BALANCE_CHANGED",
    )
    require(
        all(
            amount(value.get(k)) == wallet
            for k in ("totalWalletBalance", "totalMarginBalance", "availableBalance")
        )
        and all(amount(value.get(k)) == 0 for k in ("totalUnrealizedProfit", "totalInitialMargin")),
        "DAY13_ACCOUNT_MARGIN_CHANGED",
    )
    return str(wallet)


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
    flat_review: bool = False,
    day13_preview: bool = False,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    require(apply is True, "PROBE_EXPLICIT_RUN_REQUIRED")
    require(type(flat_review) is bool, "PROBE_PROFILE_INVALID")
    require(
        type(day13_preview) is bool and not (flat_review and day13_preview), "PROBE_PROFILE_INVALID"
    )
    routes = DAY13_ROUTES if day13_preview else FLAT_ROUTES if flat_review else BASIC_ROUTES
    planned_gets = len(routes)
    event_count = planned_gets * 2
    modeled_weight = sum(cost for _, cost in routes)
    policy = DAY13_POLICY if day13_preview else FLAT_POLICY if flat_review else POLICY
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
    operation = str(uuid5(journal, policy))
    marker = directory.parent / (
        (
            "accounted-day13-preview-"
            if day13_preview
            else "accounted-flat-review-"
            if flat_review
            else "accounted-read-probe-"
        )
        + operation
    )
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
        if day13_preview:
            from services.execution.demo_read_audit_bootstrap import _validate_schema

            with engine.connect() as c:
                validate_day13_row(
                    c.execute(select(accounts).where(accounts.c.account_id == account))
                    .mappings()
                    .one(),
                    scope,
                    journal,
                    installation,
                    current,
                )
                _validate_schema(c)
        else:
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
            and original["audit_events"] + event_count <= 1024,
            "PROBE_AUDIT_NOT_READY",
        )
        if day13_preview:
            state["stage"] = "DAY13_PREPARATION_AND_PRESERVED_STATE"
            verify_day13_preparation(
                engine, directory, scope, journal, installation, fingerprint, shared.path
            )
        if flat_review:
            state["stage"] = "PRIOR_TWO_GET_RECEIPT_AND_AUDIT"
            verify_prior_probe(
                engine, directory, scope, journal, installation, shared, original, fingerprint
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
            if day13_preview:
                validate_day13_row(row, scope, journal, installation, current)
            else:
                validate_account_row(row, scope, journal, installation, current)
            resources_before, _ = resource_rows(engine, scope)
            kwargs = {} if sender is None else dict(sender=sender)
            transport = AccountedDemoReadTransport(
                settings,
                shared,
                enable_reads=True,
                monotonic=current.monotonic,
                max_requests=planned_gets,
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
                policy=policy,
                operation_id=operation,
                source_reference=source_reference,
                scope=scope,
                journal=str(journal),
                installation=str(installation),
                protected_snapshot_sha256=before,
                planned_gets=planned_gets,
                modeled_weight=modeled_weight,
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
            flat_counts: dict[str, int] = {}
            observation_times: dict[str, str] = {}
            if flat_review:
                for path, _ in routes[2:]:
                    state["stage"] = {
                        "/fapi/v3/positionRisk": "ACCOUNTED_POSITION_RISK_GET",
                        "/fapi/v1/openOrders": "ACCOUNTED_REGULAR_ORDERS_GET",
                        "/fapi/v1/openAlgoOrders": "ACCOUNTED_ALGO_ORDERS_GET",
                    }[path]
                    payload = client.request("GET", path)
                    flat_counts[path] = verify_flat_payload(path, payload)
                    observation_times[path] = current.wall_clock().isoformat()
            history_cutoff_ms = 0
            if day13_preview:
                history_cutoff_ms = client.clock_ms() + client.offset
                wallets = []
                for index, (path, _) in enumerate(routes[2:]):
                    state["stage"] = "DAY13_ACCOUNTED_READ_" + str(index + 3)
                    params = (
                        dict(
                            startTime="1794409200000",
                            endTime=str(history_cutoff_ms),
                            limit="1000",
                            page="1",
                        )
                        if path.endswith("/income")
                        else None
                    )
                    payload = client.request("GET", path, params)
                    if path.endswith("/account"):
                        wallets.append(day13_wallet(payload))
                    elif path.endswith("/income"):
                        require(
                            isinstance(payload, list) and not payload,
                            "DAY13_PRIOR_DAY_INCOME_NOT_EMPTY",
                        )
                    else:
                        flat_counts[path] = verify_flat_payload(path, payload)
                    observation_times[path + ":" + str(index)] = current.wall_clock().isoformat()
                require(
                    wallets == ["4998.74183608", "4998.74183608"], "DAY13_WALLET_RECHECK_CHANGED"
                )
                validate_day13_row(row, scope, journal, installation, current)
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
                ("demo_shared_weight_checkpoints", scope, old_revision + i)
                for i in range(1, event_count + 1)
            } | {("demo_read_audit_events", scope, first_sequence + i) for i in range(event_count)}
            require(additions == expected, "PROBE_RESOURCE_DELTA_INVALID")
            for offset, (path, cost) in enumerate(routes):
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
                final["audit_events"] == original["audit_events"] + event_count
                and final["shared_total_weight"] == original["shared_total_weight"] + modeled_weight
                and final["local_read_activity_complete"],
                "PROBE_AUDIT_DELTA_INVALID",
            )
        transport.close()
        metrics = transport.diagnostics()
        require(
            metrics["http_attempts"] == metrics["completed_reads"] == planned_gets
            and metrics["modeled_weight_charged"] == modeled_weight,
            "PROBE_TRANSPORT_DELTA_INVALID",
        )
        state["stage"] = "PROTECTED_STATE_AFTER"
        after = protected_snapshot(engine, directory.parent, shared.path, marker, fingerprint)
        require(after == before, "PROBE_PROTECTED_STATE_CHANGED")
        result: dict[str, Any] = dict(
            accounted_read_probe="DAY13_PREVIEW_NO_FINANCIAL_CHANGE"
            if day13_preview
            else "FIVE_GETS_FLAT_OBSERVED_KEEP_PAUSED"
            if flat_review
            else "TWO_GETS_AUDITED_KEEP_PAUSED",
            operation_id=operation,
            http_attempts=planned_gets,
            completed_reads=planned_gets,
            modeled_weight_charged=modeled_weight,
            audit_events_added=event_count,
            resource_checkpoints_added=event_count,
            prior_resource_rows_preserved=True,
            protected_snapshot_sha256=after,
            financial_ledger_unchanged_verified=True,
            account_mode_fields_verified=True,
            exchange_account_identity_verified=False,
            positions_or_orders_checked=flat_review or day13_preview,
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
        if flat_review:
            result.update(
                prior_two_get_receipt_verified=True,
                positions_empty_at_read=True,
                regular_orders_empty_at_read=True,
                algo_orders_empty_at_read=True,
                position_rows_observed=flat_counts["/fapi/v3/positionRisk"],
                observation_times_utc=observation_times,
                snapshot_atomic=False,
                observation_scope="SEQUENTIAL_ACCOUNT_WIDE_LIST_READS_ONLY",
            )
        if day13_preview:
            result.update(
                operator_attested_day_kst="2026-11-13",
                operator_attestation_used=True,
                old_day_kst="2026-11-12",
                proposed_day_kst="2026-11-13",
                week_kst_preserved="2026-11-09",
                proposed_daily_base_usdt="4998.74183608",
                weekly_base_usdt_preserved="4998.74183608",
                high_water_usdt_preserved="5000.00000000",
                prior_two_get_receipt_verified=True,
                positions_empty_at_read=True,
                regular_orders_empty_at_read=True,
                algo_orders_empty_at_read=True,
                income_since_prior_day_empty=True,
                income_window_start_ms=1794409200000,
                wallet_rechecked=True,
                signed_history_cutoff_ms=history_cutoff_ms,
                exact_day_midnight_valuation_verified=False,
                account_wide_trade_export_verified=False,
                baseline_adoption_approved=False,
                day_adopted=False,
                financial_database_changed_by_preview=False,
                old_valuation_timestamp_preserved=True,
                snapshot_atomic=False,
                observation_times_utc=observation_times,
                next_action="REVIEW_DAY13_VALUES_BEFORE_APPLY",
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
    day13: bool = False,
    clocks: Any = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recover only an unused, audited scope; review polls never rebase again."""
    require(type(day13) is bool, "PROBE_CLOCK_FLAGS_INVALID")
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
    if day13:
        marker, request_marker = day13_paths(directory, journal)
        require(
            not request_marker.exists() and not request_marker.is_symlink(),
            "PROBE_ALREADY_ATTEMPTED_REVIEW_REQUIRED",
        )
    _private_directory(directory.parent)
    fd = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ProbeBlocked("PROBE_ALREADY_RUNNING") from None
        require(
            (day13 and not apply) or (not marker.exists() and not marker.is_symlink()),
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
            if day13:
                validate_day13_row(row, scope, journal, installation, current)
            else:
                validate_account_row(row, scope, journal, installation, current)
            if apply:
                # Verify complete history without trusting an invalid boot/wall-clock sample.
                with _raw(directory, scope, journal, anchor) as (cp, local, shared_file):
                    history, snapshot = audit._verified_history(cp, local)
                    if day13:
                        require(
                            snapshot[1][0][7:] == [6, 0]
                            and all(r[4] is not None for r in snapshot[2]),
                            "DAY13_PRIOR_COMPLETED_READS_REQUIRED",
                        )
                    else:
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
                if day13:
                    from types import SimpleNamespace

                    verify_prior_probe(
                        engine,
                        directory,
                        scope,
                        journal,
                        installation,
                        SimpleNamespace(path=shared_file),
                        dict(shared_total_weight=6),
                        fingerprint,
                    )
                folder = directory / (PREFIX + _operation(str(journal), revision, old_digest))
                require(
                    not folder.exists() and not folder.is_symlink(),
                    "PROBE_CLOCK_BACKUP_ALREADY_PRESENT",
                )
                resources_before, actions_before = resource_rows(engine, scope)
                before = protected_snapshot(
                    engine, directory.parent, shared_file, marker, fingerprint
                )
                if day13:
                    from packages.persistence.demo_read_recovery import _sync_directory, _write_new

                    os.mkdir(marker, mode=0o700)
                    _write_new(
                        marker / "intent.json",
                        json.dumps(
                            dict(
                                policy=DAY13_POLICY,
                                operator_attested_day_kst="2026-11-13",
                                scope=scope,
                                journal=str(journal),
                                installation=str(installation),
                            ),
                            sort_keys=True,
                        ).encode(),
                    )
                    _sync_directory(marker)
                    _sync_directory(directory.parent)
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
                if day13:
                    record = dict(
                        policy=DAY13_POLICY,
                        scope=scope,
                        journal=str(journal),
                        installation=str(installation),
                        operator_attested_day_kst="2026-11-13",
                        prior_two_get_receipt_verified=True,
                        protected_snapshot_sha256=full_snapshot,
                        resource_witness_sha256=resource_witness(engine, scope),
                    )
                    _write_new(
                        marker / "complete.json", json.dumps(record, sort_keys=True).encode()
                    )
                    _sync_directory(marker)
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
                if day13:
                    verify_day13_preparation(
                        engine, directory, scope, journal, installation, fingerprint, shared.path
                    )
                full_snapshot = protected_snapshot(
                    engine, directory.parent, shared.path, marker, fingerprint
                )
                require(full_snapshot == expected, "PROBE_PROTECTED_STATE_CHANGED")
                changed = False
            require(
                report["shared_total_weight"] == (6 if day13 else 0)
                and report["local_read_activity_complete"],
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
            args
            in (
                ["--run-two-gets"],
                ["--review-flat"],
                ["--recover-clock"],
                ["--prepare-day13"],
                ["--preview-day13"],
                ["--apply-day13"],
                ["--day13-adoption-status"],
                ["--prepare-day13-adoption-clock"],
            )
            or (
                len(args) == 2
                and args[0] in ("--review-clock", "--day13-status", "--day13-adoption-clock-status")
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
        if args[0] in (
            "--apply-day13",
            "--day13-adoption-status",
            "--prepare-day13-adoption-clock",
            "--day13-adoption-clock-status",
        ):
            from types import SimpleNamespace

            adoption_spec = importlib.util.spec_from_file_location(
                "_accounted_day13_adoption", root / "scripts/adopt_demo_accounted_day13.py"
            )
            require(
                adoption_spec is not None and adoption_spec.loader is not None,
                "DAY13_APPLY_MODULE_INVALID",
            )
            assert adoption_spec is not None and adoption_spec.loader is not None
            adoption = importlib.util.module_from_spec(adoption_spec)
            adoption_spec.loader.exec_module(adoption)
            common = SimpleNamespace(
                **{
                    name: globals()[name]
                    for name in (
                        "require",
                        "RESOURCE_TABLES",
                        "day13_paths",
                        "DAY13_POLICY",
                        "BASIC_ROUTES",
                        "DAY13_ROUTES",
                        "protected_snapshot",
                        "resource_rows",
                        "resource_witness",
                        "validate_day13_row",
                        "verify_flat_payload",
                        "day13_wallet",
                    )
                }
            )
            if args[0] == "--prepare-day13-adoption-clock":
                result = adoption.prepare_clock(
                    engine,
                    STORAGE,
                    SCOPE,
                    UUID(binding["journal"]),
                    UUID(binding["checkpoint_installation"]),
                    ACCOUNT,
                    lambda: credentials(prompt=False),
                    fingerprint,
                    common,
                    apply=True,
                    state=state,
                )
            elif args[0] == "--day13-adoption-clock-status":
                result = adoption.clock_status(
                    engine,
                    STORAGE,
                    SCOPE,
                    UUID(binding["journal"]),
                    UUID(binding["checkpoint_installation"]),
                    ACCOUNT,
                    fingerprint,
                    common,
                    expected=args[1],
                )
            else:
                result = adoption.run(
                    engine,
                    STORAGE,
                    SCOPE,
                    UUID(binding["journal"]),
                    UUID(binding["checkpoint_installation"]),
                    ACCOUNT,
                    settings,
                    lambda: credentials(prompt=False),
                    fingerprint,
                    common,
                    apply=args[0] == "--apply-day13",
                    status=args[0] == "--day13-adoption-status",
                    state=state,
                )
            result["installed_adoption_modules_verified"] = True
        elif args[0] in ("--run-two-gets", "--review-flat", "--preview-day13"):
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
                flat_review=args[0] == "--review-flat",
                day13_preview=args[0] == "--preview-day13",
                state=state,
            )
            result["installed_transport_verified"] = True
        else:
            if args[0] == "--prepare-day13":
                state["stage"] = "PRIVATE_CREDENTIAL_SESSION_PREFLIGHT"
                try:
                    credentials(prompt=False)
                except Exception:
                    raise ProbeBlocked("PROBE_CREDENTIAL_SESSION_UNAVAILABLE") from None
            result = recover_probe_clock(
                engine,
                STORAGE,
                SCOPE,
                UUID(binding["journal"]),
                UUID(binding["checkpoint_installation"]),
                ACCOUNT,
                fingerprint,
                apply=args[0] in ("--recover-clock", "--prepare-day13"),
                day13=args[0] in ("--prepare-day13", "--day13-status"),
                expected=args[1] if len(args) == 2 else None,
                state=state,
            )
            result["installed_recovery_verified"] = True
            if args[0] == "--prepare-day13":
                result["exchange_credentials_accessed"] = True
                result["credential_session_prechecked"] = True
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
            clock_preparation_created=state.get("clock_preparation_created", False),
            application_may_have_completed=state.get("application_may_have_completed", False),
            clock_resource_state="REVIEW_REQUIRED"
            if state.get("clock_apply_started")
            else "NO_CLOCK_APPLY_STARTED",
            http_attempts=metrics.get("http_attempts", 0),
            completed_reads=metrics.get("completed_reads", 0),
            modeled_weight_charged=metrics.get("modeled_weight_charged", 0),
            resource_state="REVIEW_REQUIRED"
            if state["intent_created"]
            else "NO_PROBE_REQUEST_STARTED",
            financial_ledger_writes_by_probe=None
            if args and args[0] == "--apply-day13" and state.get("application_may_have_completed")
            else 0,
            financial_ledger_unchanged_verified=False,
            order_writes=0,
            execution_approved=False,
            orders_enabled=False,
            live_trading_enabled=False,
            automatic_retry=False,
            next_action="RUN_READ_ONLY_STATUS_NO_APPLY_RETRY"
            if args and args[0] in ("--apply-day13", "--day13-adoption-status")
            else "REVIEW_OUTPUT_NO_REPEAT_AFTER_INTENT",
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
