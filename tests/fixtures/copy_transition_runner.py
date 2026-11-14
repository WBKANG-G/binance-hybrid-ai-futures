"""Realistic closed account and audited runtime, created ONLY in fresh Docker tests."""

import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path

from packages.domain.contracts import OrderState, PositionState, RiskState
from services.execution.demo_models import DemoLedger
from services.execution.models import PaperOrder
from services.risk.paper import AccountValue
from tests.integration.test_demo import settings
from tests.integration.test_paper import signal, snapshot
from tests.unit.test_cold_backup_executor import PWSH
from tests.unit.test_copy_transition_bundle import materialize


def seed(api, docker):
    ledger = DemoLedger(
        policy=settings().policy,
        value=AccountValue(as_of="2026-11-13T00:00:00Z", initial="5000"),
        daily_base="4998.74183608",
        weekly_base="4998.74183608",
        high_water="5000",
        daily_key="2026-11-13",
        weekly_key="2026-11-09",
        paused=True,
        risk_state=RiskState.NORMAL,
        requests={"a": "UNKNOWN", "b": "UNKNOWN", "c": "UNKNOWN", "d": "SUBMITTING"},
    )
    for i in range(34):
        sig = signal(environment="DEMO")
        ledger.orders[str(i)] = PaperOrder(
            client_id="synthetic-" + str(i),
            signal=sig,
            snapshot=snapshot(),
            group="CRYPTO",
            quantity="0.0007",
            quantity_step="0.0001",
            price_tick="0.1",
            loss_per_unit="10",
            budget="1",
            remaining="0",
            filled="0.0007",
            state=OrderState.FILLED,
            position_state=PositionState.CLOSED,
        )
    encoded = base64.b64encode(ledger.model_dump_json().encode()).decode()
    code = """
import sys,os,json,base64
sys.path.insert(0,'/app')
from pathlib import Path
from datetime import datetime,UTC
from uuid import uuid4
from sqlalchemy import create_engine,text
from packages.persistence.demo import DemoStore,metadata
from packages.persistence.demo_shared_checkpoint import SharedWeightAnchorStore
from packages.persistence.demo_shared_weight import POLICY
from packages.persistence.demo_read_audit import ReadAuditStore
from services.execution.demo_models import DemoLedger
from services.execution.demo_runtime_storage import RUNTIME_STORAGE_KEY
engine=create_engine(os.environ['DATABASE_URL'])
with engine.begin() as c:
 c.execute(text('DROP TABLE read_response_evidence_events_v2'))
 c.execute(text('DROP TABLE demo_accounts'))
 c.execute(text("CREATE TABLE unprefixed_copy_fixture (id int PRIMARY KEY, body text)"))
 c.execute(text("INSERT INTO unprefixed_copy_fixture VALUES (1, 'SYNTHETIC_PRIVATE_EVIDENCE')"))
metadata.create_all(engine)
anchors=SharedWeightAnchorStore(engine); anchors.provision()
folder=Path('/app/demo-export-state/demo-runtime-20261111/shared-weight'); folder.mkdir(parents=True,mode=0o700)
journal=uuid4(); scope='DEMO-COPY-TEST'
w=anchors.bootstrap(folder,scope,journal,monotonic=lambda:0.0,wall_clock=lambda:datetime(2026,11,13,tzinfo=UTC),boot=lambda:'fixture-old-boot')
audit=ReadAuditStore(engine); audit.provision(apply=True); audit.bind_empty(w,apply=True)
ledger=DemoLedger.model_validate_json(base64.b64decode(ENCODED))
ledger.operations[RUNTIME_STORAGE_KEY]=json.dumps(dict(version=1,scope=scope,journal=str(journal),checkpoint_installation=str(anchors.installation_id()),policy=POLICY))
DemoStore(engine).create('DEMO-WINDOWS-ISOLATED-BTC-V1',ledger)
engine.dispose()
"""
    docker(
        "exec", "-i", api, "python", "-I", "-B", "-", stdin="ENCODED=" + repr(encoded) + "\n" + code
    )


