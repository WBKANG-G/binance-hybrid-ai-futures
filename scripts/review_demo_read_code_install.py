"""Snapshot guard for a code-only image replacement, never a trading admission."""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

from packages.persistence.demo_order_quota import canonical
from packages.persistence.demo_read_audit import require
from packages.persistence.demo_read_recovery import _file, _private_directory
from scripts import review_demo_read_deployment as deployment
from services.execution.demo_runtime_storage import RuntimeClocks

INSTALL_FILES = (
    "packages/persistence/demo_read_audit.py",
    "packages/persistence/demo_read_recovery.py",
    "services/execution/demo_read_audit_bootstrap.py",
    "services/execution/demo_unused_read_clock.py",
    "services/execution/demo_runtime_read_transport.py",
    "scripts/review_demo_read_journal.py",
)


def preserved_snapshot(engine: Any, storage_root: Path) -> str:
    value: dict[str, Any] = {}
    with engine.connect() as c, c.begin():
        c.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        names = sorted(n for n in inspect(c).get_table_names() if n.startswith("demo_"))
        require(1 <= len(names) <= 40, "READ_INSTALL_DATABASE_LIMIT")
        for name in names:
            require(
                re.fullmatch(r"demo_[a-z0-9_]+", name) is not None, "READ_INSTALL_DATABASE_LIMIT"
            )
            rows = (
                c.execute(text('SELECT row_to_json(t) FROM "' + name + '" t LIMIT 10001'))
                .scalars()
                .all()
            )
            require(len(rows) <= 10000, "READ_INSTALL_DATABASE_LIMIT")
            value[name] = sorted(canonical(row) for row in rows)
            require(
                len(canonical(value).encode()) <= 32 * 1024 * 1024, "READ_INSTALL_DATABASE_LIMIT"
            )
    _private_directory(storage_root)
    files: dict[str, str] = {}
    total = 0
    for path in sorted(storage_root.rglob("*")):
        require(not path.is_symlink(), "READ_INSTALL_STORAGE_SYMLINK")
        if path.is_dir():
            _private_directory(path)
            continue
        require(not path.name.endswith(("-wal", "-shm", "-journal")), "READ_INSTALL_STORAGE_BUSY")
        body = _file(path)
        total += len(body)
        require(len(files) < 128 and total <= 128 * 1024 * 1024, "READ_INSTALL_STORAGE_LIMIT")
        files[str(path.relative_to(storage_root))] = hashlib.sha256(body).hexdigest()
    require(bool(files), "READ_INSTALL_STORAGE_MISSING")
    return hashlib.sha256(canonical(dict(database=value, storage=files)).encode()).hexdigest()


def installed_review(root: Path, storage: Path, expected_files: dict[str, str]) -> dict[str, Any]:
    # A separate isolated process resolves the operational command from /app,
    # independently of the temporary candidate's imports.
    module_names = [name.removesuffix(".py").replace("/", ".") for name in INSTALL_FILES]
    source = """
import importlib,json,pathlib,sys
sys.dont_write_bytecode=True
sys.path.insert(0,sys.argv[1])
for name in json.loads(sys.argv[2]):
 m=importlib.import_module(name)
 assert pathlib.Path(m.__file__).resolve().is_relative_to(pathlib.Path(sys.argv[1]).resolve())
from sqlalchemy import create_engine,select
from packages.persistence.demo import accounts
from services.execution.demo_runtime_storage import RUNTIME_STORAGE_KEY
from scripts.review_demo_read_journal import run
import os
e=create_engine(os.environ['DATABASE_URL'],hide_parameters=True,connect_args={'connect_timeout':5,'options':'-c statement_timeout=2000'})
with e.connect() as c:
 state=c.execute(select(accounts.c.state).where(accounts.c.account_id==sys.argv[3])).scalar_one()
b=json.loads(state['operations'][RUNTIME_STORAGE_KEY])
e.dispose()
code,result=run(['review','--directory',sys.argv[4],'--scope',b['scope'],'--journal',b['journal'],'--installation',b['checkpoint_installation']])
assert code==0 and result['local_read_activity_complete'] and result['startup_ready']
assert result['execution_approved'] is False and result['orders_enabled'] is False
print(json.dumps({'installed_command_verified':True,'audit_events':result['audit_events']}))
"""
    deployment.verify_files(root, expected_files)
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            source,
            str(root),
            json.dumps(module_names),
            deployment.ACCOUNT,
            str(storage),
        ],
        capture_output=True,
        timeout=20,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
    )
    require(result.returncode == 0, "READ_INSTALL_OPERATIONAL_COMMAND_FAILED")
    report = json.loads(result.stdout)
    require(
        report.get("installed_command_verified") is True, "READ_INSTALL_OPERATIONAL_COMMAND_FAILED"
    )
    return dict(report)


