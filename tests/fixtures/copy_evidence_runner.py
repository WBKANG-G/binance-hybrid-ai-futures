"""TEST ONLY: extend a fresh-copy clock drill with the synthetic evidence pipeline.

This changes only the materialized cloud test package, never a Windows release.
"""

import hashlib
import json
from pathlib import Path

from tests.unit.test_cold_backup_executor import ROOT
from tests.unit.test_copy_account_content import account_body

EXTRAS = (
    (
        "services.execution.demo_response_evidence_transport",
        "services/execution/demo_response_evidence_transport.py",
    ),
    (
        "services.execution.demo_copy_baseline_evidence",
        "services/execution/demo_copy_baseline_evidence.py",
    ),
    (
        "services.execution.demo_copy_response_archive",
        "services/execution/demo_copy_response_archive.py",
    ),
    (
        "services.execution.demo_copy_account_content",
        "services/execution/demo_copy_account_content.py",
    ),
    (
        "services.execution.demo_copy_evidence_rehearsal",
        "services/execution/demo_copy_evidence_rehearsal.py",
    ),
)


def extend_package(target):
    config = json.loads((target / "CONFIG.json").read_text())
    for _name, path in EXTRAS:
        body = (ROOT / path).read_bytes()
        dest = target / "payload" / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        config["candidate_files"][path] = hashlib.sha256(body).hexdigest()
    config["worker_report_keys"].append("synthetic_evidence_rehearsal")
    (target / "CONFIG.json").write_text(json.dumps(config))
    path = target / "copy_worker.py"
    worker = path.read_text()
    marker = "\ndef collect("
    assert worker.count(marker) == 1
    worker = worker.replace(marker, "\nMODULES = MODULES + " + repr(EXTRAS) + "\n" + marker)
    worker = worker.replace("from uuid import UUID", "from uuid import UUID, uuid4")
    marker = "            return report | dict("
    assert worker.count(marker) == 1
    snippet = """            from services.execution.demo_copy_evidence_rehearsal import rehearse_synthetic_copy_evidence
            pipeline = rehearse_synthetic_copy_evidence(
                engine, writer, "DEMO-WINDOWS-ISOLATED-BTC-V1", found[0], scope,
                UUID(binding["journal"]), UUID(binding["checkpoint_installation"]),
                report["after_snapshot_sha256"], found[0]/("synthetic-response-"+uuid4().hex),
                SYNTHETIC_BODY, copy_only=True, synthetic_only=True,
            )
""".replace("SYNTHETIC_BODY", repr(json.dumps(account_body()).encode()))
    worker = worker.replace(
        marker, snippet + marker + "\n                synthetic_evidence_rehearsal=pipeline,"
    )
    assert worker.count("candidate_files_verified=5") == 1
    worker = worker.replace("candidate_files_verified=5", "candidate_files_verified=10")
    path.write_text(worker)
    launcher = target / "RUN_COPY.ps1"
    text = launcher.read_text()
    marker = "'payload/services/execution/demo_copy_clock_rehearsal.py','CLOCK_EXT.ps1')"
    assert text.count(marker) == 1
    additions = ",".join("'payload/" + path + "'" for _, path in EXTRAS)
    launcher.write_text(
        text.replace(
            marker,
            "'payload/services/execution/demo_copy_clock_rehearsal.py','CLOCK_EXT.ps1',"
            + additions
            + ")",
        )
    )
    path = target / "CLOCK_EXT.ps1"
    text = path.read_text()
    assert text.count("$value.candidate_files_verified -ne 5") == 1
    path.write_text(
        text.replace(
            "$value.candidate_files_verified -ne 5", "$value.candidate_files_verified -ne 10"
        )
    )
    manifest = json.loads((target / "TRANSFER_MANIFEST.json").read_text())
    for _, path in EXTRAS:
        manifest["files"]["payload/" + path] = hashlib.sha256(
            (target / "payload" / path).read_bytes()
        ).hexdigest()
    manifest["files"] = {
        p: hashlib.sha256((target / p).read_bytes()).hexdigest() for p in manifest["files"]
    }
    (target / "TRANSFER_MANIFEST.json").write_text(json.dumps(manifest))


def record_result(target, receipt):
    value = receipt["copy_clock_result"]["synthetic_evidence_rehearsal"]
    assert (
        value["synthetic_copy_evidence_rehearsal"]
        == "PIPELINE_VERIFIED_SYNTHETIC_ONLY_KEEP_STOPPED"
    )
    assert value["synthetic_sender_calls"] == 1 and value["exchange_http_attempts"] == 0
    assert value["audit_events_after"] == value["audit_events_before"] + 2
    assert value["content_review"]["positive_usdt_wallet_observed"]
    assert not value["content_review"]["baseline_adoption_approved"]
    assert (
        receipt["clone_stopped"]
        and receipt["clone_clean_exit_verified"]
        and receipt["input_hashes_reverified"]
    )
    Path(target).write_text(
        json.dumps(
            dict(
                scope="SYNTHETIC_FRESH_DOCKER_COPY_CLOCK_AUXILIARY_ARCHIVE_CONTENT",
                actual_windows_touched=False,
                real_docker_lifecycle=True,
                real_70_second_hold=True,
                network_mode="none",
                originals_and_previous_copies_not_started=True,
                windows_acl_and_input_discovery_mocked=True,
                copy_stopped_cleanly=True,
                input_hashes_reverified=True,
                result=value,
            ),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
