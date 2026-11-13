"""Read-only, bounded metadata walk and pinned preflight package contract."""

import hashlib
import json
import os
import runpy
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import build_windows_backup_preflight_bundle as builder
from scripts.build_demo_read_review_bundle import archive

ROOT = Path(__file__).resolve().parents[2]
WORKER = runpy.run_path(str(ROOT / builder.PREFIX / "worker.py"), run_name="preflight_unit")


def package():
    release = os.environ.get("PREFLIGHT_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {p[len(builder.FOLDER) + 1 :]: z.read(p) for p in z.namelist()}
    real_git = builder.git

    def source(*args):
        if args[0] == "show":
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real_git(*args)

    with patch.object(builder, "git", source):
        return builder.package_files("HEAD")


def config():
    return json.loads(package()["PREFLIGHT_CONFIG.json"])


def test_pinned_package_is_deterministic_and_has_only_streamed_read_entrypoint():
    files = package()
    assert set(files) == {
        "worker.py",
        "PREFLIGHT.ps1",
        "README_KO.md",
        "PREFLIGHT_CONFIG.json",
        "REPORT_SCHEMA.json",
        "TRANSFER_MANIFEST.json",
        "RUN_PREFLIGHT.cmd",
    }
    assert archive(files) == archive(package())
    manifest = json.loads(files["TRANSFER_MANIFEST.json"])
    assert set(manifest["files"]) == set(files) - {"TRANSFER_MANIFEST.json", "RUN_PREFLIGHT.cmd"}
    for p, h in manifest["files"].items():
        assert hashlib.sha256(files[p]).hexdigest() == h
    assert (
        hashlib.sha256(files["TRANSFER_MANIFEST.json"]).hexdigest().encode()
        in files["RUN_PREFLIGHT.cmd"]
    )
    assert b"python -I -B -" in files["PREFLIGHT.ps1"]
    for text in (
        b"docker cp",
        b"docker build",
        b"docker compose",
        b"docker restart",
        b"Config.Env",
        b"demo_key_session",
    ):
        assert text not in files["PREFLIGHT.ps1"]
    for text in (b"from packages", b"from services", b"BINANCE_", b"read_bytes(", b"read_text("):
        assert text not in files["worker.py"]
    assert set(WORKER["base_report"]()) == set(json.loads(files["REPORT_SCHEMA.json"])["fields"])


@pytest.mark.parametrize("change", ["account", "root", "reference", "extra", "none"])
def test_invalid_config_denied_before_database_or_storage(change, tmp_path, monkeypatch):
    cfg = config()
    if change == "none":
        cfg = None
    elif change == "extra":
        cfg["private_key"] = "PRIVATE_SENTINEL"
    else:
        cfg[
            {"account": "account", "root": "storage_root", "reference": "source_reference"}[change]
        ] = "PRIVATE_SENTINEL"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    report, code = WORKER["collect"](cfg, storage_root=tmp_path)
    assert code == 2 and report["failure_category"] == "PREFLIGHT_INPUT_INVALID"
    assert not report["database_configuration_used"] and report["storage"] is None
    assert "PRIVATE_SENTINEL" not in json.dumps(report)


def test_walk_reads_metadata_without_opening_files_or_leaking_names(tmp_path, monkeypatch):
    shared = tmp_path / "demo-runtime-20261111" / "shared-weight"
    shared.mkdir(parents=True)
    (shared / "PRIVATE_FILENAME-wal").write_bytes(b"PRIVATE_BODY" * 1000)
    (tmp_path / "PRIVATE_EXTRA_FILE").write_bytes(b"PRIVATE_OTHER_BODY")
    before = {
        p: (p.stat().st_size, p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file()
    }
    actual_open = os.open
    opened = []

    def open_dirs(path, flags, *args, **kwargs):
        assert flags & os.O_DIRECTORY
        opened.append(path)
        return actual_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", open_dirs)
    report = WORKER["storage_metadata"](tmp_path, time.monotonic() + 3)
    assert report["files"] == 2 and report["sqlite_wal_files"] == 1
    assert report["groups"]["SHARED_WEIGHT"]["files"] == 1
    assert report["groups"]["OTHER"]["files"] == 1
    assert "PRIVATE" not in json.dumps(report)
    assert opened and all((p.stat().st_size, p.stat().st_mtime_ns) == v for p, v in before.items())


@pytest.mark.parametrize(
    "kind",
    [
        "symlink-file",
        "symlink-root",
        "symlink-parent",
        "hardlink",
        "fifo",
        "entry-limit",
        "depth-limit",
        "expired",
    ],
)
def test_walk_denies_unsafe_or_unbounded_scope_without_following_files(tmp_path, kind):
    scope = tmp_path / "scope"
    scope.mkdir()
    (scope / "a").write_bytes(b"PRIVATE_BODY")
    if kind == "symlink-file":
        (scope / "link").symlink_to(scope / "a")
    elif kind == "symlink-root":
        (tmp_path / "link").symlink_to(scope, target_is_directory=True)
        scope = tmp_path / "link"
    elif kind == "symlink-parent":
        (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
        scope = tmp_path / "link" / "scope"
    elif kind == "hardlink":
        os.link(scope / "a", scope / "b")
    elif kind == "fifo":
        os.mkfifo(scope / "pipe")
    elif kind == "entry-limit":
        for i in range(1024):
            (scope / f"x{i}").touch()
    elif kind == "depth-limit":
        p = scope
        for _ in range(14):
            p = p / "nested"
            p.mkdir()
    deadline = time.monotonic() - 1 if kind == "expired" else time.monotonic() + 4
    with pytest.raises((ValueError, OSError)):
        WORKER["storage_metadata"](scope, deadline)


def test_walk_detects_file_metadata_change(tmp_path, monkeypatch):
    path = tmp_path / "a"
    path.write_bytes(b"original")
    original = os.stat
    count = 0

    def racing(name, *args, **kwargs):
        nonlocal count
        info = original(name, *args, **kwargs)
        if name == "a":
            count += 1
            if count == 1:
                path.write_bytes(b"changed_size")
        return info

    monkeypatch.setattr(os, "stat", racing)
    with pytest.raises(ValueError, match="PREFLIGHT_STORAGE_CHANGED"):
        WORKER["storage_metadata"](tmp_path, time.monotonic() + 4)
