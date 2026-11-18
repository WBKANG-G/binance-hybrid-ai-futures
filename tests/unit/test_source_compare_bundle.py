"""Stopped original read-only tree comparison and helper admission checks."""

import hashlib
import json
import os
import subprocess
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_source_compare_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch
from tests.unit.test_private_receipt_review_v2 import snapshot
from tools.cold_backup_candidate import create_bundle
from tools.windows_demo_source_compare.worker import compare


def package():
    release = os.getenv("SOURCE_COMPARE_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git
    ref = real("rev-parse", "HEAD").decode().strip()

    def read(*args):
        if args[0] == "show" and args[1].startswith(ref + ":tools/windows_demo_source_compare/"):
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with patch.object(builder, "git", read):
        return builder.package_files("HEAD")


def fixture(tmp_path):
    roots = {g: tmp_path / g for g in ("database", "runtime")}
    for root in roots.values():
        root.mkdir()
    (roots["database"] / "18").mkdir()
    (roots["database"] / "18/data").write_bytes(b"PRIVATE_PG_BYTES")
    (roots["runtime"] / "state.json").write_text('{"private":"do not print"}')
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    create_bundle(roots, bundle, enabled=True, sources_frozen_by_caller=True)
    return roots, json.loads((bundle / "manifest.json").read_bytes()), package()["READ_LIBRARY.py"]


def test_matching_two_passes_preserve_bytes_metadata_and_no_raw_output(tmp_path):
    roots, manifest, lib = fixture(tmp_path)
    before = {g: snapshot(p) for g, p in roots.items()}
    result = compare(manifest, roots, lib)
    assert result["status"] == "STOPPED_SOURCE_TREES_MATCH_PINNED_BACKUP", result
    assert result["passes"] == 2 and result["file_counts"] == {"database": 1, "runtime": 1}
    assert not result["source_data_written"] and not result["logical_account_row_queried"]
    assert "PRIVATE" not in json.dumps(result)
    assert before == {g: snapshot(p) for g, p in roots.items()}


@pytest.mark.parametrize(
    "fault",
    [
        "content",
        "missing",
        "extra",
        "mode",
        "mtime",
        "symlink",
        "hardlink",
        "fifo",
        "xattr",
        "manifest_path",
        "manifest_hash",
        "root_scope",
    ],
)
def test_mismatch_and_unsupported_files_fail_closed_without_writes(tmp_path, fault):
    roots, manifest, lib = fixture(tmp_path)
    p = roots["database"] / "18/data"
    if fault == "content":
        stamp = p.stat().st_mtime_ns
        p.write_bytes(b"ALTERED_PG_BYTES")
        os.utime(p, ns=(stamp, stamp))
    elif fault == "missing":
        p.unlink()
    elif fault == "extra":
        (roots["runtime"] / "extra").write_bytes(b"x")
    elif fault == "mode":
        p.chmod(0o400)
    elif fault == "mtime":
        os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns + 1000))
    elif fault == "symlink":
        p.unlink()
        p.symlink_to("/etc/passwd")
    elif fault == "hardlink":
        os.link(p, tmp_path / "link")
    elif fault == "fifo":
        p.unlink()
        os.mkfifo(p)
    elif fault == "xattr":
        os.setxattr(p, "user.test", b"x")
    elif fault == "manifest_path":
        manifest["groups"]["database"]["entries"][0]["path"] = "../outside"
    elif fault == "manifest_hash":
        manifest["groups"]["database"]["entries"][1]["sha256"] = "0" * 64
    elif fault == "root_scope":
        roots["extra"] = tmp_path
    result = compare(manifest, roots, lib)
    assert result["status"] == "BLOCKED", result
    assert not result["source_data_written"]
    assert "PRIVATE" not in json.dumps(result) and "/etc" not in json.dumps(result)


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "fault",
    [
        "valid",
        "writable",
        "wrong-volume",
        "running",
        "network",
        "privileged",
        "writable-root",
        "extra-mount",
        "source-id",
        "caps",
        "command",
    ],
)
def test_helper_must_be_new_stopped_exact_image_and_readonly_isolated(tmp_path, fault):
    files = package()
    for n, b in files.items():
        (tmp_path / n).write_bytes(b)
    ident = "a" * 64
    state = dict(
        canonical_images=["sha256:" + "8" * 64], database_volume="db", runtime_volume="run"
    )
    original = [dict(id="b" * 64)]
    item = dict(
        image=state["canonical_images"][0],
        running=False,
        restarting=False,
        paused=False,
        dead=False,
        restart=dict(Name="no", MaximumRetryCount=0),
        mounts=[
            dict(Type="volume", Name="db", Destination="/source/database", RW=False),
            dict(Type="volume", Name="run", Destination="/source/runtime", RW=False),
        ],
    )
    host = dict(
        NetworkMode="none",
        ReadonlyRootfs=True,
        Privileged=False,
        Memory=536870912,
        PidsLimit=32,
        CapDrop=["ALL"],
        CapAdd=["DAC_OVERRIDE"],
        SecurityOpt=["no-new-privileges"],
        Devices=[],
    )
    command = ["-I", "-B", "-"]
    if fault == "writable":
        item["mounts"][0]["RW"] = True
    elif fault == "wrong-volume":
        item["mounts"][1]["Name"] = "OTHER"
    elif fault == "running":
        item["running"] = True
    elif fault == "network":
        host["NetworkMode"] = "bridge"
    elif fault == "privileged":
        host["Privileged"] = True
    elif fault == "writable-root":
        host["ReadonlyRootfs"] = False
    elif fault == "extra-mount":
        item["mounts"].append(item["mounts"][0])
    elif fault == "source-id":
        original = [dict(id=ident)]
    elif fault == "caps":
        host["CapAdd"].append("SYS_ADMIN")
    elif fault == "command":
        command = ["server.py"]
    for n, v in [
        ("item", item),
        ("host", host),
        ("state", state),
        ("original", original),
        ("command", command),
    ]:
        (tmp_path / (n + ".json")).write_text(json.dumps(v))
    body = f"""
. '{tmp_path}/COMPARE_CORE.ps1'
function Read-Container {{param($Id) return (Get-Content -Raw '{tmp_path}/item.json'|ConvertFrom-Json)}}
function Backup-Docker {{param($Arguments,$Maximum) return @((Get-Content -Raw '{tmp_path}/host.json'),'"0:0"','["python"]',(Get-Content -Raw '{tmp_path}/command.json'))}}
$state=Get-Content -Raw '{tmp_path}/state.json'|ConvertFrom-Json
$original=Get-Content -Raw '{tmp_path}/original.json'|ConvertFrom-Json
try{{Assert-CompareHelper '{ident}' $state $original;'ACCEPTED'}}catch{{'BLOCKED'}}
"""
    value = launch(tmp_path, body)
    assert value.returncode == 0 and not value.stderr, value.stdout + value.stderr
    assert value.stdout.strip() == ("ACCEPTED" if fault == "valid" else "BLOCKED")


