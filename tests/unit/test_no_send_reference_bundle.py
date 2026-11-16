"""No retrospective no-send inference from not-found, flat, or missing records."""

import hashlib
import io
import json
import os
import shutil
import tarfile
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_no_send_reference_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch
from tests.unit.test_private_receipt_review_v2 import snapshot
from tests.unit.test_request_reference_bundle import records as request_records

pytestmark = pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")


def h(body):
    return hashlib.sha256(body).hexdigest()


def package():
    release = os.getenv("NO_SEND_REFERENCE_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git
    ref = real("rev-parse", "HEAD").decode().strip()

    def read(*args):
        if args[0] == "show" and args[1].startswith(ref + ":"):
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with patch.object(builder, "git", read):
        return builder.package_files("HEAD")


def records():
    a, p = request_records()
    p["operation_id"] = "22222222-2222-2222-2222-222222222222"
    a["old_row"]["state"]["request_no_send_evidence"] = {}
    original = dict(
        policy=p["policy"],
        operation_id=p["operation_id"],
        requests_observed=3,
        requests_unqueried=1,
        observations=[],
    )
    exit_report = dict(
        policy="PREVIOUSLY_UNQUERIED_EXIT_EVIDENCE_20261113_V1",
        requests_observed=1,
        requests_unqueried=0,
        observations=[],
    )
    for row in p["plan"]:
        entry = row["role"] == "ENTRY"
        record = dict(
            request_reference_sha256=row["request_reference_sha256"],
            role=row["role"],
            observed_status="FILLED" if entry else "NOT_FOUND_UNRESOLVED",
            response_identity_fields_match=entry,
            remote_resolution_verified=False,
            raw_secret_unknown="NEVER_OUTPUT_SECRET",
        )
        (exit_report if row["role"] == "EXIT" else original)["observations"].append(record)
    return a, p, original, exit_report


def setup(tmp_path, mutate=None, missing=None):
    a, p, o, e = records()
    if mutate:
        mutate(a, p, o, e)
    root = tmp_path / "data" / "WBKANG-DEMO-Backups"
    folder = root / ("cold-20261114-" + "1" * 32)
    folder.mkdir(parents=True)
    archive = folder / "runtime.tar"
    entries = []
    bodies = {
        "demo-runtime-20261111/accounted-day13-adoption-11111111-1111-1111-1111-111111111111/intent.json": a,
        "demo-runtime-20261111/historical-request-evidence-22222222-2222-2222-2222-222222222222/intent.json": p,
        "demo-runtime-20261111/historical-request-evidence-22222222-2222-2222-2222-222222222222/complete.json": o,
        "demo-runtime-20261111/historical-exit-evidence-33333333-3333-3333-3333-333333333333/complete.json": e,
    }
    with tarfile.open(archive, "w") as tar:
        for i, (name, value) in enumerate(bodies.items()):
            if i == missing:
                continue
            body = json.dumps(value).encode()
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
            entries.append(dict(path=name, kind="file", size=len(body)))
    manifest = json.dumps(
        dict(
            groups=dict(
                runtime=dict(
                    entries=entries,
                    archive_bytes=archive.stat().st_size,
                    archive_sha256=h(archive.read_bytes()),
                )
            )
        )
    ).encode()
    (folder / "manifest.json").write_bytes(manifest)
    for name, body in package().items():
        (tmp_path / name).write_bytes(body)
    return root, folder, h(manifest)


def run(tmp_path, root, pin, extra=""):
    r = launch(
        tmp_path,
        f"""
. '{tmp_path}/READ_CORE.ps1'
. '{tmp_path}/NO_SEND_CORE.ps1'
function tar.exe {{& tar @args}}
function docker {{throw 'DOCKER_MUST_NOT_RUN'}}
{extra}
Invoke-NoSendReferenceInspection '{root}' '{pin}'|ConvertTo-Json -Depth 8 -Compress
""",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    return json.loads(r.stdout), r.stdout


def assert_no_resolution(v):
    r = v["review"]
    for flag in [
        "current_database_state_verified",
        "event_transition_history_replayed",
        "wire_send_or_non_send_independently_verified",
        "stored_no_send_reason_independently_verified",
        "not_found_proves_no_submission",
        "current_flat_position_proves_no_submission",
        "request_states_changed",
        "request_resolution_authorized",
        "resend_authorized",
        "remote_resolution_verified",
    ]:
        assert r[flag] is False
    for row in r["requests"]:
        for flag in [
            "actual_submission_or_non_submission_independently_verified",
            "not_found_treated_as_not_sent",
            "remote_resolution_verified",
            "request_resolution_authorized",
            "automatically_resolvable",
        ]:
            assert row[flag] is False


def test_empty_map_and_not_found_never_imply_no_submission_or_resolution(tmp_path):
    root, _, pin = setup(tmp_path)
    before = snapshot(root)
    v, out = run(tmp_path, root, pin)
    assert (
        v["no_send_reference_inspection"]
        == "PINNED_PRIOR_NO_SEND_RECORDS_AND_READ_OBSERVATIONS_REVIEWED"
    ), v
    assert v["request_count"] == 4 and v["review"]["recorded_reason_count_for_four_requests"] == 0
    assert all(
        row["finding"] == "NO_DURABLE_NO_SEND_REASON_IN_THIS_PRIOR_SNAPSHOT"
        for row in v["review"]["requests"]
    )
    assert_no_resolution(v)
    assert [row["stored_query_observed_status"] for row in v["review"]["requests"]].count(
        "NOT_FOUND_UNRESOLVED"
    ) == 2
    assert "PRIVATE_CLIENT_SENTINEL" not in out and "NEVER_OUTPUT_SECRET" not in out
    assert snapshot(root) == before


def test_absent_legacy_map_is_distinct_from_empty_present_map(tmp_path):
    root, _, pin = setup(
        tmp_path, lambda a, p, o, e: a["old_row"]["state"].pop("request_no_send_evidence")
    )
    v, _ = run(tmp_path, root, pin)
    assert v["review"]["no_send_map_present"] is False
    assert all(
        row["finding"] == "NO_SEND_MAP_ABSENT_IN_THIS_PRIOR_SNAPSHOT"
        for row in v["review"]["requests"]
    )
    assert_no_resolution(v)


@pytest.mark.parametrize(
    "reason", ["LOCAL_VALIDATION_FAILED", "POSITION_ALREADY_FLAT", "POSITION_DIRECTION_MISMATCH"]
)
def test_recorded_reason_with_unresolved_state_requires_review_without_reclassification(
    tmp_path, reason
):
    def mutate(a, p, o, e):
        a["old_row"]["state"]["request_no_send_evidence"]["PRIVATE_CLIENT_SENTINEL_3"] = reason

    root, _, pin = setup(tmp_path, mutate)
    before = snapshot(root)
    v, _ = run(tmp_path, root, pin)
    row = next(r for r in v["review"]["requests"] if r["role"] == "EXIT")
    assert (
        row["recorded_request_state"] == "SUBMITTING" and row["recorded_no_send_reason"] == reason
    )
    assert row["recorded_reason_with_unresolved_state_requires_review"] is True
    assert (
        row["finding"]
        == "RECORDED_REASON_PRESENT_BUT_UNRESOLVED_REQUEST_STATE_REQUIRES_SEPARATE_REVIEW"
    )
    assert v["review"]["recorded_reason_count_for_four_requests"] == 1
    assert_no_resolution(v)
    assert snapshot(root) == before


@pytest.mark.parametrize("value", [None, True, 1, {}, "BAD_PRIVATE_REASON"])
def test_invalid_selected_reason_is_redacted_and_denied(tmp_path, value):
    root, _, pin = setup(
        tmp_path,
        lambda a, p, o, e: a["old_row"]["state"]["request_no_send_evidence"].update(
            {"PRIVATE_CLIENT_SENTINEL_2": value}
        ),
    )
    v, out = run(tmp_path, root, pin)
    assert v["failure_category"] == "NO_SEND_REASON_INVALID"
    assert "BAD_PRIVATE_REASON" not in out and "review" not in v


@pytest.mark.parametrize(
    "change",
    [
        "plan_hash",
        "report_hash",
        "report_role",
        "policy",
        "count",
        "identity",
        "authority",
        "operation",
        "invalid_map",
    ],
)
def test_invalid_links_do_not_produce_partial_or_resolvable_results(tmp_path, change):
    def mutate(a, p, o, e):
        if change == "plan_hash":
            p["plan"][0]["request_reference_sha256"] = "0" * 64
        elif change == "report_hash":
            e["observations"][0]["request_reference_sha256"] = "0" * 64
        elif change == "report_role":
            e["observations"][0]["role"] = "STOP_LOSS"
        elif change == "policy":
            o["policy"] = "WRONG_POLICY"
        elif change == "count":
            o["requests_observed"] = 2
        elif change == "identity":
            e["observations"][0]["response_identity_fields_match"] = True
        elif change == "authority":
            e["observations"][0]["remote_resolution_verified"] = True
        elif change == "operation":
            o["operation_id"] = "44444444-4444-4444-4444-444444444444"
        elif change == "invalid_map":
            a["old_row"]["state"]["request_no_send_evidence"] = None

    root, _, pin = setup(tmp_path, mutate)
    before = snapshot(root)
    v, _ = run(tmp_path, root, pin)
    assert v["no_send_reference_inspection"] == "BLOCKED_NO_WRITES" and "review" not in v
    assert snapshot(root) == before


@pytest.mark.parametrize("missing", [0, 1, 2, 3])
def test_incomplete_records_are_not_reconstructed_or_assumed(tmp_path, missing):
    root, _, pin = setup(tmp_path, missing=missing)
    v, _ = run(tmp_path, root, pin)
    assert v["no_send_reference_inspection"] == "BLOCKED_NO_WRITES" and "review" not in v


@pytest.mark.parametrize("which", ["manifest", "archive"])
def test_corrupt_or_duplicate_backups_are_not_used(tmp_path, which):
    root, folder, pin = setup(tmp_path)
    p = folder / ("manifest.json" if which == "manifest" else "runtime.tar")
    p.write_bytes(p.read_bytes() + b" ")
    v, _ = run(tmp_path, root, pin, "function Inspect-ReadArchive {throw 'MUST_NOT_READ'}")
    assert v["no_send_reference_inspection"] == "BLOCKED_NO_WRITES"


def test_duplicate_backup_refused(tmp_path):
    root, folder, pin = setup(tmp_path)
    shutil.copytree(folder, root / ("cold-20261114-" + "2" * 32))
    v, _ = run(tmp_path, root, pin)
    assert v["failure_category"] == "INSPECT_BACKUP_NOT_UNIQUE"


@pytest.mark.parametrize("file", ["READ_CORE.ps1", "NO_SEND_CORE.ps1", "README_KO.md"])
def test_transfer_tamper_refused(tmp_path, file):
    for name, body in package().items():
        (tmp_path / name).write_bytes(body)
    pin = h((tmp_path / "TRANSFER_MANIFEST.json").read_bytes())
    (tmp_path / file).write_text("PRIVATE_SENTINEL_DO_NOT_RUN")
    r = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_NO_SEND_CHECK.ps1' -ManifestSHA256 '{pin}'\nexit $LASTEXITCODE",
    )
    assert r.returncode == 2 and not r.stderr and "PRIVATE_SENTINEL" not in r.stdout
    assert json.loads(r.stdout)["failure_category"] == "INSPECT_TRANSFER_INVALID"


def test_sealed_launcher_success_with_only_fixture_pin_and_tar_name_substituted(tmp_path):
    root, _, pin = setup(tmp_path)
    before = snapshot(root)
    p = tmp_path / "RUN_NO_SEND_CHECK.ps1"
    p.write_text(
        p.read_text().replace(
            "2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d", pin
        )
    )
    manifest = json.loads((tmp_path / "TRANSFER_MANIFEST.json").read_bytes())
    manifest["files"]["RUN_NO_SEND_CHECK.ps1"] = h(p.read_bytes())
    body = json.dumps(manifest).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(body)
    r = launch(
        tmp_path,
        f"function tar.exe {{& tar @args}}\n& '{tmp_path}/RUN_NO_SEND_CHECK.ps1' -ManifestSHA256 '{h(body)}'\nexit $LASTEXITCODE",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    assert "PRIOR_NO_SEND_RECORDS_READ_NO_RESOLUTION_NO_WRITES_NO_DOCKER" in r.stdout
    assert snapshot(root) == before and "PRIVATE_CLIENT_SENTINEL" not in r.stdout


def test_final_source_recheck_failure_returns_no_partial_review(tmp_path):
    root, _, pin = setup(tmp_path)
    before = snapshot(root)
    v, _ = run(
        tmp_path,
        root,
        pin,
        """
$script:hash=${function:Inspect-StreamHash};$script:count=0
function Inspect-StreamHash([IO.Stream]$Stream) {$script:count++;if ($script:count -eq 4) {return ('0'*64)};return (& $script:hash $Stream)}
""",
    )
    assert v["failure_category"] == "INSPECT_SOURCE_CHANGED" and "review" not in v
    assert snapshot(root) == before


def test_reader_core_is_identical_to_successful_windows_release():
    p = (
        ROOT
        / "artifacts/windows-demo-request-reference-20261116/windows-demo-request-reference-20261116.zip"
    )
    with zipfile.ZipFile(p) as z:
        assert package()["READ_CORE.ps1"] == z.read(
            "windows-demo-request-reference-20261116/INSPECT_CORE.ps1"
        )


def test_parse_and_no_network_write_service_or_dynamic_execution_commands(tmp_path):
    for name, body in package().items():
        (tmp_path / name).write_bytes(body)
    r = launch(
        tmp_path,
        f"""
foreach ($file in @('RUN_NO_SEND_CHECK.ps1','NO_SEND_CORE.ps1','READ_CORE.ps1')) {{
 $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path '{tmp_path}' $file),[ref]$tokens,[ref]$errors)
 if ($errors.Count -gt 0) {{throw 'PARSE_ERROR'}}
 foreach ($node in $ast.FindAll({{param($n) $n -is [Management.Automation.Language.CommandAst]}},$true)) {{
  if ($node.GetCommandName() -in @('docker','Start-Process','Invoke-Expression','Remove-Item','Move-Item','Copy-Item','Set-Content','Out-File','Invoke-WebRequest','Invoke-RestMethod')) {{throw 'FORBIDDEN_COMMAND'}}
 }}
}}
Write-Output 'PARSED_NO_WRITES_NETWORK_OR_SERVICES'
""",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    assert "PARSED_NO_WRITES_NETWORK_OR_SERVICES" in r.stdout
