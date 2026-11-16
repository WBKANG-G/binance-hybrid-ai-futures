"""Native PowerShell: bounded reference selection and non-overwriting USB copy.

Linux substitutes Windows ACL/media discovery only; no real USB/Windows ACL claim.
"""

import hashlib
import json
import os
import shutil
import zipfile
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from scripts import build_windows_reference_handoff_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch
from tests.unit.test_private_receipt_review_v2 import snapshot
from tests.unit.test_private_reference_bundle import run as reference_run
from tests.unit.test_private_reference_bundle import setup as reference_setup

pytestmark = pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")


def package():
    release = os.getenv("REFERENCE_HANDOFF_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git
    ref = real("rev-parse", "HEAD").decode().strip()

    def source(*args):
        if args[0] == "show" and args[1].startswith(ref + ":"):
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with ExitStack() as stack:
        for name in ("reference_handoff", "private_receipt_review_v2", "private_receipt_review"):
            stack.enter_context(patch("scripts.build_windows_" + name + "_bundle.git", source))
        return builder.package_files("HEAD")


def setup(tmp_path):
    base, _, _ = reference_setup(tmp_path)
    cfg = json.loads((tmp_path / "CONFIG.json").read_bytes())
    cfg["reference_builder_source_reference"] = "ee8aa302e90ec2b9227e03c557b2a226b78f0289"
    (tmp_path / "CONFIG.json").write_text(json.dumps(cfg))
    refs = tmp_path / "references"
    refs.mkdir()
    destination = refs / ("v3-reference-" + "1" * 32)
    # Reference creator generates its own UUID; rename folder to that genuine ID below.
    r = reference_run(
        tmp_path,
        base,
        "-CreateReference",
        f"function Reference-NewDirectory {{$null=[IO.Directory]::CreateDirectory('{destination}');return '{destination}'}}",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    assert json.loads(r.stdout)["reference_created"]
    manifest = json.loads((destination / "REFERENCE_MANIFEST.json").read_bytes())
    target = refs / ("v3-reference-" + manifest["reference_id"])
    destination.rename(target)
    for p, b in package().items():
        (tmp_path / p).write_bytes(b)
    usb = tmp_path / "usb"
    usb.mkdir()
    (usb / "existing-recovery-backup.tar").write_bytes(b"EXISTING_BACKUP_MUST_STAY")
    return base, refs, target, usb


def run(tmp_path, base, refs, usb, mode="-CopyReference", extra="", fs="exFAT"):
    return launch(
        tmp_path,
        f"""
. '{tmp_path}/REVIEW_CORE.ps1'
. '{tmp_path}/MEDIA.ps1'
. '{tmp_path}/HANDOFF_CORE.ps1'
function docker {{throw 'DOCKER_MUST_NOT_RUN'}}
function Reference-AssertPrivateDirectory {{}}
function Find-LocalReferenceBase {{return '{refs}'}}
function Read-HandoffMedia {{return [pscustomobject]@{{root='D:\\';identity_sha256=('a'*64);free_bytes=[long]100000000;filesystem='{fs}';bus='USB';is_boot=$false;is_system=$false;is_offline=$false;is_readonly=$false}}}}
# Linux substitutes destination root but runs production creation/validation for exFAT.
$script:newDirectory=${{function:Handoff-NewDirectory}}
function Handoff-NewDirectory([object]$Media,[string]$Token) {{
 $null=New-PSDrive -Name D -PSProvider FileSystem -Root '{usb}'
 # .NET GetAttributes uses native paths, so substitute only the Windows root check here.
 $p=Join-Path '{usb}' ('WBKANG-DEMO-Reference-20261116-'+$Token)
 Receipt-Require (-not [IO.Directory]::Exists($p) -and -not [IO.File]::Exists($p)) 'HANDOFF_DESTINATION_EXISTS'
 $script:HandoffAttemptPath=$p;$null=[IO.Directory]::CreateDirectory($p)
 Handoff-AssertDestination $p $Media.filesystem
 return $p
}}
{extra}
Invoke-ReferenceHandoff '{base}' (Get-Content -Raw '{tmp_path}/CONFIG.json'|ConvertFrom-Json) {mode}|ConvertTo-Json -Depth 16 -Compress
""",
    )


def result(r):
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    return json.loads(r.stdout)


def test_check_only_and_copy_preserve_sources_and_existing_backup(tmp_path):
    base, refs, folder, usb = setup(tmp_path)
    before = [snapshot(p) for p in (base, refs, usb)]
    v = result(run(tmp_path, base, refs, usb, "-CheckOnly"))
    assert v["reference_handoff"] == "SOURCE_REFERENCE_AND_USB_CHECKED_NO_COPY"
    assert [snapshot(p) for p in (base, refs, usb)] == before
    v = result(run(tmp_path, base, refs, usb))
    assert v["reference_handoff"] == "EXTERNAL_REFERENCE_HASHES_VERIFIED_SAFE_EJECT_PENDING", v
    dest = next(p for p in usb.iterdir() if p.is_dir())
    assert len(list(dest.iterdir())) == 3
    for name in ("REFERENCE_MANIFEST.json", "REFERENCE_RECEIPT.json"):
        assert (folder / name).read_bytes() == (dest / name).read_bytes()
    assert (usb / "existing-recovery-backup.tar").read_bytes() == b"EXISTING_BACKUP_MUST_STAY"
    assert [snapshot(p) for p in (base, refs)] == before[:2]
    assert (
        snapshot(usb)["existing-recovery-backup.tar"] == before[2]["existing-recovery-backup.tar"]
    )
    for flag in (
        "physical_disconnect_verified",
        "independent_external_custody_verified",
        "separate_pc_verification_performed",
        "execution_approved",
        "orders_enabled",
        "live_trading_enabled",
        "docker_used",
        "services_started",
        "destination_ntfs_acl_verified",
    ):
        assert v[flag] is False


@pytest.mark.parametrize("mode", ["", "-CheckOnly -CopyReference"])
def test_explicit_mode_required(tmp_path, mode):
    base, refs, _, usb = setup(tmp_path)
    v = result(
        run(
            tmp_path,
            base,
            refs,
            usb,
            mode,
            extra="function Review-PrivateReceipts {throw 'MUST_NOT_READ'}",
        )
    )
    assert v["failure_category"] == "HANDOFF_EXPLICIT_MODE_REQUIRED"
    assert len(list(usb.iterdir())) == 1


@pytest.mark.parametrize(
    "change", ["extra_secret", "bad_pin", "bad_fact", "bad_authority", "bad_link", "bad_hash"]
)
def test_invalid_reference_denied_before_any_usb_write(tmp_path, change):
    base, refs, folder, usb = setup(tmp_path)
    path = folder / "REFERENCE_MANIFEST.json"
    m = json.loads(path.read_bytes())
    if change == "extra_secret":
        m["secret"] = "DO_NOT_EXPORT"
    elif change == "bad_pin":
        m["prior_reference_pins"]["backup_manifest_sha256"] = "0" * 64
    elif change == "bad_fact":
        m["recorded_facts"]["orders_preserved"] = 35
    elif change == "bad_authority":
        m["execution_approved"] = True
    elif change == "bad_link":
        m["reference_id"] = "0" * 32
    elif change == "bad_hash":
        m["source_file_sha256"]["PRIVATE_RESOURCES.json"] = "0" * 64
    path.write_text(json.dumps(m))
    receipt = folder / "REFERENCE_RECEIPT.json"
    r = json.loads(receipt.read_bytes())
    r["manifest_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    receipt.write_text(json.dumps(r))
    v = result(run(tmp_path, base, refs, usb))
    assert v["reference_handoff"] == "BLOCKED_NO_AUTOMATIC_RETRY"
    assert len(list(usb.iterdir())) == 1


def test_duplicate_reference_refused(tmp_path):
    base, refs, folder, usb = setup(tmp_path)
    other = refs / ("v3-reference-" + "2" * 32)
    shutil.copytree(folder, other)
    m = json.loads((other / "REFERENCE_MANIFEST.json").read_bytes())
    m["reference_id"] = "2" * 32
    (other / "REFERENCE_MANIFEST.json").write_text(json.dumps(m))
    r = json.loads((other / "REFERENCE_RECEIPT.json").read_bytes())
    r["reference_id"] = "2" * 32
    r["manifest_sha256"] = hashlib.sha256(
        (other / "REFERENCE_MANIFEST.json").read_bytes()
    ).hexdigest()
    (other / "REFERENCE_RECEIPT.json").write_text(json.dumps(r))
    v = result(run(tmp_path, base, refs, usb))
    assert v["failure_category"] == "HANDOFF_REFERENCE_NOT_UNIQUE"
    assert len(list(usb.iterdir())) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("bus", "'SATA'"),
        ("filesystem", "'FAT32'"),
        ("is_boot", "$true"),
        ("is_system", "$true"),
        ("is_offline", "$true"),
        ("is_readonly", "$true"),
        ("root", "'E:\\'"),
        ("free_bytes", "[long]0"),
    ],
)
def test_invalid_media_refused_before_creation(tmp_path, field, value):
    base, refs, _, usb = setup(tmp_path)
    extra = f"$script:readMedia=${{function:Read-HandoffMedia}};function Read-HandoffMedia {{$m=& $script:readMedia;$m.{field}={value};return $m}}"
    v = result(run(tmp_path, base, refs, usb, extra=extra))
    assert v["reference_handoff"] == "BLOCKED_NO_AUTOMATIC_RETRY"
    assert len(list(usb.iterdir())) == 1


def test_media_swap_during_copy_retains_partial(tmp_path):
    base, refs, _, usb = setup(tmp_path)
    v = result(
        run(
            tmp_path,
            base,
            refs,
            usb,
            extra="""
$script:readMedia=${function:Read-HandoffMedia};$script:reads=0
function Read-HandoffMedia {$script:reads++;$m=& $script:readMedia;if ($script:reads -ge 4) {$m.identity_sha256=('b'*64)};return $m}
""",
        )
    )
    assert v["failure_category"] == "HANDOFF_MEDIA_CHANGED" and not v["automatic_retry"]
    dest = next(p for p in usb.iterdir() if p.is_dir())
    assert len(list(dest.iterdir())) == 1 and v["retained_partial_destination"] == str(dest)


def test_write_readback_corruption_retains_partial(tmp_path):
    base, refs, _, usb = setup(tmp_path)
    v = result(
        run(
            tmp_path,
            base,
            refs,
            usb,
            extra="""
$script:write=${function:Handoff-WriteNew}
function Handoff-WriteNew([string]$Path,[byte[]]$Bytes) {& $script:write $Path ([Text.Encoding]::UTF8.GetBytes('{}'))}
""",
        )
    )
    assert v["failure_category"] == "HANDOFF_COPY_HASH_MISMATCH"
    assert not v["automatic_retry"]
    assert len(list(next(p for p in usb.iterdir() if p.is_dir()).iterdir())) == 1


def test_create_new_never_overwrites(tmp_path):
    base, refs, _, usb = setup(tmp_path)
    dest = usb / "preexisting"
    dest.mkdir()
    (dest / "REFERENCE_MANIFEST.json").write_text("KEEP")
    v = result(
        run(tmp_path, base, refs, usb, extra=f"function Handoff-NewDirectory {{return '{dest}'}}")
    )
    assert v["reference_handoff"] == "BLOCKED_NO_AUTOMATIC_RETRY"
    assert (dest / "REFERENCE_MANIFEST.json").read_text() == "KEEP"
    assert len(list(dest.iterdir())) == 1


@pytest.mark.parametrize(
    "file", ["MEDIA.ps1", "HANDOFF_CORE.ps1", "REVIEW_CORE.ps1", "CONFIG.json"]
)
def test_transfer_tamper_denied(tmp_path, file):
    for p, b in package().items():
        (tmp_path / p).write_bytes(b)
    pin = hashlib.sha256((tmp_path / "TRANSFER_MANIFEST.json").read_bytes()).hexdigest()
    (tmp_path / file).write_text("SENTINEL_MUST_NOT_RUN")
    r = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_HANDOFF.ps1' -ManifestSHA256 '{pin}' -CopyReference\nexit $LASTEXITCODE",
    )
    assert r.returncode == 2 and not r.stderr and "SENTINEL" not in r.stdout
    assert json.loads(r.stdout)["failure_category"] == "HANDOFF_TRANSFER_INVALID"


