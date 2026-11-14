"""Image-only import/default-denial review; candidate modules exist only in RAM."""

import base64
import hashlib
import json
import os
import sys
import types
from pathlib import Path
from typing import Any

MODULES = (
    (
        "packages.persistence.demo_response_evidence",
        "packages/persistence/demo_response_evidence.py",
    ),
    (
        "services.execution.demo_response_evidence_transport",
        "services/execution/demo_response_evidence_transport.py",
    ),
)


def collect(config: Any, encoded: Any) -> tuple[dict[str, Any], int]:
    report: dict[str, Any] = dict(
        candidate_review="BLOCKED",
        failure_category="CANDIDATE_REVIEW_UNAVAILABLE",
        baseline_files_verified=0,
        candidate_files_verified=0,
        default_denials_verified=0,
        network_attempts=0,
        database_attempts=0,
        filesystem_write_attempts=0,
        candidate_installed=False,
        database_changed=False,
        financial_proof_verified=False,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
    )
    try:
        if (
            not isinstance(config, dict)
            or set(config) != {"baseline_files", "candidate_files"}
            or not isinstance(encoded, dict)
        ):
            return report, 2
        if (
            len(config["baseline_files"]) != 89
            or set(config["candidate_files"]) != {x[1] for x in MODULES}
            or set(encoded) != set(config["candidate_files"])
        ):
            return report, 2
        for name, expected in config["baseline_files"].items():
            if (
                not isinstance(name, str)
                or not name.endswith(".py")
                or ".." in name
                or name.startswith("/")
            ):
                return report, 2
            path = Path("/app") / name
            if (
                not path.resolve().is_relative_to(Path("/app"))
                or path.stat().st_size > 2 * 1024**2
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected
            ):
                report["failure_category"] = "CANDIDATE_BASE_IMAGE_BASELINE_MISMATCH"
                return report, 2
            report["baseline_files_verified"] += 1
        sources = {}
        for name, expected in config["candidate_files"].items():
            body = base64.b64decode(encoded[name], validate=True)
            if len(body) > 1024**2 or hashlib.sha256(body).hexdigest() != expected:
                report["failure_category"] = "CANDIDATE_PAYLOAD_HASH_MISMATCH"
                return report, 2
            compile(body, "<reviewed-candidate>", "exec")
            sources[name] = body
            report["candidate_files_verified"] += 1
        # Remove inherited image environment, never inspect credential values.
        os.environ.clear()
        sys.path.insert(0, "/app")
        import builtins
        import io
        import socket
        import sqlite3

        import sqlalchemy

        real_open, real_io_open, real_os_open = builtins.open, io.open, os.open

        def no_network(*args: Any, **kwargs: Any) -> Any:
            report["network_attempts"] += 1
            raise RuntimeError("CANDIDATE_NETWORK_FORBIDDEN")

        def no_database(*args: Any, **kwargs: Any) -> Any:
            report["database_attempts"] += 1
            raise RuntimeError("CANDIDATE_DATABASE_FORBIDDEN")

        def safe_open(file: Any, mode: Any = "r", *args: Any, **kwargs: Any) -> Any:
            if not isinstance(mode, str) or any(c in mode for c in "wax+"):
                report["filesystem_write_attempts"] += 1
                raise RuntimeError("CANDIDATE_WRITE_FORBIDDEN")
            return real_open(file, mode, *args, **kwargs)

        def safe_io_open(file: Any, mode: Any = "r", *args: Any, **kwargs: Any) -> Any:
            if not isinstance(mode, str) or any(c in mode for c in "wax+"):
                report["filesystem_write_attempts"] += 1
                raise RuntimeError("CANDIDATE_WRITE_FORBIDDEN")
            return real_io_open(file, mode, *args, **kwargs)

        def safe_os_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                report["filesystem_write_attempts"] += 1
                raise RuntimeError("CANDIDATE_WRITE_FORBIDDEN")
            return real_os_open(path, flags, *args, **kwargs)

        socket.create_connection = no_network
        socket.socket.connect = no_network
        socket.socket.connect_ex = no_network
        sqlite3.connect = no_database
        sqlalchemy.create_engine = no_database
        builtins.open, io.open, os.open = safe_open, safe_io_open, safe_os_open
        try:
            loaded = {}
            for module_name, name in MODULES:
                module = types.ModuleType(module_name)
                module.__file__ = "/in-memory-review/" + name
                module.__package__ = module_name.rpartition(".")[0]
                sys.modules[module_name] = module
                exec(compile(sources[name], "<reviewed-candidate>", "exec"), module.__dict__)
                loaded[module_name] = module
            evidence = loaded[MODULES[0][0]]
            transport = loaded[MODULES[1][0]]
            from packages.persistence.demo_order_quota import LocalQuotaStopped

            store = object.__new__(evidence.ResponseEvidenceStore)
            checks = (
                (
                    lambda: transport.EvidenceReadTransport(None, None),
                    "RESPONSE_EVIDENCE_TRANSPORT_DISABLED",
                ),
                (
                    lambda: evidence.EvidenceReadJournal(
                        None, evidence=None, account="DEMO-review"
                    ),
                    "RESPONSE_EVIDENCE_DISABLED",
                ),
                (lambda: store.provision(), "RESPONSE_EVIDENCE_EXPLICIT_PROVISION_REQUIRED"),
                (
                    lambda: store.bind(None, "DEMO-review"),
                    "RESPONSE_EVIDENCE_EXPLICIT_BIND_REQUIRED",
                ),
                (
                    lambda: transport.EvidenceReadTransport(
                        None, None, enable_evidence_reads=True, sender=no_network
                    ),
                    "RESPONSE_EVIDENCE_WRITABLE_PROVIDER_REQUIRED",
                ),
            )
            for call, expected in checks:
                try:
                    call()
                except LocalQuotaStopped as error:
                    if str(error) != expected:
                        raise RuntimeError("CANDIDATE_DEFAULT_DENIAL_MISMATCH") from None
                else:
                    raise RuntimeError("CANDIDATE_DEFAULT_DENIAL_MISMATCH")
                report["default_denials_verified"] += 1
        finally:
            builtins.open, io.open, os.open = real_open, real_io_open, real_os_open
        if any(
            report[name] != 0
            for name in ("network_attempts", "database_attempts", "filesystem_write_attempts")
        ):
            return report, 2
        report.update(
            candidate_review="BASE_IMAGE_IMPORTS_AND_DEFAULT_DENIALS_VERIFIED",
            failure_category="NONE",
        )
        return report, 0
    except Exception:
        return report, 2


if __name__ == "__main__":
    result, code = collect(globals().get("CONFIG"), globals().get("SOURCES"))
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(code)