def test_package_is_sealed_and_carries_no_source_start_or_write_functions():
    files = package()
    manifest = json.loads(files["TRANSFER_MANIFEST.json"])
    for n, h in manifest["files"].items():
        assert hashlib.sha256(files[n]).hexdigest() == h
    text = b"\n".join(v for n, v in files.items() if n.endswith((".py", ".ps1")))
    for forbidden in [
        b"Config.Env",
        b"--entrypoint','postgres",
        b"def write_group",
        b"def restore_bundle",
        b"function Stop-OwnedClone",
        b"function Request-GracefulSourceStop",
        b"FileAccess]::Write",
        b"volume','create",
    ]:
        assert forbidden not in text
    assert (
        b"readonly,volume-nocopy" in text and b"docker start --attach --interactive $helper" in text
    )


def test_real_docker_readonly_source_mounts_and_worker(tmp_path):
    if subprocess.run(
        ["docker", "version", "--format", "{{.Server.Version}}"], capture_output=True
    ).returncode:
        pytest.skip("Docker unavailable")
    image = "phase4-runtime-code-package:20261111"
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode:
        pytest.skip("Cached test image unavailable")
    roots, manifest, lib = fixture(tmp_path)
    files = package()
    import base64

    payload = "import base64,json\nREAD_LIBRARY=base64.b64decode('{}')\nMANIFEST=json.loads(base64.b64decode('{}'))\nexec(compile(base64.b64decode('{}'),'<worker>','exec'))".format(
        *tuple(
            base64.b64encode(v).decode()
            for v in (lib, json.dumps(manifest).encode(), files["worker.py"])
        )
    )
    before = {g: snapshot(p) for g, p in roots.items()}
    args = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--interactive",
        "--network",
        "none",
        "--restart",
        "no",
        "--read-only",
        "--memory",
        "512m",
        "--pids-limit",
        "32",
        "--cap-drop",
        "ALL",
        "--cap-add",
        "DAC_OVERRIDE",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "0:0",
    ]
    for g, p in roots.items():
        args += ["--mount", f"type=bind,source={p},target=/source/{g},readonly"]
    args += ["--entrypoint", "python", image, "-I", "-B", "-"]
    value = subprocess.run(args, input=payload, text=True, capture_output=True, timeout=30)
    assert value.returncode == 0, value.stdout + value.stderr
    assert json.loads(value.stdout)["status"] == "STOPPED_SOURCE_TREES_MATCH_PINNED_BACKUP"
    assert before == {g: snapshot(p) for g, p in roots.items()}


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_real_docker_new_helper_admission_and_isolation(tmp_path):
    import uuid

    image = subprocess.check_output(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            "phase4-runtime-code-package:20261111",
        ],
        text=True,
    ).strip()
    volumes = []
    helper = None
    try:
        for suffix in ("db", "runtime"):
            name = "test-source-compare-" + uuid.uuid4().hex + "-" + suffix
            subprocess.run(["docker", "volume", "create", name], check=True, capture_output=True)
            volumes.append(name)
        args = [
            "docker",
            "create",
            "--pull=never",
            "--interactive",
            "--network",
            "none",
            "--restart",
            "no",
            "--read-only",
            "--memory",
            "512m",
            "--pids-limit",
            "32",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "DAC_OVERRIDE",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "0:0",
        ]
        for v, g in zip(volumes, ("database", "runtime"), strict=True):
            args += ["--mount", f"type=volume,source={v},target=/source/{g},readonly,volume-nocopy"]
        args += ["--entrypoint", "python", image, "-I", "-B", "-"]
        helper = subprocess.check_output(args, text=True).strip()
        for name, raw in package().items():
            (tmp_path / name).write_bytes(raw)
        state = dict(
            canonical_images=[image], database_volume=volumes[0], runtime_volume=volumes[1]
        )
        (tmp_path / "state.json").write_text(json.dumps(state))
        body = f"""
. '{tmp_path}/CONTEXT.ps1'
. '{tmp_path}/SUPPORT.ps1'
. '{tmp_path}/COMPARE_CORE.ps1'
$state=Get-Content -Raw '{tmp_path}/state.json'|ConvertFrom-Json
Assert-CompareHelper '{helper}' $state @([pscustomobject]@{{id=('b'*64)}})
'ACCEPTED'
"""
        result = launch(tmp_path, body)
        assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
        assert result.stdout.strip() == "ACCEPTED"
    finally:
        if helper:
            subprocess.run(["docker", "rm", helper], check=True, capture_output=True)
        for v in volumes:
            subprocess.run(["docker", "volume", "rm", v], check=True, capture_output=True)


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "fault",
    [
        "valid",
        "check",
        "bad-input",
        "running-source",
        "worker-error",
        "changed-scope",
        "both",
        "no-mode",
    ],
)
def test_orchestration_before_after_guards_and_no_source_start(tmp_path, fault):
    files = package()
    for n, b in files.items():
        (tmp_path / n).write_bytes(b)
    backup = tmp_path / "backup"
    backup.mkdir()
    manifest = dict(
        groups={
            g: dict(entries=[dict(kind="file")], logical_bytes=16) for g in ("database", "runtime")
        }
    )
    for name in (
        "BACKUP_RECEIPT.json",
        "BACKUP_STATE.json",
        "database.tar",
        "runtime.tar",
        "images.tar",
    ):
        (backup / name).write_bytes(b"{}")
    (backup / "manifest.json").write_text(json.dumps(manifest))
    config = dict(
        canonical_images=["sha256:" + "8" * 64],
        database_volume="db",
        runtime_volume="runtime",
        manifest_sha256="a" * 64,
    )
    (tmp_path / "test-config.json").write_text(json.dumps(config))
    mode = {"check": "-CheckOnly", "both": "-CheckOnly -CompareOnce", "no-mode": ""}.get(
        fault, "-CompareOnce"
    )
    script = f"""
. '{tmp_path}/COMPARE_CORE.ps1'
$script:creates=0;$script:starts=0;$script:snapshots=0;$script:helperChecks=0
function Read-RestoreJson {{param($Path,$Maximum) if($Path.EndsWith('manifest.json')){{return Get-Content -Raw $Path|ConvertFrom-Json}};return Get-Content -Raw '{tmp_path}/test-config.json'|ConvertFrom-Json}}
function Find-RetainedBackup {{param($Config) return '{backup}'}}
function Assert-InputHashes {{param($Directory,$Config,$Receipt) if('{fault}' -ceq 'bad-input'){{throw 'RESTORE_RETAINED_HASH_MISMATCH'}}}}
function Read-RestoreSources {{param($Config,$Receipt,$State) if('{fault}' -ceq 'running-source'){{throw 'RESTORE_ORIGINAL_NOT_STOPPED'}};return ,@([pscustomobject]@{{id=('b'*64)}})}}
function Assert-RestoreSourceSnapshot {{param($Original,$Helpers,$Frozen) $script:snapshots++;if('{fault}' -ceq 'changed-scope' -and $script:snapshots -eq 2){{throw 'COLD_SOURCE_RUNNING_STATE_CHANGED'}}}}
function Backup-Docker {{param($Arguments,$Maximum) if($Arguments[0] -cne 'create'){{throw 'UNEXPECTED_COMMAND'}};$script:creates++;return ('c'*64)}}
function Assert-CompareHelper {{param($Id,$State,$Original) if($Id -cne ('c'*64)){{throw 'WRONG_HELPER'}};$script:helperChecks++}}
function docker {{begin{{if(($args -join '|') -cne ('start|--attach|--interactive|'+('c'*64))){{throw 'SOURCE_START_ATTEMPT'}};$script:starts++}}process{{}}end{{$global:LASTEXITCODE=0;if('{fault}' -ceq 'worker-error'){{$global:LASTEXITCODE=2}};'{{"status":"STOPPED_SOURCE_TREES_MATCH_PINNED_BACKUP","failure_category":"NONE","passes":2,"file_counts":{{"database":1,"runtime":1}},"logical_bytes":{{"database":16,"runtime":16}},"source_data_written":false,"logical_account_row_queried":false}}'}}}}
$output=@(Invoke-SourceCompare '{tmp_path}' {mode})
[pscustomobject]@{{output=$output;creates=$script:creates;starts=$script:starts;snapshots=$script:snapshots;helperChecks=$script:helperChecks}}|ConvertTo-Json -Depth 5 -Compress
"""
    value = launch(tmp_path, script)
    assert value.returncode == 0 and not value.stderr, value.stdout + value.stderr
    result = json.loads(value.stdout)
    expected_success = fault in {"valid", "check"}
    assert result["output"][-1] == (0 if expected_success else 2), result
    if fault in {"bad-input", "running-source", "both", "no-mode", "check"}:
        assert result["creates"] == result["starts"] == 0, result
    if fault == "changed-scope":
        assert result["creates"] == 1 and result["starts"] == 0
    if fault == "valid":
        assert (
            result["creates"] == result["starts"] == 1
            and result["helperChecks"] == 2
            and result["snapshots"] == 3
        )


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_transfer_seal_blocks_before_docker_or_input_access(tmp_path):
    files = package()
    for n, b in files.items():
        (tmp_path / n).write_bytes(b)
    pin = hashlib.sha256(files["TRANSFER_MANIFEST.json"]).hexdigest()
    (tmp_path / "COMPARE_CORE.ps1").write_text("PRIVATE_DO_NOT_EXECUTE")
    body = f"function docker {{throw 'DOCKER_MUST_NOT_RUN'}}\n& '{tmp_path}/RUN_COMPARE.ps1' -ManifestSHA256 '{pin}' -CompareOnce\nexit $LASTEXITCODE"
    value = launch(tmp_path, body)
    assert value.returncode == 2 and not value.stderr, value.stdout + value.stderr
    assert (
        "PRIVATE_DO_NOT_EXECUTE" not in value.stdout and "DOCKER_MUST_NOT_RUN" not in value.stdout
    )
    assert json.loads(value.stdout)["source_compare"] == "BLOCKED_TRANSFER_OR_LAUNCHER"
