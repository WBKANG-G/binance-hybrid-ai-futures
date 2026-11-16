"""Native v2 transfer and prior stopped-resource metadata guards."""

import hashlib
import json
import os
import re
import zipfile
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from scripts import build_windows_private_week_v2_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_private_week_bundle import native


def package():
    release = os.getenv("PRIVATE_WEEK_V2_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git
    rev = real("rev-parse", "HEAD").decode().strip()

    def source(*args):
        if args[0] == "show" and args[1].startswith(rev + ":"):
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with ExitStack() as stack:
        for name in (
            "private_week_v2",
            "private_week",
            "copy_evidence",
            "copy_clock",
            "copy_transition",
            "response_candidate_review",
        ):
            stack.enter_context(patch("scripts.build_windows_" + name + "_bundle.git", source))
        return builder.package_files("HEAD")


def materialize(root):
    root.mkdir(parents=True, exist_ok=True)
    for p, b in package().items():
        target = root / p
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b)


def test_v2_sealed_payload_and_prior_ids():
    files = package()
    cfg = json.loads(files["CONFIG.json"])
    manifest = json.loads(files["TRANSFER_MANIFEST.json"])["files"]
    required = re.search(r"\$required=@\(([^\n]+)\)", files["RUN_COPY.ps1"].decode()).group(1)
    assert set(re.findall(r"'([^']+)'", required)) == set(manifest)
    assert len(cfg["candidate_files"]) == 16 and len(cfg["baseline_files"]) == 89
    assert cfg["prior_private_helper_ids"] == list(builder.PRIOR_PRIVATE_IDS)
    for p, h in manifest.items():
        assert hashlib.sha256(files[p]).hexdigest() == h
    for p, h in cfg["candidate_files"].items():
        assert hashlib.sha256(files["payload/" + p]).hexdigest() == h
    compile(files["private_worker.py"], "<v2-worker>", "exec")
    assert b"SyntheticPrivatePreparation" in files["private_worker.py"]
    assert b"COPY_PRIVATE_WEEK_V2_RECEIPT.json" in files["COPY_CORE.ps1"]


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_native_scripts_parse_and_tamper_denied(tmp_path):
    materialize(tmp_path)
    r = native(
        tmp_path,
        f"""
foreach ($file in Get-ChildItem '{tmp_path}' -Filter '*.ps1') {{
 $tokens=$null;$errors=$null;$null=[Management.Automation.Language.Parser]::ParseFile($file.FullName,[ref]$tokens,[ref]$errors)
 if ($errors.Count -gt 0) {{throw ($errors|ConvertTo-Json -Depth 5)}}
}}
""",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    pin = hashlib.sha256((tmp_path / "TRANSFER_MANIFEST.json").read_bytes()).hexdigest()
    (tmp_path / "PRIVATE_EXT.ps1").write_text("SENTINEL")
    r = native(
        tmp_path,
        f"& '{tmp_path}/RUN_COPY.ps1' -ManifestSHA256 '{pin}' -ExecuteReview\nexit $LASTEXITCODE",
    )
    assert r.returncode == 2 and not r.stderr and "SENTINEL" not in r.stdout
    assert not json.loads(r.stdout)["source_state_may_have_changed"]


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "fault",
    [
        "valid",
        "running",
        "restart",
        "project",
        "image",
        "key_rw",
        "source_volume",
        "alias",
        "missing",
        "oom",
    ],
)
def test_prior_resources_must_stay_stopped_and_match_copy_mounts(tmp_path, fault):
    materialize(tmp_path)
    ids = list(builder.PRIOR_PRIVATE_IDS)
    images = ["sha256:" + "a" * 64, "sha256:" + "b" * 64]
    db = "demo-copy-review-database-" + "1" * 32
    runtime = "demo-copy-review-runtime-" + "1" * 32
    socket = "demo-copy-review-socket-" + "1" * 32
    keys = "demo-read-private-keys-" + "2" * 32
    specs = [
        [
            ("/bundle", "bind", "", False),
            ("/clone/database", "volume", db, True),
            ("/clone/runtime", "volume", runtime, True),
            ("/review-socket", "volume", socket, True),
        ],
        [("/var/lib/postgresql", "volume", db, True), ("/review-socket", "volume", socket, True)],
        [("/run/demo-keys", "volume", keys, False)],
        [("/run/demo-keys", "volume", keys, True)],
        [
            ("/run/demo-keys", "volume", keys, False),
            ("/review-runtime", "volume", runtime, True),
            ("/review-socket", "volume", socket, False),
        ],
    ]
    roles = ["archive-restore", "copy-db", "key-holder", "registrar", "read-helper"]
    objects = {
        id: dict(
            Id=id,
            Image=images[i == 1],
            Config=dict(
                User="10001:10001",
                Labels={
                    "com.docker.compose.project": "demo-copy-review"
                    if i < 2
                    else "demo-read-private",
                    "com.docker.compose.service": roles[i],
                },
            ),
            State=dict(
                Running=False,
                Restarting=False,
                Paused=False,
                Dead=False,
                OOMKilled=False,
                Status="exited",
            ),
            HostConfig=dict(
                RestartPolicy=dict(Name="no", MaximumRetryCount=0),
                NetworkMode="none",
                ReadonlyRootfs=True,
                LogConfig=dict(Type="none"),
            ),
            Mounts=[dict(Destination=p, Type=t, Name=n, RW=rw) for p, t, n, rw in specs[i]],
        )
        for i, id in enumerate(ids)
    }
    reader = objects[ids[-1]]
    if fault == "running":
        reader["State"]["Running"] = True
    if fault == "restart":
        reader["HostConfig"]["RestartPolicy"]["Name"] = "always"
    if fault == "project":
        reader["Config"]["Labels"]["com.docker.compose.project"] = "hybrid-windows"
    if fault == "image":
        reader["Image"] = images[1]
    if fault == "key_rw":
        reader["Mounts"][0]["RW"] = True
    if fault == "source_volume":
        reader["Mounts"][1]["Name"] = "original-runtime"
    if fault == "alias":
        reader["Mounts"][2]["Name"] = runtime
    if fault == "missing":
        reader["Mounts"].pop()
    if fault == "oom":
        reader["State"]["OOMKilled"] = True
    (tmp_path / "objects.json").write_text(json.dumps(objects))
    (tmp_path / "ids.json").write_text(
        json.dumps(
            dict(
                prior_private_helper_ids=ids,
                api_container_id="c" * 64,
                database_container_id="d" * 64,
            )
        )
    )
    r = native(
        tmp_path,
        f"""
. '{tmp_path}/PRIVATE_EXT.ps1'
$script:objects=Get-Content -Raw '{tmp_path}/objects.json'|ConvertFrom-Json
function Private-ReadInspect([string]$Id) {{return $script:objects.$Id}}
function Resolve-RestoreImageMatch([string]$A,[string]$B) {{return $A -ceq $B}}
$state=[pscustomobject]@{{canonical_images=@('{images[0]}','{images[1]}');database_volume='original-db';runtime_volume='original-runtime'}}
try {{Assert-PriorPrivateStopped (Get-Content -Raw '{tmp_path}/ids.json'|ConvertFrom-Json) $state;Write-Output 'OK'}} catch {{Write-Output $_.Exception.Message;exit 2}}
""",
    )
    assert r.returncode == (0 if fault == "valid" else 2) and not r.stderr, r.stdout + r.stderr
    assert (r.stdout.strip() == "OK") == (fault == "valid")


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "fault",
    [
        "valid",
        "http",
        "order",
        "short_hold",
        "identity",
        "adoption",
        "scope_identity",
        "stored_week",
        "content_string",
        "early_lease",
        "prep_deadline",
        "prep_missing",
    ],
)
def test_v2_result_requires_late_lease_without_authority(tmp_path, fault):
    materialize(tmp_path)
    cfg = json.loads((tmp_path / "CONFIG.json").read_bytes())
    actual = json.loads(
        (ROOT / "docs/reviews/20261116/PRIVATE_WEEK_DOCKER_VALIDATION.json").read_bytes()
    )
    week = actual["result"]
    pipeline = week["pipeline"]
    pipeline.update(private_lease_opened_at_capture=True, private_preparation_seconds=200)
    count = pipeline["audit_events_before"]
    value = dict.fromkeys(cfg["worker_report_keys"])
    value.update(
        copy_clock_rehearsal="CLOCK_REBASED_HOLD_VERIFIED_KEEP_STOPPED",
        before_snapshot_sha256="a" * 64,
        after_snapshot_sha256="b" * 64,
        protected_database_sha256="c" * 64,
        baseline_files_verified=89,
        candidate_files_verified=16,
        prior_audit_events=count - 1,
        audit_events_after=count,
        prior_revision=0,
        revision_after=1,
        cumulative_weight_before=0,
        cumulative_weight_after=0,
        startup_hold_seconds=70.001,
        exchange_http_attempts=0,
        order_writes=0,
        private_week_review=week,
        synthetic_evidence_rehearsal=pipeline,
        private_scope_policy=actual["private_scope_policy"],
    )
    for flag in (
        "clock_rebased",
        "startup_hold_verified",
        "prior_database_rows_preserved",
        "prior_files_preserved",
        "copy_read_sessions_read_only",
        "copy_write_session_explicit",
        "synthetic_key_file_unchanged",
    ):
        value[flag] = True
    for flag in (
        "financial_ledger_changed",
        "period_baselines_changed",
        "request_states_changed",
        "automatic_retry",
        "installation_ready",
        "execution_approved",
        "orders_enabled",
        "live_trading_enabled",
    ):
        value[flag] = False
    if fault == "http":
        value["exchange_http_attempts"] = 1
    if fault == "order":
        value["order_writes"] = 1
    if fault == "short_hold":
        value["startup_hold_seconds"] = 0
    if fault == "identity":
        week["remote_account_identity_verified"] = True
    if fault == "adoption":
        week["baseline_adoption_approved"] = True
    if fault == "scope_identity":
        value["private_scope_policy"]["remote_account_identity_verified"] = True
    if fault == "stored_week":
        week["stored_week_kst"] = "2026-11-16"
    if fault == "content_string":
        pipeline["content_review"] = "PSCustomObject string"
    if fault == "early_lease":
        pipeline["private_lease_opened_at_capture"] = False
    if fault == "prep_deadline":
        pipeline["private_preparation_seconds"] = 300
    if fault == "prep_missing":
        pipeline["private_preparation_seconds"] = None
    (tmp_path / "value.json").write_text(json.dumps(value))
    r = native(
        tmp_path,
        f"""
. '{tmp_path}/COPY_CORE.ps1'
. '{tmp_path}/PRIVATE_EXT.ps1'
$script:ClockPrior=[pscustomobject]@{{copy_transition_diagnostic=[pscustomobject]@{{snapshot_sha256=('a'*64)}}}}
try {{Assert-PrivateWeekResult (Read-RestoreJson '{tmp_path}/value.json') (Read-RestoreJson '{tmp_path}/CONFIG.json');Write-Output 'OK'}} catch {{Write-Output $_.Exception.Message;exit 2}}
""",
    )
    assert r.returncode == (0 if fault == "valid" else 2) and not r.stderr, r.stdout + r.stderr
    if fault == "valid":
        assert r.stdout.strip() == "OK"
    else:
        assert r.stdout.strip().startswith("PRIVATE_")
