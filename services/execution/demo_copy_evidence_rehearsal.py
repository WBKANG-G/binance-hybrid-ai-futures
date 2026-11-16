"""Synthetic-only evidence pipeline on a clock-rehearsed disposable copy.

No injectable network sender, exchange credentials, production install or adoption.
The external lifecycle boundary must attest copy isolation; failures retain state.
"""

import hashlib
import hmac
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.engine import Engine

from integrations.binance_usdm.demo import DemoSettings
from integrations.binance_usdm.rest import RESTResponse, prepare_signed_query
from packages.persistence.demo import accounts
from packages.persistence.demo_read_audit import ReadAuditStore, read_events, require
from packages.persistence.demo_response_evidence import (
    EvidenceReadJournal,
    ResponseEvidenceStore,
    events,
)
from packages.persistence.demo_shared_checkpoint import SharedWeightAnchorStore, checkpoints
from services.execution.demo_copy_account_content import (
    review_account_body,
    review_account_evidence_inputs,
)
from services.execution.demo_copy_baseline_evidence import (
    ALLOWED_LOCAL_BLOCKERS,
    prepare_copy_baseline_evidence,
)
from services.execution.demo_copy_clock_rehearsal import _all_auxiliary
from services.execution.demo_copy_response_archive import CopyAccountArchiveTransport
from services.execution.demo_copy_transition_review import review_copy_transition
from services.execution.demo_evidence_snapshot import evidence_snapshot
from services.execution.demo_runtime_storage import RuntimeClocks

SYNTHETIC_PRIVATE_KEY = "SYNTHETIC_TMPFS_DOCKER_KEY"
SYNTHETIC_PRIVATE_SECRET = "SYNTHETIC_TMPFS_DOCKER_SECRET"


