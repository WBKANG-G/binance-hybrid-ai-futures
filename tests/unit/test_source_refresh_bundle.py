"""Retained read-only reader lifecycle, fail-closed orchestration and sealed bytes."""

import base64
import hashlib
import json
import os
import subprocess
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_source_refresh_bundle as builder
from tests.unit import test_source_compare_bundle as prior
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch
from tests.unit.test_private_receipt_review_v2 import snapshot


def package():
    release = os.getenv("SOURCE_REFRESH_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git

    def read(*args):
        if args[0] == "show" and ":tools/windows_demo_source_refresh/" in args[1]:
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with patch.object(builder, "git", read):
        return builder.package_files("HEAD")


def test_seal_and_unchanged_worker_guards():
    f = package()
    old = builder.scope_files(builder.SCOPE)
    for n in ("SOURCE_SCOPE.ps1", "COMPARE_ADMISSION.ps1", "FINANCIAL_EXT.ps1"):
        assert f[n] == old[n]
    comparator = builder.compare_files(builder.COMPARE)
    for n in ("worker.py", "READ_LIBRARY.py"):
        assert f[n] == comparator[n]
    m = json.loads(f["TRANSFER_MANIFEST.json"])
    assert all(hashlib.sha256(f[n]).hexdigest() == h for n, h in m["files"].items())
    assert b"StandardInputEncoding" not in f["REFRESH_CORE.ps1"]
    text = b"\n".join(v for n, v in f.items() if n.endswith((".py", ".ps1")))
    for forbidden in (
        b"function Stop-OwnedClone",
        b"function Invoke-IsolatedRestore",
        b"volume','create",
        b"Config.Env",
        b"def write_group",
        b"def restore_bundle",
    ):
        assert forbidden not in text


def test_matching_worker(tmp_path):
    with patch.object(prior, "package", package):
        prior.test_matching_two_passes_preserve_bytes_metadata_and_no_raw_output(tmp_path)


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
def test_worker_fault(tmp_path, fault):
    with patch.object(prior, "package", package):
        prior.test_mismatch_and_unsupported_files_fail_closed_without_writes(tmp_path, fault)


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
def test_unchanged_exact_readonly_helper_guard(tmp_path, fault):
    f = package()
    f["COMPARE_CORE.ps1"] = f["COMPARE_ADMISSION.ps1"]
    with patch.object(prior, "package", lambda: f):
        prior.test_helper_must_be_new_stopped_exact_image_and_readonly_isolated(tmp_path, fault)


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "name",
    [
        "CONFIG.json",
        "SOURCE_ACCEPTANCE.json",
        "worker.py",
        "REFRESH_CORE.ps1",
        "TRANSFER_MANIFEST.json",
    ],
)
def test_tamper_blocks_before_helper_or_input_access(tmp_path, name):
    f = package()
    for n, b in f.items():
        (tmp_path / n).write_bytes(b)
    (tmp_path / name).write_text("PRIVATE_SENTINEL")
    v = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_COMPARE.ps1' -ManifestSHA256 '{hashlib.sha256(f['TRANSFER_MANIFEST.json']).hexdigest()}' -CompareOnce\nexit $LASTEXITCODE",
    )
    assert v.returncode == 2 and not v.stderr, v.stdout + v.stderr
    assert "PRIVATE_SENTINEL" not in v.stdout and "SCOPE_TRANSFER_INVALID" in v.stdout


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "check",
        "hash",
        "scope",
        "admission",
        "worker",
        "post-scope",
        "counts",
        "both",
        "no-mode",
    ],
)
def test_native_entry_guards_and_receipt(tmp_path, fault):
    f = package()
    for n, b in f.items():
        (tmp_path / n).write_bytes(b)
    backup = tmp_path / "backup"
    backup.mkdir()
    for n in (
        "BACKUP_RECEIPT.json",
        "BACKUP_STATE.json",
        "database.tar",
        "runtime.tar",
        "images.tar",
    ):
        (backup / n).write_text("{}")
    (backup / "manifest.json").write_text(
        json.dumps(
            dict(
                groups={
                    g: dict(entries=[dict(kind="file")], logical_bytes=16)
                    for g in ("database", "runtime")
                }
            )
        )
    )
    ext = tmp_path / "FINANCIAL_EXT.ps1"
    ext.write_text(
        ext.read_text()
        + f"""
function Container-Ids {{return ,('b'*64)}}
function Find-RetainedBackup {{param($Config) return '{backup}'}}
function Assert-InputHashes {{param($Directory,$Config,$Receipt) if('{fault}' -ceq 'hash'){{throw 'RESTORE_INPUT_HASH_MISMATCH'}}}}
function Read-CopySources {{param($Config,$Receipt,$State) if('{fault}' -ceq 'scope'){{throw 'RESTORE_ORIGINAL_NOT_STOPPED'}};$script:FinancialPrior=[pscustomobject]@{{account_state_sha256=('a'*64)}};return ,@([pscustomobject]@{{id=('b'*64)}})}}
function Assert-RestoreSourceSnapshot {{param($Original,$Helpers,$Frozen) if($Frozen){{throw 'TEST_BAD_FROZEN'}};if($script:RefreshHelperVisible -ne ($Helpers.Count -eq 1)){{throw 'COMPARE_SNAPSHOT_ORDER_INVALID'}};if('{fault}' -ceq 'post-scope' -and (Test-Path '{tmp_path}/started')){{throw 'COLD_SOURCE_SCOPE_CHANGED'}}}}
function New-PrivateBackupDirectory {{param($Id) $d='{tmp_path}/report';$null=New-Item -ItemType Directory $d;return $d}}
"""
    )
    refresh = tmp_path / "REFRESH_CORE.ps1"
    refresh.write_text(
        refresh.read_text()
        + f"""
function Assert-RefreshReader {{param($Config,$State,$Original) if('{fault}' -ceq 'admission'){{throw 'COMPARE_HELPER_SCOPE_INVALID'}}}}
function Invoke-RefreshPayload {{param($Config,$State,$Original,$Payload) if($Config.comparison_helper_id -cne '{json.loads(f["CONFIG.json"])["comparison_helper_id"]}'){{throw 'COMPARE_WRONG_HELPER'}};[IO.File]::WriteAllText('{tmp_path}/started','yes');if('{fault}' -ceq 'worker'){{throw 'COMPARE_READER_RUN_FAILED'}};return [pscustomobject]@{{status='STOPPED_SOURCE_TREES_MATCH_PINNED_BACKUP';failure_category='NONE';passes=2;file_counts=[pscustomobject]@{{database=$(if('{fault}' -ceq 'counts'){{2}}else{{1}});runtime=1}};logical_bytes=[pscustomobject]@{{database=16;runtime=16}};source_data_written=$false;logical_account_row_queried=$false}}}}
"""
    )
    m = json.loads(f["TRANSFER_MANIFEST.json"])
    for n in ("FINANCIAL_EXT.ps1", "REFRESH_CORE.ps1"):
        m["files"][n] = hashlib.sha256((tmp_path / n).read_bytes()).hexdigest()
    raw = json.dumps(m).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(raw)
    before = {p.name: p.read_bytes() for p in backup.iterdir()}
    mode = {"check": "-CheckOnly", "both": "-CheckOnly -CompareOnce", "no-mode": ""}.get(
        fault, "-CompareOnce"
    )
    v = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_COMPARE.ps1' -ManifestSHA256 '{hashlib.sha256(raw).hexdigest()}' {mode}\nexit $LASTEXITCODE",
    )
    assert not v.stderr, v.stdout + v.stderr
    assert v.returncode == (0 if fault in {"none", "check"} else 2), v.stdout
    assert before == {p.name: p.read_bytes() for p in backup.iterdir()}
    assert (tmp_path / "started").exists() == (fault in {"none", "worker", "post-scope", "counts"})
    if fault == "none":
        r = json.loads((tmp_path / "report/SOURCE_REFRESH_RECEIPT.json").read_bytes())
        assert r["passes"] == 2 and r["helper_stopped_verified"]
        assert not r["original_apply_ready"] and not r["current_original_sql_queried"]
        assert r["containers_created"] == r["volumes_created"] == r["order_writes"] == 0
    elif fault == "check":
        assert not (tmp_path / "report").exists()
        assert "SOURCE_REFRESH_CHECK_PASSED_NO_HELPER_START" in v.stdout
    else:
        assert "SOURCE_REFRESH_MATCHED" not in v.stdout


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("fault", ["none", "closed", "once", "tty", "exit", "guard"])
def test_reuse_admission_stdin_exit_and_visible_container_set(tmp_path, fault):
    (tmp_path / "REFRESH_CORE.ps1").write_bytes(package()["REFRESH_CORE.ps1"])
    body = f"""
function Container-Ids {{return @('original','extra')}}
function Financial-AllContainerIds {{return @('original','extra','helper')}}
. '{tmp_path}/REFRESH_CORE.ps1'
if((@(Container-Ids)-join '|') -cne 'original|extra'){{throw 'HIDDEN_EXTRA'}}
$script:RefreshHelperVisible=$true
if((@(Container-Ids)-join '|') -cne 'original|extra|helper'){{throw 'HIDDEN_HELPER'}}
function Assert-CompareHelper {{param($Id,$State,$Original) if('{fault}' -ceq 'guard'){{throw 'COMPARE_HELPER_SCOPE_INVALID'}}}}
function Backup-Docker {{param($Arguments,$Maximum) if($Maximum -eq 3){{return @($(if('{fault}' -ceq 'closed'){{'false'}}else{{'true'}}),$(if('{fault}' -ceq 'once'){{'false'}}else{{'true'}}),$(if('{fault}' -ceq 'tty'){{'true'}}else{{'false'}}))}};return $(if('{fault}' -ceq 'exit'){{'2 false'}}else{{'0 false'}})}}
try{{Assert-RefreshReader ([pscustomobject]@{{comparison_helper_id='helper'}}) @{{}} @();'ACCEPTED'}}catch{{'BLOCKED'}}
"""
    v = launch(tmp_path, body)
    assert not v.stderr and v.returncode == 0, v.stdout + v.stderr
    assert v.stdout.strip() == ("ACCEPTED" if fault == "none" else "BLOCKED")


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_managed_client_twice_same_real_docker_reader(tmp_path):
    image = "phase4-runtime-code-package:20261111"
    subprocess.run(["docker", "image", "inspect", image], check=True, capture_output=True)
    with patch.object(prior, "package", package):
        roots, manifest, lib = prior.fixture(tmp_path)
    f = package()
    for n in ("REFRESH_CORE.ps1", "DOCKER_READ.ps1"):
        (tmp_path / n).write_bytes(f[n])
    payload = "import base64,json\nREAD_LIBRARY=base64.b64decode('{}')\nMANIFEST=json.loads(base64.b64decode('{}'))\nexec(compile(base64.b64decode('{}'),'<worker>','exec'))".format(
        *(
            base64.b64encode(v).decode()
            for v in (lib, json.dumps(manifest).encode(), f["worker.py"])
        )
    )
    (tmp_path / "payload.txt").write_text(payload)
    args = [
        "docker",
        "create",
        "--pull=never",
        "--interactive",
        "--network",
        "none",
        "--read-only",
        "--restart",
        "no",
        "--user",
        "0:0",
        "--cap-drop",
        "ALL",
        "--cap-add",
        "DAC_OVERRIDE",
        "--security-opt",
        "no-new-privileges",
    ]
    for g, p in roots.items():
        args += ["--mount", f"type=bind,source={p},target=/source/{g},readonly"]
    args += ["--entrypoint", "python", image, "-I", "-B", "-"]
    helper = subprocess.check_output(args, text=True).strip()
    before = {g: snapshot(p) for g, p in roots.items()}
    try:
        body = f"""
function Container-Ids {{}}
. '{tmp_path}/DOCKER_READ.ps1'
. '{tmp_path}/REFRESH_CORE.ps1'
# Mount/image admission is independently exercised; this test uses synthetic bind roots.
function Assert-RefreshReader {{param($Config,$State,$Original)}}
$config=[pscustomobject]@{{comparison_helper_id='{helper}'}}
$payload=[IO.File]::ReadAllText('{tmp_path}/payload.txt')
foreach($n in 1..2){{$v=Invoke-RefreshPayload $config @{{}} @() $payload;$v|ConvertTo-Json -Compress}}
"""
        v = launch(tmp_path, body)
        assert v.returncode == 0 and not v.stderr, v.stdout + v.stderr
        assert len(v.stdout.splitlines()) == 2
        for line in v.stdout.splitlines():
            assert json.loads(line)["passes"] == 2
        assert (
            subprocess.check_output(
                ["docker", "inspect", "--format", "{{.State.Running}} {{.State.ExitCode}}", helper],
                text=True,
            ).strip()
            == "false 0"
        )
        assert before == {g: snapshot(p) for g, p in roots.items()}
    finally:
        subprocess.run(["docker", "rm", helper], check=True, capture_output=True)


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("fault", ["exit", "json", "lines", "unicode"])
def test_managed_client_failure_is_fixed_and_never_echoes_stderr(tmp_path, fault):
    import sys

    f = package()
    for n in ("REFRESH_CORE.ps1", "DOCKER_READ.ps1"):
        (tmp_path / n).write_bytes(f[n])
    fake = tmp_path / "docker"
    fake.write_text(
        f"#!{sys.executable}\nimport sys\nassert sys.argv[1:]==['start','--attach','--interactive',{'a' * 64!r}]\nsys.stdin.read()\nsys.stderr.write('PRIVATE_SECRET_SENTINEL')\nprint({('not json' if fault == 'json' else '{}\n{}' if fault == 'lines' else '{}')!r})\nsys.exit({2 if fault == 'exit' else 0})\n"
    )
    fake.chmod(0o700)
    payload = "한글" if fault == "unicode" else "print(1)"
    v = launch(
        tmp_path,
        f"""
$env:PATH='{tmp_path}'+[IO.Path]::PathSeparator+$env:PATH
function Container-Ids {{}}
. '{tmp_path}/DOCKER_READ.ps1'
. '{tmp_path}/REFRESH_CORE.ps1'
function Assert-RefreshReader {{param($Config,$State,$Original)}}
try{{$null=Invoke-RefreshPayload ([pscustomobject]@{{comparison_helper_id=('a'*64)}}) @{{}} @() '{payload}';'WRONG_SUCCESS'}}catch{{$_.Exception.Message}}
""",
    )
    assert v.returncode == 0 and not v.stderr, v.stdout + v.stderr
    assert (
        v.stdout.strip()
        == {
            "exit": "COMPARE_READER_RUN_FAILED",
            "json": "COMPARE_READER_OUTPUT_INVALID",
            "lines": "COMPARE_READER_OUTPUT_INVALID",
            "unicode": "COMPARE_PAYLOAD_LIMIT",
        }[fault]
    )
    assert "PRIVATE_SECRET_SENTINEL" not in v.stdout