def review(
    candidate: Path,
    *,
    installed: Path = Path("/app"),
    phase: str,
    expected: str | None = None,
    clocks: RuntimeClocks | None = None,
) -> dict[str, Any]:
    require(phase in {"pre", "post"}, "READ_INSTALL_PHASE_INVALID")
    if phase == "post":
        require(
            isinstance(expected, str) and re.fullmatch("[0-9a-f]{64}", expected) is not None,
            "READ_INSTALL_EXPECTED_SNAPSHOT_REQUIRED",
        )
    manifest = json.loads((candidate / "CANDIDATE_MANIFEST.json").read_text())
    targets = manifest["install_files"]
    require(
        set(targets) == set(INSTALL_FILES)
        and not (set(targets) & set(manifest["installed_files"])),
        "READ_INSTALL_TARGET_MANIFEST_INVALID",
    )
    present = [n for n in targets if (installed / n).exists() or (installed / n).is_symlink()]
    require(not present or set(present) == set(targets), "READ_INSTALL_PARTIAL_SOURCE")
    if present:
        deployment.verify_files(installed, targets)
    require(phase != "post" or bool(present), "READ_INSTALL_SOURCE_NOT_INSTALLED")
    # The candidate manifest operation remains review: this never provisions or repairs.
    base = deployment.review(candidate, installed_root=installed, clocks=clocks)
    base.pop("api_replaced", None)
    require(
        not base["audit_schema_required"] and not base["audit_binding_required"],
        "READ_INSTALL_AUDIT_SETUP_REQUIRED",
    )
    url = os.environ["DATABASE_URL"]
    engine = create_engine(
        url,
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={
            "connect_timeout": 5,
            "options": "-c statement_timeout=2000 -c lock_timeout=2000",
        },
    )
    try:
        before = preserved_snapshot(engine, deployment.STORAGE.parent)
        if expected is not None:
            require(before == expected, "READ_INSTALL_PRESERVED_STATE_CHANGED")
        direct = installed_review(installed, deployment.STORAGE, targets) if phase == "post" else {}
        after = preserved_snapshot(engine, deployment.STORAGE.parent)
        require(before == after, "READ_INSTALL_PRESERVED_STATE_CHANGED")
    finally:
        engine.dispose()
    return (
        base
        | direct
        | dict(
            read_code_install="POSTCHECKED_READ_CODE_INSTALLED_KEEP_PAUSED"
            if phase == "post"
            else "PRECHECKED_READ_CODE_INSTALL_KEEP_PAUSED",
            preserved_snapshot_sha256=after,
            target_already_installed=bool(present),
            target_file_count=len(targets),
            installed_command_verified=phase == "post",
            source_installation_only=True,
            api_replacement_performed_by_guard=False,
            exchange_http_attempts=0,
            order_writes=0,
            execution_approved=False,
            orders_enabled=False,
            live_trading_enabled=False,
        )
    )


def main() -> int:
    try:
        args = sys.argv[2:]
        require(len(args) in (1, 2), "READ_INSTALL_ARGUMENTS_INVALID")
        report = review(
            Path(__file__).resolve().parents[1],
            phase=args[0],
            expected=args[1] if len(args) == 2 else None,
        )
        code = 0
    except Exception as error:
        from packages.persistence.demo_order_quota import LocalQuotaStopped

        reason = (
            str(error)
            if isinstance(error, LocalQuotaStopped)
            and re.fullmatch(r"[A-Z][A-Z0-9_]{1,100}", str(error))
            else "READ_INSTALL_REVIEW_UNAVAILABLE"
        )
        report = dict(
            read_code_install="BLOCKED_KEEP_PAUSED",
            stop_reason=reason,
            exchange_http_attempts=0,
            order_writes=0,
            execution_approved=False,
            orders_enabled=False,
            live_trading_enabled=False,
        )
        code = 1
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
