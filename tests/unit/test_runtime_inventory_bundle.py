"""Inventory package is minimal and pinned; worker does not import app code."""

import hashlib
import json
import os
import runpy
import zipfile
from pathlib import Path

import pytest

from scripts.build_demo_read_review_bundle import archive
from scripts.build_windows_runtime_inventory_bundle import BASELINE, package_files, read_source

ROOT = Path(__file__).resolve().parents[2]
WORKER = runpy.run_path(
    str(ROOT / "tools/windows_demo_runtime_inventory/worker.py"), run_name="inventory_unit"
)


def read_worktree(ref, name):
    if ref == BASELINE:
        return read_source(ref, name)
    return (ROOT / name).read_bytes()


def package():
    release = os.environ.get("INVENTORY_RELEASE_ZIP")
    if release:
        prefix = "windows-demo-runtime-inventory-20261113/"
        with zipfile.ZipFile(release) as bundle:
            return {
                name[len(prefix) :]: bundle.read(name)
                for name in bundle.namelist()
                if name.startswith(prefix)
            }
    return package_files("a" * 40, read=read_worktree)


def test_bundle_hashes_and_entrypoint_are_deterministic_and_install_free():
    files = package()
    assert archive(files) == archive(package())
    assert set(files) == {
        "worker.py",
        "INVENTORY.ps1",
        "README_KO.md",
        "INVENTORY_CONFIG.json",
        "TRANSFER_MANIFEST.json",
        "RUN_INVENTORY.cmd",
    }
    manifest = json.loads(files["TRANSFER_MANIFEST.json"])
    assert all(
        hashlib.sha256(files[name]).hexdigest() == value
        for name, value in manifest["files"].items()
    )
    assert (
        hashlib.sha256(files["TRANSFER_MANIFEST.json"]).hexdigest().encode()
        in files["RUN_INVENTORY.cmd"]
    )
    cfg = json.loads(files["INVENTORY_CONFIG.json"])
    WORKER["config_valid"](cfg)
    assert len(cfg["baseline_files"]) == 89 and len(cfg["candidate_files"]) == 9
    assert b"python -I -B -" in files["INVENTORY.ps1"]
    for forbidden in (
        b"docker cp",
        b"docker compose",
        b"docker restart",
        b"docker build",
        b"Config.Env",
        b"demo_key_session",
    ):
        assert forbidden not in files["INVENTORY.ps1"]
    assert b"from packages" not in files["worker.py"] and b"from services" not in files["worker.py"]


def test_unknown_configuration_is_rejected_without_touching_env_or_files(tmp_path):
    report, status = WORKER["collect"](None, root=tmp_path)
    assert status == 2 and report["failure_category"] == "INPUT_OR_STATE_INVALID"
    assert not report["database_configuration_used"]


@pytest.mark.parametrize("kind", ["path", "hash", "account", "extra"])
def test_config_rejects_paths_or_data_outside_fixed_contract(kind):
    config = json.loads(package()["INVENTORY_CONFIG.json"])
    if kind == "path":
        config["baseline_files"]["../private-key"] = "a" * 64
    elif kind == "hash":
        config["baseline_files"][next(iter(config["baseline_files"]))] = "PRIVATE_VALUE"
    elif kind == "account":
        config["account"] = "LIVE-other"
    else:
        config["api_key"] = "PRIVATE_VALUE"
    with pytest.raises(ValueError):
        WORKER["config_valid"](config)


@pytest.mark.parametrize(
    "kind", ["regular", "missing", "missing-parent", "symlink", "directory", "oversized"]
)
def test_source_file_hashes_are_bounded_and_never_follow_links(tmp_path, kind):
    target = tmp_path / "packages"
    target.mkdir()
    path = target / "test.py"
    if kind == "regular":
        path.write_bytes(b"synthetic code")
        assert (
            WORKER["digest_file"](tmp_path, "packages/test.py")
            == hashlib.sha256(b"synthetic code").hexdigest()
        )
    elif kind in {"missing", "missing-parent"}:
        assert (
            WORKER["digest_file"](
                tmp_path, "packages/test.py" if kind == "missing" else "services/test.py"
            )
            is None
        )
    else:
        if kind == "symlink":
            path.symlink_to(tmp_path / "private")
        elif kind == "directory":
            path.mkdir()
        else:
            path.write_bytes(b"x" * 2097153)
        with pytest.raises(ValueError):
            WORKER["digest_file"](tmp_path, "packages/test.py")
