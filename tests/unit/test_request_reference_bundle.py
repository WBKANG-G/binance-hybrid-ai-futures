"""Offline native-tar reader tests with synthetic archives and immutable sources."""

import hashlib
import io
import json
import os
import tarfile
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_request_reference_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch
from tests.unit.test_private_receipt_review_v2 import snapshot

pytestmark = pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")


def h(body):
    return hashlib.sha256(body).hexdigest()


def package():
    release = os.getenv("REQUEST_REFERENCE_RELEASE_ZIP")
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
    orders = {}
    requests = {}
    plan = []
    algos = {}
    for i, role in enumerate(["ENTRY", "ENTRY", "STOP_LOSS", "EXIT"]):
        identifier = "PRIVATE_CLIENT_SENTINEL_" + str(i)
        state = "SUBMITTING" if role == "EXIT" else "UNKNOWN"
        requests[identifier] = state
        fields = dict(
            client_id="unrelated-" + str(i),
            algo_id=None,
            exit_id=None,
            server_order_id=str(28668119491 + i),
            quantity="0.0007",
            position_state="CLOSED",
            signal=dict(
                environment="DEMO",
                symbol="BTCUSDT",
                direction="LONG",
                signal_closed_candle_at="2026-11-06T06:45:00Z",
                suggested_limit_price="75725.10",
                initial_stop_price="75344.40",
            ),
            sensitive_unknown="NEVER_OUTPUT_SENSITIVE_SENTINEL",
        )
        fields[dict(ENTRY="client_id", STOP_LOSS="algo_id", EXIT="exit_id")[role]] = identifier
        orders[str(i)] = fields
        if role == "STOP_LOSS":
            algos[identifier] = "1000000275329189"
        plan.append(dict(role=role, state=state, request_reference_sha256=h(identifier.encode())))
    for i in range(4, 34):
        orders[str(i)] = dict(client_id="unrelated-" + str(i), algo_id=None, exit_id=None)
    state = dict(mode="DEMO", orders=orders, requests=requests, algo_server_ids=algos)
    return {
        "policy": "OPERATOR_ATTESTED_ACCOUNTED_DAY13_ADOPTION_V1",
        "old_row": {"account_id": "DEMO-WINDOWS-ISOLATED-BTC-V1", "state": state},
    }, dict(
        account="DEMO-WINDOWS-ISOLATED-BTC-V1",
        policy="HISTORICAL_ORIGINAL_ID_EVIDENCE_20261113_V1",
        plan=plan,
    )


def setup(tmp_path, mutate=None):
    adopt, plan = records()
    if mutate:
        mutate(adopt, plan)
    root = tmp_path / "data" / "WBKANG-DEMO-Backups"
    folder = root / ("cold-20261114-" + "1" * 32)
    folder.mkdir(parents=True)
    archive = folder / "runtime.tar"
    entries = []
    bodies = {
        "demo-runtime-20261111/accounted-day13-adoption-11111111-1111-1111-1111-111111111111/intent.json": adopt,
        "demo-runtime-20261111/historical-request-evidence-22222222-2222-2222-2222-222222222222/intent.json": plan,
    }
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
    for p, b in package().items():
        (tmp_path / p).write_bytes(b)
    return root, folder, h(manifest)


