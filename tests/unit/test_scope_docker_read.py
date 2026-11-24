"""Managed native Docker reads: stderr/exit handling, exact argv, no mutation commands."""

import json
import sys

import pytest

from tests.unit.test_cold_backup_executor import PWSH
from tests.unit.test_evidence_receipt_review import launch
from tests.unit.test_original_scope_bundle import package

pytestmark = pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")


def execute(tmp_path, mode, args=None, maximum=128):
    files = package()
    (tmp_path / "DOCKER_READ.ps1").write_bytes(files["DOCKER_READ.ps1"])
    directory = tmp_path / "fake bin"
    directory.mkdir()
    fake = directory / "docker"
    mark = tmp_path / "invoked"
    fake.write_text(f"""#!{sys.executable}
import json,sys,time
from pathlib import Path
Path({str(mark)!r}).write_text('called')
mode={mode!r}
if mode=='stderr-good':
    print('PRIVATE_SECRET_SENTINEL',file=sys.stderr);print('a'*64)
elif mode=='daemon':
    print('error during connect: dockerDesktopLinuxEngine PRIVATE_SECRET_SENTINEL',file=sys.stderr);sys.exit(1)
elif mode=='fail':
    print('PRIVATE_SECRET_SENTINEL',file=sys.stderr);sys.exit(7)
elif mode=='timeout':time.sleep(60)
elif mode=='many':print('a\\nb\\nc')
elif mode=='long':print('a'*65537)
elif mode=='argv':print(json.dumps(sys.argv[1:]))
elif mode=='empty':pass
else:print('a'*64)
""")
    fake.chmod(0o700)
    if mode == "unlaunchable":
        fake.write_text("PRIVATE_SECRET_SENTINEL")
    if args is None:
        args = ["ps", "--all", "--quiet", "--no-trunc"]
    data = json.dumps(args).replace("'", "''")
    script = f"""
. '{tmp_path}/DOCKER_READ.ps1'
$env:PATH='{directory}'+[IO.Path]::PathSeparator+$env:PATH
$argv=ConvertFrom-Json -InputObject '{data}'
try{{$lines=@(Invoke-ScopeDockerRead $argv {maximum});[pscustomobject]@{{status='OK';lines=$lines;diagnostic=$script:ScopeDockerDiagnostic}}|ConvertTo-Json -Depth 5 -Compress}}catch{{[pscustomobject]@{{status='BLOCKED';code=$_.Exception.Message;diagnostic=$script:ScopeDockerDiagnostic}}|ConvertTo-Json -Depth 5 -Compress}}
"""
    value = launch(tmp_path, script)
    assert value.returncode == 0 and not value.stderr, value.stdout + value.stderr
    assert "PRIVATE_SECRET_SENTINEL" not in value.stdout
    return json.loads(value.stdout), mark.exists()


@pytest.mark.parametrize("mode", ["good", "stderr-good", "empty"])
def test_successful_read_uses_stdout_and_exit_code_even_with_stderr(tmp_path, mode):
    r, called = execute(tmp_path, mode)
    assert called and r["status"] == "OK" and r["diagnostic"]["exit_code"] == 0
    assert r["diagnostic"]["stderr_present"] is (mode == "stderr-good")
    assert r["lines"] == ([] if mode == "empty" else ["a" * 64])


@pytest.mark.parametrize(
    "mode,code",
    [
        ("daemon", "SCOPE_DOCKER_DAEMON_UNAVAILABLE"),
        ("fail", "SCOPE_DOCKER_READ_FAILED"),
        ("many", "SCOPE_DOCKER_OUTPUT_LIMIT"),
        ("long", "SCOPE_DOCKER_OUTPUT_LIMIT"),
        ("timeout", "SCOPE_DOCKER_READ_TIMEOUT"),
    ],
)
def test_native_failure_is_controlled_and_never_exposes_stderr(tmp_path, mode, code):
    r, called = execute(tmp_path, mode, maximum=1)
    assert called and r["status"] == "BLOCKED" and r["code"] == code
    if mode == "fail":
        assert r["diagnostic"]["exit_code"] == 7


@pytest.mark.parametrize(
    "args",
    [
        ["start", "a" * 64],
        ["create", "image"],
        ["kill", "a" * 64],
        ["volume", "rm", "data"],
        ["image", "rm", "sha256:" + "a" * 64],
        ["inspect", "--format", "{{.Id}}", "--bad-option"],
    ],
)
def test_mutating_commands_and_option_substitution_never_start_a_process(tmp_path, args):
    r, called = execute(tmp_path, "good", args)
    assert not called and r["code"] == "SCOPE_DOCKER_READ_OPERATION_FORBIDDEN"
    assert not r["diagnostic"]["process_started"]


def test_native_argv_preserves_spaces_quotes_and_backslashes_without_shell(tmp_path):
    args = ["inspect", "--format", '{{json .Config.Labels}} "quoted" C:\\tail\\', "a" * 64]
    r, called = execute(tmp_path, "argv", args)
    assert called and r["status"] == "OK"
    assert json.loads(r["lines"][0]) == args


def test_missing_cli_is_classified_without_starting_docker_desktop(tmp_path):
    (tmp_path / "DOCKER_READ.ps1").write_bytes(package()["DOCKER_READ.ps1"])
    empty = tmp_path / "empty"
    empty.mkdir()
    value = launch(
        tmp_path,
        f"""
. '{tmp_path}/DOCKER_READ.ps1'
$env:PATH='{empty}'
try{{$null=Invoke-ScopeDockerRead @('ps','--all','--quiet','--no-trunc');throw 'TEST_NOT_BLOCKED'}}catch{{[pscustomobject]@{{code=$_.Exception.Message;diagnostic=$script:ScopeDockerDiagnostic}}|ConvertTo-Json -Depth 5 -Compress}}
""",
    )
    assert value.returncode == 0 and not value.stderr, value.stdout + value.stderr
    r = json.loads(value.stdout)
    assert r["code"] == "SCOPE_DOCKER_CLI_NOT_FOUND" and not r["diagnostic"]["process_started"]


def test_native_start_failure_does_not_expose_executable_path_or_content(tmp_path):
    r, called = execute(tmp_path, "unlaunchable")
    assert not called and r["code"] == "SCOPE_DOCKER_PROCESS_START_FAILED"
    assert not r["diagnostic"]["process_started"]
