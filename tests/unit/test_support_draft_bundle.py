"""Private draft creation uses synthetic identifiers; never calls a remote service."""

import io
import json
import os
import tarfile
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_support_draft_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch
from tests.unit.test_private_receipt_review_v2 import snapshot
from tests.unit.test_request_reference_bundle import h, records

pytestmark = pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
STOP = "DS-" + "a" * 30
EXIT = "DX-" + "a" * 30


def package():
    release = os.getenv("SUPPORT_DRAFT_RELEASE_ZIP")
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


def setup(tmp_path, change=None):
    adopt, plan = records()
    state = adopt["old_row"]["state"]
    for i, identifier, field in ((2, STOP, "algo_id"), (3, EXIT, "exit_id")):
        old = state["orders"][str(i)][field]
        state["requests"][identifier] = state["requests"].pop(old)
        state["orders"][str(i)][field] = identifier
        plan["plan"][i]["request_reference_sha256"] = h(identifier.encode())
    state["orders"]["2"]["server_order_id"] = state["orders"]["3"]["server_order_id"]
    original = dict(policy=plan["policy"], observations=[])
    exit_report = dict(policy="PREVIOUSLY_UNQUERIED_EXIT_EVIDENCE_20261113_V1", observations=[])
    for i, report in ((2, original), (3, exit_report)):
        report["observations"].append(
            dict(
                role=plan["plan"][i]["role"],
                request_reference_sha256=plan["plan"][i]["request_reference_sha256"],
                observed_status="NOT_FOUND_UNRESOLVED",
                response_identity_fields_match=False,
                remote_resolution_verified=False,
            )
        )
    if change:
        change(state, original, exit_report)
    root = tmp_path / "data" / "WBKANG-DEMO-Backups"
    folder = root / ("cold-20261114-" + "1" * 32)
    folder.mkdir(parents=True)
    entries = []
    prefix = "demo-runtime-20261111/"
    bodies = {
        prefix + "accounted-day13-adoption-11111111-1111-1111-1111-111111111111/intent.json": adopt,
        prefix
        + "historical-request-evidence-22222222-2222-2222-2222-222222222222/intent.json": plan,
        prefix
        + "historical-request-evidence-22222222-2222-2222-2222-222222222222/complete.json": original,
        prefix
        + "historical-exit-evidence-33333333-3333-3333-3333-333333333333/complete.json": exit_report,
    }
    archive = folder / "runtime.tar"
    with tarfile.open(archive, "w") as tar:
        for name, value in bodies.items():
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