def rehearse_synthetic_copy_evidence(
    read_engine: Engine,
    write_engine: Engine,
    account: str,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    expected_snapshot_sha256: str,
    archive_directory: Path,
    synthetic_body: bytes,
    *,
    copy_only: bool = False,
    synthetic_only: bool = False,
    clocks: RuntimeClocks | None = None,
    progress: Callable[[str], None] | None = None,
    private_lease: Any = None,
) -> dict[str, Any]:
    require(copy_only is True and synthetic_only is True, "COPY_PIPELINE_SYNTHETIC_COPY_REQUIRED")
    require(
        read_engine is not write_engine and read_engine.url == write_engine.url,
        "COPY_PIPELINE_SAME_COPY_DATABASE_REQUIRED",
    )
    require(
        archive_directory.parent == directory
        and re.fullmatch(r"synthetic-response-[0-9a-f]{32}", archive_directory.name) is not None,
        "COPY_PIPELINE_PRIVATE_ARCHIVE_SCOPE_REQUIRED",
    )
    require(
        not archive_directory.exists() and not archive_directory.is_symlink(),
        "COPY_PIPELINE_NO_RESUME",
    )
    review_account_body(synthetic_body)  # Reject invalid local fixture before any mutation.
    if private_lease is not None:
        # Lazy import keeps the sealed default synthetic pipeline independent of
        # this later tmpfs-only candidate. No arbitrary sender is accepted.
        from services.execution.demo_copy_volatile_credentials import PrivateReadCredentialLease

        require(
            type(private_lease) is PrivateReadCredentialLease,
            "COPY_PIPELINE_PRIVATE_LEASE_REQUIRED",
        )
        private_lease.verify()
        credentials = private_lease._credentials
        require(
            credentials is not None
            and (private_lease._account, private_lease._scope) == (account, scope),
            "COPY_PIPELINE_PRIVATE_SCOPE_MISMATCH",
        )
        assert credentials is not None
        require(
            hmac.compare_digest(credentials.api_key.get_secret_value(), SYNTHETIC_PRIVATE_KEY)
            and hmac.compare_digest(
                credentials.secret.get_secret_value(), SYNTHETIC_PRIVATE_SECRET
            ),
            "COPY_PIPELINE_SYNTHETIC_PRIVATE_KEYS_REQUIRED",
        )
    clock = clocks or RuntimeClocks()

    def stage(name: str) -> None:
        if progress is not None:
            progress(name)

    stage("VERIFY_REHEARSED_COPY")
    before_report = review_copy_transition(
        read_engine,
        account,
        directory,
        scope,
        journal,
        installation,
        copy_only_review=True,
        clocks=clock,
    )
    require(
        before_report["snapshot_sha256"] == expected_snapshot_sha256, "COPY_PIPELINE_INPUT_CHANGED"
    )
    require(not before_report["v2_bound"], "COPY_PIPELINE_ALREADY_BOUND_NO_RETRY")
    require(
        before_report["clock_matches"] and set(before_report["blockers"]) <= ALLOWED_LOCAL_BLOCKERS,
        "COPY_PIPELINE_CLOCK_HOLD_OR_LOCAL_STATE_INVALID",
    )
    before = evidence_snapshot(read_engine, directory)
    require(before["sha256"] == expected_snapshot_sha256, "COPY_PIPELINE_INPUT_CHANGED")
    with write_engine.connect() as control, control.begin():
        control.execute(text("SET LOCAL lock_timeout='2s'"))
        control.execute(text("SET LOCAL statement_timeout='2s'"))
        account_state = control.execute(
            select(accounts.c.state)
            .where(accounts.c.account_id == account)
            .with_for_update(read=True)
        ).scalar_one()
        stage("CONFIGURE_COPY_AUXILIARY_ONLY")
        setup = prepare_copy_baseline_evidence(
            write_engine,
            account,
            directory,
            scope,
            journal,
            installation,
            expected_snapshot_sha256,
            copy_only=True,
            apply=True,
            readers_stopped=True,
            clocks=clock,
        )
        require(setup["evidence_binding_created"], "COPY_PIPELINE_NEW_BINDING_REQUIRED")
        configured = evidence_snapshot(read_engine, directory)
        auxiliary = _all_auxiliary(read_engine)
        anchors, audit, evidence = (
            SharedWeightAnchorStore(write_engine),
            ReadAuditStore(write_engine),
            ResponseEvidenceStore(write_engine),
        )
        shared = EvidenceReadJournal(
            directory,
            scope,
            journal,
            anchors=anchors,
            audit=audit,
            evidence=evidence,
            account=account,
            enable_evidence_binding=True,
            read_only=False,
            monotonic=clock.monotonic,
            wall_clock=clock.wall_clock,
            boot=clock.boot,
        )
        sent = 0

        def synthetic_sender(*args: Any) -> RESTResponse:
            nonlocal sent
            sent += 1
            require(sent == 1, "COPY_PIPELINE_SYNTHETIC_SEND_LIMIT")
            return RESTResponse(200, synthetic_body, {"x-mbx-used-weight-1m": "100"})

        settings = DemoSettings(
            policy=account_state["policy"],
            mode="DEMO",
            demo_trading_enabled=False,
            live_trading_enabled=False,
            margin_mode="ISOLATED",
            symbols=("BTCUSDT",),
        )
        stage("SYNTHETIC_GET_AND_ARCHIVE")
        if private_lease is None:
            transport = CopyAccountArchiveTransport(
                settings,
                shared,
                archive_directory,
                copy_only=True,
                enable_capture=True,
                credential_context_id=str(uuid4()),
                expected_api_key=SecretStr("SYNTHETIC_NO_EXCHANGE_KEY"),
                sender=synthetic_sender,
                monotonic=clock.monotonic,
            )
            signed = prepare_signed_query(
                {},
                api_key=SecretStr("SYNTHETIC_NO_EXCHANGE_KEY"),
                secret=SecretStr("SYNTHETIC_NO_EXCHANGE_SECRET"),
                timestamp_ms=int(clock.wall_clock().timestamp() * 1000),
            )
            try:
                transport("GET", "/fapi/v3/account", signed.query, signed.api_key)
            finally:
                transport.close()
            require(
                sent == 1 and transport.archive_report is not None, "COPY_PIPELINE_ARCHIVE_REQUIRED"
            )
            assert transport.archive_report is not None
            archive_report = transport.archive_report
        else:
            from services.execution.demo_copy_volatile_credentials import (
                capture_with_private_copy_credentials,
            )

            private_result = capture_with_private_copy_credentials(
                settings,
                shared,
                archive_directory,
                private_lease,
                enable_capture=True,
                copy_only=True,
                sender=synthetic_sender,
            )
            archive_report = private_result["read_context"]["archive"]
            require(sent == 1, "COPY_PIPELINE_SYNTHETIC_SEND_LIMIT")
        stage("READ_ONLY_CONTENT_AND_PRESERVATION_CHECK")
        content = review_account_evidence_inputs(
            shared,
            archive_directory,
            archive_report["receipt_sha256"],
            now=clock.wall_clock(),
        )
        after = evidence_snapshot(read_engine, directory)
        require(
            after["catalog"] == configured["catalog"]
            and set(after["tables"]) == set(configured["tables"]),
            "COPY_PIPELINE_SCHEMA_CHANGED",
        )
        for name, value in configured["tables"].items():
            if name not in {read_events.name, checkpoints.name, events.name}:
                require(after["tables"][name] == value, "COPY_PIPELINE_PROTECTED_ROWS_CHANGED")
        after_aux = _all_auxiliary(read_engine)
        for name in (read_events.name, checkpoints.name):
            old, new = auxiliary[name], after_aux[name]
            require(
                [r for r in new if r not in old]
                == [r for r in new if r["scope"] == scope and r not in old]
                and len(new) == len(old) + 2
                and all(r in new for r in old),
                "COPY_PIPELINE_AUXILIARY_DELTA_INVALID",
            )
        require(
            after["tables"][events.name]["rows"] == configured["tables"][events.name]["rows"] + 2,
            "COPY_PIPELINE_RESPONSE_DELTA_INVALID",
        )
        sqlite_name = hashlib.sha256(scope.encode()).hexdigest() + ".weight.sqlite3"
        witness = evidence.witness(directory, scope).name
        additions = {
            archive_directory.name + "/" + name
            for name in ("intent.json", "response.bin", "receipt.json")
        }
        require(
            set(after["files"]) == set(configured["files"]) | additions
            and all(
                after["files"][p] == h
                for p, h in configured["files"].items()
                if p not in {sqlite_name, witness}
            ),
            "COPY_PIPELINE_PROTECTED_FILES_CHANGED",
        )
        final = review_copy_transition(
            read_engine,
            account,
            directory,
            scope,
            journal,
            installation,
            copy_only_review=True,
            clocks=clock,
        )
        require(
            final["snapshot_sha256"] == after["sha256"]
            and final["clock_matches"]
            and final["v2_bound"]
            and set(final["blockers"]) <= ALLOWED_LOCAL_BLOCKERS
            and final["historical_unresolved_requests"]
            == before_report["historical_unresolved_requests"]
            and (final["current_day_kst"], final["current_week_kst"])
            == (before_report["current_day_kst"], before_report["current_week_kst"]),
            "COPY_PIPELINE_FINAL_CONTEXT_CHANGED",
        )
    return dict(
        synthetic_copy_evidence_rehearsal="PIPELINE_VERIFIED_SYNTHETIC_ONLY_KEEP_STOPPED",
        before_snapshot_sha256=before["sha256"],
        after_snapshot_sha256=after["sha256"],
        startup_ready_observed=True,
        evidence_binding_created=True,
        synthetic_sender_calls=sent,
        private_tmpfs_credentials_used=private_lease is not None,
        audit_events_before=before_report["prior_audit_events"],
        audit_events_after=final["prior_audit_events"],
        archive_receipt_sha256=archive_report["receipt_sha256"],
        content_review=content,
        protected_rows_and_files_preserved=True,
        shared_journal_changed=True,
        resource_checkpoint_changed=True,
        copy_isolation_independently_verified=False,
        exchange_credentials_accessed=False,
        exchange_http_attempts=0,
        order_writes=0,
        financial_ledger_changed=False,
        period_baselines_changed=False,
        request_states_changed=False,
        automatic_retry=False,
        installation_ready=False,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
    )