def test_no_external_process_or_destructive_commands_and_parse_all(tmp_path):
    for p, b in package().items():
        (tmp_path / p).write_bytes(b)
    r = launch(
        tmp_path,
        f"""
foreach ($file in @('RUN_HANDOFF.ps1','HANDOFF_CORE.ps1','MEDIA.ps1')) {{
 $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path '{tmp_path}' $file),[ref]$tokens,[ref]$errors)
 if ($errors.Count -gt 0) {{throw 'PARSE_ERROR'}}
 foreach ($node in $ast.FindAll({{param($n) $n -is [Management.Automation.Language.CommandAst]}},$true)) {{
  if ($node.GetCommandName() -in @('docker','Start-Process','Invoke-Expression','Remove-Item','Move-Item','Copy-Item','Format-Volume','Set-Disk','Set-Partition','Invoke-WebRequest','Invoke-RestMethod')) {{throw 'FORBIDDEN_COMMAND'}}
 }}
}}
Write-Output 'PARSED_NO_EXTERNAL_OR_DESTRUCTIVE_COMMANDS'
""",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    assert "PARSED_NO_EXTERNAL_OR_DESTRUCTIVE_COMMANDS" in r.stdout


@pytest.mark.parametrize("mode", ["CheckOnly", "CopyReference"])
def test_sealed_launcher_end_to_end_with_windows_dependencies_substituted(tmp_path, mode):
    base, refs, _, usb = setup(tmp_path)
    core = tmp_path / "HANDOFF_CORE.ps1"
    # Only OS/device boundary functions are substituted; seal authenticates test bytes.
    core.write_text(
        core.read_text()
        + f"""
function Reference-AssertPrivateDirectory {{}}
function Find-LocalReferenceBase {{return '{refs}'}}
function Read-HandoffMedia {{return [pscustomobject]@{{root='D:\\';identity_sha256=('a'*64);free_bytes=[long]100000000;filesystem='exFAT';bus='USB';is_boot=$false;is_system=$false;is_offline=$false;is_readonly=$false}}}}
function Handoff-NewDirectory([object]$Media,[string]$Token) {{
 $p=Join-Path '{usb}' ('WBKANG-DEMO-Reference-20261116-'+$Token)
 Receipt-Require (-not [IO.Directory]::Exists($p)) 'HANDOFF_DESTINATION_EXISTS'
 $script:HandoffAttemptPath=$p;$null=[IO.Directory]::CreateDirectory($p);return $p
}}
"""
    )
    manifest = json.loads((tmp_path / "TRANSFER_MANIFEST.json").read_bytes())
    manifest["files"]["HANDOFF_CORE.ps1"] = hashlib.sha256(core.read_bytes()).hexdigest()
    body = json.dumps(manifest).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(body)
    before = snapshot(base)
    r = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_HANDOFF.ps1' -ManifestSHA256 '{hashlib.sha256(body).hexdigest()}' -{mode}\nexit $LASTEXITCODE",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    marker = (
        "REFERENCE_USB_CHECK_PASSED_NO_WRITES_NO_DOCKER"
        if mode == "CheckOnly"
        else "REFERENCE_USB_COPY_VERIFIED_SAFE_EJECT_PENDING_NO_DOCKER"
    )
    assert marker in r.stdout and snapshot(base) == before
    assert len(list(usb.iterdir())) == (1 if mode == "CheckOnly" else 2)