def run(tmp_path, root, pin, mode="-CheckOnly", extra="", private=False):
    override = ""
    if private:
        override = f"function Reference-NewDirectory {{param($Token) $p='{tmp_path}/private'; $null=[IO.Directory]::CreateDirectory($p);return $p}}"
    result = launch(
        tmp_path,
        f"""
. '{tmp_path}/READ_CORE.ps1'
. '{tmp_path}/PRIVATE_STORAGE.ps1'
. '{tmp_path}/SUPPORT_CORE.ps1'
function tar.exe {{& tar @args}}
function docker {{throw 'MUST_NOT_RUN'}}
{override}
{extra}
$template=[IO.File]::ReadAllText('{tmp_path}/REQUEST_TEMPLATE_KO.txt',[Text.Encoding]::UTF8)
Invoke-SupportDraft '{root}' '{pin}' $template {mode}|ConvertTo-Json -Depth 8 -Compress
""",
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert STOP not in result.stdout and EXIT not in result.stdout
    assert "NEVER_OUTPUT" not in result.stdout
    return json.loads(result.stdout)


def test_check_is_read_only_and_create_contains_only_selected_identifiers(tmp_path):
    root, _, pin = setup(tmp_path)
    before = snapshot(root)
    check = run(tmp_path, root, pin)
    assert check["support_draft"] == "PINNED_TWO_REQUEST_DRAFT_INPUTS_CHECKED_NO_FILE_CREATED", (
        check
    )
    assert not (tmp_path / "private").exists()
    result = run(tmp_path, root, pin, "-CreateDraft", private=True)
    assert result["support_draft"] == "PRIVATE_TWO_REQUEST_SUPPORT_DRAFT_CREATED_NOT_SENT", result
    draft = (tmp_path / "private/REQUEST_CLARIFICATION_KO.txt").read_text()
    receipt = (tmp_path / "private/DRAFT_RECEIPT.json").read_text()
    assert draft.count(STOP) == 1 and draft.count(EXIT) == 1
    assert (
        "{{" not in draft and "NEVER_OUTPUT" not in draft and "PRIVATE_CLIENT_SENTINEL" not in draft
    )
    assert STOP not in receipt and EXIT not in receipt
    assert h(draft.encode()) == result["draft_sha256"]
    assert snapshot(root) == before and result["messages_sent"] == 0
    again = run(tmp_path, root, pin, "-CreateDraft", private=True)
    assert again["support_draft"] == "BLOCKED_NO_AUTOMATIC_RETRY"
    assert (tmp_path / "private/REQUEST_CLARIFICATION_KO.txt").read_text() == draft


@pytest.mark.parametrize("mode", ["", "-CheckOnly -CreateDraft"])
def test_explicit_mode_required_before_reads(tmp_path, mode):
    root, _, pin = setup(tmp_path)
    result = run(
        tmp_path, root, pin, mode, extra="function Inspect-Open {throw 'READ_MUST_NOT_RUN'}"
    )
    assert result["failure_category"] == "SUPPORT_EXPLICIT_MODE_REQUIRED"


@pytest.mark.parametrize("bad", ["observation", "duplicate", "entry_link", "policy", "identity"])
def test_invalid_target_evidence_refuses_before_creation(tmp_path, bad):
    def change(state, original, exit_report):
        if bad == "observation":
            exit_report["observations"][0]["observed_status"] = "FILLED"
        elif bad == "duplicate":
            original["observations"] *= 2
        elif bad == "entry_link":
            state["orders"]["3"]["server_order_id"] = "123"
        elif bad == "policy":
            original["policy"] = "UNTRUSTED"
        else:
            original["observations"][0]["response_identity_fields_match"] = True

    root, _, pin = setup(tmp_path, change)
    result = run(tmp_path, root, pin, "-CreateDraft", private=True)
    assert result["support_draft"] == "BLOCKED_NO_AUTOMATIC_RETRY"
    assert not (tmp_path / "private").exists()


def test_bad_template_or_corrupt_archive_creates_nothing(tmp_path):
    root, folder, pin = setup(tmp_path)
    (tmp_path / "REQUEST_TEMPLATE_KO.txt").write_text("INVALID")
    assert (
        run(tmp_path, root, pin, "-CreateDraft", private=True)["failure_category"]
        == "SUPPORT_TEMPLATE_INVALID"
    )
    archive = folder / "runtime.tar"
    archive.write_bytes(archive.read_bytes() + b"x")
    assert (
        run(tmp_path, root, pin, "-CreateDraft", private=True)["failure_category"]
        == "INSPECT_ARCHIVE_HASH_MISMATCH"
    )
    assert not (tmp_path / "private").exists()


def test_native_linux_refuses_claiming_windows_acl(tmp_path):
    root, _, pin = setup(tmp_path)
    result = run(tmp_path, root, pin, "-CreateDraft")
    assert result["failure_category"] == "REFERENCE_WINDOWS_ACL_REQUIRED"


@pytest.mark.parametrize(
    "file", ["SUPPORT_CORE.ps1", "PRIVATE_STORAGE.ps1", "REQUEST_TEMPLATE_KO.txt"]
)
def test_seal_rejects_modified_code_or_template_before_execution(tmp_path, file):
    for name, body in package().items():
        (tmp_path / name).write_bytes(body)
    pin = h((tmp_path / "TRANSFER_MANIFEST.json").read_bytes())
    (tmp_path / file).write_text("PRIVATE_IDENTIFIER_MUST_NOT_RUN")
    result = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_SUPPORT_DRAFT.ps1' -ManifestSHA256 '{pin}' -CheckOnly\nexit $LASTEXITCODE",
    )
    assert result.returncode == 2 and not result.stderr
    assert "PRIVATE_IDENTIFIER" not in result.stdout
    assert json.loads(result.stdout)["failure_category"] == "INSPECT_TRANSFER_INVALID"