def exercise(root, backup, restore_report, pins, containers, volumes):
    target = root / "fresh copy review"
    materialize(target)
    config = json.loads((target / "CONFIG.json").read_text())
    for k in (
        "api_container_id",
        "database_container_id",
        "preservation_source_reference",
        "restore_source_reference",
        "receipt_sha256",
        "manifest_sha256",
        "restore_receipt_sha256",
    ):
        config[k] = pins[k]
    (target / "CONFIG.json").write_text(json.dumps(config))
    output = root / "fresh copy output"
    safe = str(backup).replace("'", "''")
    proof = str(restore_report).replace("'", "''")
    dest = str(output).replace("'", "''")
    ext = target / "COPY_EXT.ps1"
    ext.write_text(
        ext.read_text()
        + f"""
# TEST ONLY: input discovery and Windows NTFS ACL substituted on Linux.
function Find-RetainedBackup {{return '{safe}'}}
function Find-RestoreEvidence {{return '{proof}/ISOLATED_RESTORE_RECEIPT.json'}}
function Assert-PrivateBackupDirectory {{}}
function Assert-PrivateBackupFiles {{}}
function New-PrivateBackupDirectory {{ $null=[IO.Directory]::CreateDirectory('{dest}'); return '{dest}' }}
"""
    )
    manifest = json.loads((target / "TRANSFER_MANIFEST.json").read_text())
    manifest["files"] = {
        p: hashlib.sha256((target / p).read_bytes()).hexdigest() for p in manifest["files"]
    }
    body = json.dumps(manifest, sort_keys=True).encode()
    (target / "TRANSFER_MANIFEST.json").write_bytes(body)
    script = target / "native-test.ps1"
    script.write_text(
        f"$PSNativeCommandArgumentPassing='Legacy'\n& '{target / 'RUN_COPY.ps1'}' -ManifestSHA256 '{hashlib.sha256(body).hexdigest()}' -ExecuteReview\nexit $LASTEXITCODE\n"
    )
    check_script = target / "native-check.ps1"
    check_script.write_text(script.read_text().replace("-ExecuteReview", "-CheckOnly"))

    def resource_ids():
        return tuple(
            subprocess.check_output(["docker", *args], text=True)
            for args in (
                ["ps", "--all", "--quiet", "--no-trunc"],
                ["volume", "ls", "--format", "{{.Name}}"],
            )
        )

    resource_before = resource_ids()
    check = subprocess.run(
        [str(PWSH), "-NoProfile", "-NonInteractive", "-File", str(check_script)],
        text=True,
        capture_output=True,
        timeout=90,
        env=dict(
            os.environ,
            XDG_CACHE_HOME=str(root / "cache"),
            XDG_CONFIG_HOME=str(root / "config"),
            XDG_DATA_HOME=str(root / "data"),
        ),
    )
    assert check.returncode == 0 and not check.stderr, check.stdout + check.stderr
    assert "RESTORE_CHECK_PASSED_NO_CLONE_NO_SOURCE_START_NO_ORDERS" in check.stdout
    assert resource_ids() == resource_before and not output.exists()
    result = subprocess.run(
        [str(PWSH), "-NoProfile", "-NonInteractive", "-File", str(script)],
        text=True,
        capture_output=True,
        timeout=240,
        env=dict(
            os.environ,
            XDG_CACHE_HOME=str(root / "cache"),
            XDG_CONFIG_HOME=str(root / "config"),
            XDG_DATA_HOME=str(root / "data"),
        ),
    )
    for name in (
        "RESTORE_RESOURCES.json",
        "CLONE_CREATED.json",
        "FAILURE.json",
        "COPY_DIAGNOSTIC_RESOURCE.json",
        "COPY_REVIEW_RECEIPT.json",
    ):
        path = output / name
        if path.exists():
            value = json.loads(path.read_text())
            containers.extend(value.get("owned_helper_ids", value.get("retained_helper_ids", [])))
            if value.get("clone_container_id"):
                containers.append(value["clone_container_id"])
            if value.get("helper_id"):
                containers.append(value["helper_id"])
            volumes.extend(value.get("clone_volumes", value.get("retained_clone_volumes", [])))
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert "COPY_REVIEW_COMPLETE_ORIGINALS_UNCHANGED_NEW_COPY_STOPPED_NO_ORDERS" in result.stdout
    receipt = json.loads((output / "COPY_REVIEW_RECEIPT.json").read_text())
    diagnostic = receipt["copy_transition_diagnostic"]
    assert (
        diagnostic["historical_unresolved_requests"] == 4
        and not diagnostic["clock_matches"]
        and not diagnostic["period_keys_match"]
    )
    assert (
        receipt["clone_stopped"]
        and receipt["clone_clean_exit_verified"]
        and receipt["input_hashes_reverified"]
    )
    assert (
        not diagnostic["clock_rebased"]
        and not diagnostic["period_baselines_changed"]
        and not diagnostic["installation_ready"]
    )
    Path(os.environ["COPY_REVIEW_DOCKER_REPORT"]).write_text(
        json.dumps(
            dict(
                real_docker_lifecycle=True,
                read_only_preflight_no_resources=True,
                real_unix_socket_readonly_diagnostic=True,
                windows_acl_and_input_discovery_mocked=True,
                actual_windows_touched=False,
                diagnostic=diagnostic,
            ),
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )

    if os.getenv("COPY_CLOCK_DOCKER_REPORT"):
        from tests.fixtures.copy_clock_runner import exercise as exercise_clock

        exercise_clock(root, backup, restore_report, pins, output, containers, volumes)