def run(tmp_path, root, pin, extra=""):
    r = launch(
        tmp_path,
        f"""
. '{tmp_path}/INSPECT_CORE.ps1'
# Windows tar.exe is supplied by native Linux tar with identical read-only flags.
function tar.exe {{& tar @args}}
function docker {{throw 'DOCKER_MUST_NOT_RUN'}}
{extra}
Invoke-RequestReferenceInspection '{root}' '{pin}'|ConvertTo-Json -Depth 8 -Compress
""",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    return json.loads(r.stdout), r.stdout


def test_native_archive_read_matches_four_requests_and_writes_nothing(tmp_path):
    root, _, pin = setup(tmp_path)
    before = snapshot(root)
    v, out = run(tmp_path, root, pin)
    assert (
        v["request_reference_inspection"] == "PINNED_PRIOR_LEDGER_FOUR_REQUEST_REFERENCES_READ"
    ), v
    assert v["request_count"] == 4 and snapshot(root) == before
    assert "PRIVATE_CLIENT_SENTINEL" not in out and "NEVER_OUTPUT_SENSITIVE_SENTINEL" not in out
    byrole = {
        role: [r for r in v["requests"] if r["role"] == role]
        for role in ["ENTRY", "STOP_LOSS", "EXIT"]
    }
    assert len(byrole["ENTRY"]) == 2 and byrole["STOP_LOSS"][0]["server_id"] == "1000000275329189"
    assert byrole["EXIT"][0]["server_id"] is None
    assert byrole["EXIT"][0]["entry_server_order_id"] == "28668119494"
    for k in [
        "files_written",
        "archive_extracted_to_disk",
        "raw_client_ids_displayed",
        "credentials_accessed",
        "docker_used",
        "services_started",
        "current_database_state_verified",
        "remote_resolution_verified",
        "execution_approved",
        "orders_enabled",
        "live_trading_enabled",
    ]:
        assert v[k] is False


@pytest.mark.parametrize(
    "change",
    [
        "plan_hash",
        "plan_role",
        "request_state",
        "order_link",
        "order_count",
        "environment",
        "symbol",
        "server_id",
        "amount",
        "timestamp",
    ],
)
def test_invalid_snapshot_or_plan_denied_without_raw_output(tmp_path, change):
    def mutate(a, p):
        s = a["old_row"]["state"]
        o = s["orders"]["0"]
        if change == "plan_hash":
            p["plan"][0]["request_reference_sha256"] = "0" * 64
        elif change == "plan_role":
            p["plan"][0]["role"] = "EXIT"
        elif change == "request_state":
            p["plan"][0]["state"] = "FILLED"
        elif change == "order_link":
            o["client_id"] = "other"
        elif change == "order_count":
            del s["orders"]["33"]
        elif change == "environment":
            o["signal"]["environment"] = "LIVE"
        elif change == "symbol":
            o["signal"]["symbol"] = "ETHUSDT"
        elif change == "server_id":
            o["server_order_id"] = "SECRET_MUST_NOT_OUTPUT"
        elif change == "amount":
            o["quantity"] = "SECRET_MUST_NOT_OUTPUT"
        elif change == "timestamp":
            o["signal"]["signal_closed_candle_at"] = "SECRET_MUST_NOT_OUTPUT"

    root, _, pin = setup(tmp_path, mutate)
    before = snapshot(root)
    v, out = run(tmp_path, root, pin)
    assert v["request_reference_inspection"] == "BLOCKED_NO_WRITES"
    assert "SECRET_MUST_NOT_OUTPUT" not in out and "PRIVATE_CLIENT_SENTINEL" not in out
    assert snapshot(root) == before


@pytest.mark.parametrize("which", ["manifest", "archive"])
def test_corruption_denied_before_tar_reads(tmp_path, which):
    root, folder, pin = setup(tmp_path)
    p = folder / ("manifest.json" if which == "manifest" else "runtime.tar")
    p.write_bytes(p.read_bytes() + b" ")
    v, _ = run(tmp_path, root, pin, "function Inspect-ReadArchive {throw 'MUST_NOT_READ'}")
    assert v["failure_category"] == (
        "INSPECT_BACKUP_NOT_UNIQUE" if which == "manifest" else "INSPECT_ARCHIVE_HASH_MISMATCH"
    )


def test_duplicate_pinned_backup_refused(tmp_path):
    import shutil

    root, folder, pin = setup(tmp_path)
    shutil.copytree(folder, root / ("cold-20261114-" + "2" * 32))
    v, _ = run(tmp_path, root, pin)
    assert v["failure_category"] == "INSPECT_BACKUP_NOT_UNIQUE"


def test_missing_server_id_is_null_not_an_invented_mapping(tmp_path):
    def mutate(a, p):
        a["old_row"]["state"]["algo_server_ids"] = {}
        a["old_row"]["state"]["orders"]["0"]["server_order_id"] = None

    root, _, pin = setup(tmp_path, mutate)
    v, _ = run(tmp_path, root, pin)
    assert v["request_count"] == 4
    assert next(r for r in v["requests"] if r["role"] == "STOP_LOSS")["server_id"] is None
    assert sum(r["server_id"] is None for r in v["requests"]) == 3


@pytest.mark.parametrize("file", ["INSPECT_CORE.ps1", "README_KO.md"])
def test_sealed_launcher_refuses_tampering(tmp_path, file):
    for p, b in package().items():
        (tmp_path / p).write_bytes(b)
    pin = h((tmp_path / "TRANSFER_MANIFEST.json").read_bytes())
    (tmp_path / file).write_text("SENTINEL_MUST_NOT_RUN")
    r = launch(
        tmp_path, f"& '{tmp_path}/RUN_INSPECT.ps1' -ManifestSHA256 '{pin}'\nexit $LASTEXITCODE"
    )
    assert r.returncode == 2 and not r.stderr and "SENTINEL" not in r.stdout
    assert json.loads(r.stdout)["failure_category"] == "INSPECT_TRANSFER_INVALID"


def test_sealed_launcher_success_with_only_fixture_pin_and_tar_name_substituted(tmp_path):
    root, _, pin = setup(tmp_path)
    launcher = tmp_path / "RUN_INSPECT.ps1"
    launcher.write_text(
        launcher.read_text().replace(
            "2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d", pin
        )
    )
    manifest = json.loads((tmp_path / "TRANSFER_MANIFEST.json").read_bytes())
    manifest["files"]["RUN_INSPECT.ps1"] = h(launcher.read_bytes())
    body = json.dumps(manifest).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(body)
    before = snapshot(root)
    r = launch(
        tmp_path,
        f"function tar.exe {{& tar @args}}\n& '{tmp_path}/RUN_INSPECT.ps1' -ManifestSHA256 '{h(body)}'\nexit $LASTEXITCODE",
    )
    assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
    assert "FOUR_PRIOR_REQUEST_REFERENCES_READ_NO_WRITES_NO_DOCKER" in r.stdout
    assert snapshot(root) == before and "PRIVATE_CLIENT_SENTINEL" not in r.stdout


def test_account_scope_mismatch_is_not_an_identity_proof(tmp_path):
    root, _, pin = setup(tmp_path, lambda a, p: p.update(account="DIFFERENT_ACCOUNT"))
    v, out = run(tmp_path, root, pin)
    assert v["failure_category"] == "INSPECT_ACCOUNT_LINK_INVALID"
    assert "PRIVATE_CLIENT_SENTINEL" not in out


def test_archive_read_failure_is_redacted_and_no_partial_rows_are_returned(tmp_path):
    root, _, pin = setup(tmp_path)
    v, out = run(
        tmp_path, root, pin, "function Inspect-ReadArchive {throw 'PRIVATE_CLIENT_SENTINEL'}"
    )
    assert v["request_reference_inspection"] == "BLOCKED_NO_WRITES"
    assert "PRIVATE_CLIENT_SENTINEL" not in out and "requests" not in v


def test_parsed_scripts_have_no_network_write_or_service_commands(tmp_path):
    for p, b in package().items():
        (tmp_path / p).write_bytes(b)
    r = launch(
        tmp_path,
        f"""
foreach ($file in @('RUN_INSPECT.ps1','INSPECT_CORE.ps1')) {{
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