def test_sealed_check_with_synthetic_pin(tmp_path):
    root, _, pin = setup(tmp_path)
    path = tmp_path / "RUN_SUPPORT_DRAFT.ps1"
    path.write_text(
        path.read_text().replace(
            "2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d", pin
        )
    )
    manifest = json.loads((tmp_path / "TRANSFER_MANIFEST.json").read_bytes())
    manifest["files"][path.name] = h(path.read_bytes())
    body = json.dumps(manifest).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(body)
    before = snapshot(root)
    result = launch(
        tmp_path,
        f"function tar.exe {{& tar @args}}\n& '{path}' -ManifestSHA256 '{h(body)}' -CheckOnly\nexit $LASTEXITCODE",
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert "SUPPORT_DRAFT_CHECK_PASSED_NO_WRITES_NO_HTTP" in result.stdout
    assert snapshot(root) == before and STOP not in result.stdout and EXIT not in result.stdout


def test_readback_failure_retains_partial_without_success_or_retry(tmp_path):
    root, _, pin = setup(tmp_path)
    result = run(
        tmp_path,
        root,
        pin,
        "-CreateDraft",
        private=True,
        extra="""
function Support-WriteNew {param($Path,$Bytes) [IO.File]::WriteAllText($Path,'CORRUPTED_SYNTHETIC_OUTPUT')}
""",
    )
    assert result["failure_category"] == "SUPPORT_READBACK_MISMATCH"
    assert result["draft_creation_may_have_started"] is True
    assert result["automatic_retry"] is False and result["messages_sent"] == 0
    assert not (tmp_path / "private/DRAFT_RECEIPT.json").exists()


def test_final_source_recheck_is_required_after_write(tmp_path):
    root, _, pin = setup(tmp_path)
    result = run(
        tmp_path,
        root,
        pin,
        "-CreateDraft",
        private=True,
        extra="""
$script:hashReads=0
$script:originalStreamHash=${function:Inspect-StreamHash}
function Inspect-StreamHash {param($Stream) $script:hashReads++;if($script:hashReads -ge 5){return ('0'*64)};& $script:originalStreamHash $Stream}
""",
    )
    assert result["failure_category"] == "SUPPORT_SOURCE_CHANGED"
    assert result["support_draft"] == "BLOCKED_NO_AUTOMATIC_RETRY"


def test_identifier_shape_and_same_entry_required_before_files(tmp_path):
    root, _, pin = setup(tmp_path)
    result = run(
        tmp_path,
        root,
        pin,
        "-CreateDraft",
        private=True,
        extra="""
$script:originalTargets=${function:Support-Targets}
function Support-Targets {param($State,$Rows,$Original,$ExitReport)
 $p=@($State.requests.PSObject.Properties|Where-Object {$_.Name.StartsWith('DS-')})[0]
 $value=$p.Value;$State.requests.PSObject.Properties.Remove($p.Name)
 $State.requests|Add-Member NoteProperty 'INVALID_IDENTIFIER' $value
 $row=@($Rows|Where-Object {$_.role -ceq 'STOP_LOSS'})[0]
 $row.request_reference_sha256=Inspect-Hash ([Text.Encoding]::UTF8.GetBytes('INVALID_IDENTIFIER'))
 & $script:originalTargets $State $Rows $Original $ExitReport
}
""",
    )
    assert result["failure_category"] == "SUPPORT_IDENTIFIER_SHAPE_INVALID"
    assert not (tmp_path / "private").exists()


def test_scripts_parse_without_network_or_service_commands_and_core_is_pinned(tmp_path):
    files = package()
    assert files["READ_CORE.ps1"] == builder.git(
        "show",
        "f3f8e44d11bb4696fe06fc38aed4da14f52837e0:tools/windows_demo_request_reference/INSPECT_CORE.ps1",
    )
    for name, body in files.items():
        (tmp_path / name).write_bytes(body)
    result = launch(
        tmp_path,
        f"""
foreach($file in @('READ_CORE.ps1','PRIVATE_STORAGE.ps1','SUPPORT_CORE.ps1','RUN_SUPPORT_DRAFT.ps1')) {{
 $tokens=$null;$errors=$null
 $ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path '{tmp_path}' $file),[ref]$tokens,[ref]$errors)
 if($errors.Count -gt 0){{throw 'PARSE_ERROR'}}
 foreach($node in $ast.FindAll({{param($n) $n -is [Management.Automation.Language.CommandAst]}},$true)) {{
  if($node.GetCommandName() -in @('docker','Start-Process','Invoke-WebRequest','Invoke-RestMethod','Invoke-Expression','Remove-Item','Copy-Item','Move-Item')){{throw 'FORBIDDEN_COMMAND'}}
 }}
}}
'PARSED_NO_NETWORK_OR_SERVICES'
""",
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