def test_production_exfat_directory_creation_and_existing_path_refusal(tmp_path):
    for p, b in package().items():
        (tmp_path / p).write_bytes(b)
    r = launch(
        tmp_path,
        f"""
. '{tmp_path}/REVIEW_CORE.ps1'
. '{tmp_path}/MEDIA.ps1'
. '{tmp_path}/HANDOFF_CORE.ps1'
# Native Linux path replaces device identity only; directory implementation unchanged.
function Assert-HandoffMediaFacts {{}}
$m=[pscustomobject]@{{root='{tmp_path}';filesystem='exFAT'}}
$p=Handoff-NewDirectory $m ('1'*32)
if (-not [IO.Directory]::Exists($p)) {{throw 'DIRECTORY_NOT_CREATED'}}
try {{Handoff-NewDirectory $m ('1'*32);throw 'EXISTING_NOT_REFUSED'}} catch {{if ($_.Exception.Message -cne 'HANDOFF_DESTINATION_EXISTS') {{throw}}}}
Write-Output 'NEW_ONLY_NO_OVERWRITE'
""",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    assert "NEW_ONLY_NO_OVERWRITE" in r.stdout


def test_source_receipt_changed_during_copy_retains_partial(tmp_path):
    base, refs, _, usb = setup(tmp_path)
    before = snapshot(base)
    v = result(
        run(
            tmp_path,
            base,
            refs,
            usb,
            extra=f"""
$script:open=${{function:Receipt-Open}};$script:stream=$null
function Receipt-Open([string]$Path) {{
 $item=& $script:open $Path
 if ($Path.StartsWith('{base}') -and [IO.Path]::GetFileName($Path) -ceq 'PRIVATE_RESOURCES.json') {{
  $item.handle.Dispose();$m=New-Object IO.MemoryStream;$m.Write($item.body,0,$item.body.Length);$m.Position=0;$item.handle=$m;$script:stream=$m
 }}
 return $item
}}
$script:write=${{function:Handoff-WriteNew}}
function Handoff-WriteNew([string]$Path,[byte[]]$Bytes) {{& $script:write $Path $Bytes;$script:stream.Position=0;$script:stream.WriteByte(32)}}
""",
        )
    )
    assert v["failure_category"] == "HANDOFF_FILE_CHANGED" and not v["automatic_retry"]
    assert len(list(next(p for p in usb.iterdir() if p.is_dir()).iterdir())) == 1
    assert snapshot(base) == before


def test_readonly_reviewer_is_identical_to_successful_windows_release():
    path = (
        ROOT
        / "artifacts/windows-demo-private-receipt-review-v2-20261116/windows-demo-private-receipt-review-v2-20261116.zip"
    )
    with zipfile.ZipFile(path) as z:
        assert package()["REVIEW_CORE.ps1"] == z.read(
            "windows-demo-private-receipt-review-v2-20261116/REVIEW_CORE.ps1"
        )
