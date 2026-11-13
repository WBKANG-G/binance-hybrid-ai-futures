"""Offline candidate preserves bytes and fails closed on links/tampering/partial copies."""

import hashlib
import json
import os
import runpy
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKER = runpy.run_path(str(ROOT / "tools/cold_backup_candidate.py"), run_name="cold_test")


@pytest.fixture
def case(tmp_path):
    sources = {g: tmp_path / g for g in ("database", "runtime")}
    for root in sources.values():
        root.mkdir()
        (root / "nested").mkdir()
        (root / "nested" / "PRIVATE_NAME").write_bytes(b"PRIVATE_BODY\x00\xff" * 4000)
        (root / "zero").touch()
    output = tmp_path / "backup"
    output.mkdir()
    destinations = {g: tmp_path / (g + "-restored") for g in sources}
    for root in destinations.values():
        root.mkdir()
    return sources, output, destinations


def backup(case):
    return WORKER["create_bundle"](case[0], case[1], enabled=True, sources_frozen_by_caller=True)


def restore(case, digest):
    return WORKER["restore_bundle"](case[1], case[2], retained_manifest_sha256=digest, enabled=True)


def listing(root):
    return {
        str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mode & 0o777, p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }


def test_roundtrip_preserves_bytes_metadata_and_sources(case):
    before = {g: listing(p) for g, p in case[0].items()}
    digest = backup(case)
    assert restore(case, digest) == dict(database=2, runtime=2)
    for group, path in case[2].items():
        assert listing(path) == before[group] and listing(case[0][group]) == before[group]
    manifest = json.loads((case[1] / "manifest.json").read_bytes())
    assert manifest["source_freeze_independently_verified"] is False
    assert manifest["execution_approved"] is False
    assert case[1].stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in case[1].iterdir())


@pytest.mark.parametrize("enabled,frozen", [(False, False), (True, False), (False, True)])
def test_backup_defaults_deny_before_source_or_output_access(case, enabled, frozen):
    with pytest.raises(ValueError, match="COLD_EXPLICIT_FREEZE_REQUIRED"):
        WORKER["create_bundle"](case[0], case[1], enabled=enabled, sources_frozen_by_caller=frozen)
    assert not list(case[1].iterdir())


@pytest.mark.parametrize(
    "kind",
    [
        "symlink",
        "hardlink",
        "fifo",
        "link-parent",
        "setuid",
        "overlap",
        "nonempty-output",
        "count-limit",
        "byte-limit",
        "file-limit",
    ],
)
def test_unsafe_sources_or_scope_never_produce_complete_manifest(case, monkeypatch, kind):
    root = case[0]["database"]
    if kind == "symlink":
        (root / "link").symlink_to(root / "zero")
    elif kind == "hardlink":
        os.link(root / "zero", root / "alias")
    elif kind == "fifo":
        os.mkfifo(root / "pipe")
    elif kind == "link-parent":
        link = root.parent / "link"
        link.symlink_to(root, target_is_directory=True)
        case[0]["database"] = link
    elif kind == "setuid":
        (root / "zero").chmod(0o4600)
    elif kind == "overlap":
        case[0]["database"] = case[1]
    elif kind == "nonempty-output":
        (case[1] / "existing").touch()
    else:
        key = {"count-limit": "MAX_FILES", "byte-limit": "MAX_BYTES", "file-limit": "MAX_FILE"}[
            kind
        ]
        monkeypatch.setitem(WORKER["create_bundle"].__globals__, key, 1)
    with pytest.raises((ValueError, OSError)):
        backup(case)
    assert not (case[1] / "manifest.json").exists()


@pytest.mark.parametrize(
    "kind",
    [
        "manifest",
        "database",
        "runtime",
        "retained-hash",
        "destination-nonempty",
        "disabled",
        "traversal-header",
    ],
)
def test_restore_rejects_corruption_before_writing_either_destination(case, kind):
    digest = backup(case)
    if kind == "manifest":
        with (case[1] / "manifest.json").open("ab") as stream:
            stream.write(b"changed")
    elif kind in {"database", "runtime"}:
        with (case[1] / (kind + ".tar")).open("r+b") as stream:
            stream.write(b"corrupt")
    elif kind == "retained-hash":
        digest = "f" * 64
    elif kind == "destination-nonempty":
        (case[2]["runtime"] / "existing").touch()
    elif kind == "traversal-header":
        import tarfile

        path = case[1] / "runtime.tar"
        with tarfile.open(path, "w") as archive:
            member = tarfile.TarInfo("../escape")
            member.size = 0
            archive.addfile(member)
        body = json.loads((case[1] / "manifest.json").read_bytes())
        body["groups"]["runtime"]["archive_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        body["groups"]["runtime"]["archive_bytes"] = path.stat().st_size
        raw = json.dumps(body).encode()
        (case[1] / "manifest.json").write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):
        WORKER["restore_bundle"](
            case[1], case[2], retained_manifest_sha256=digest, enabled=kind != "disabled"
        )
    assert not list(case[2]["database"].iterdir())
    assert set(p.name for p in case[2]["runtime"].iterdir()) <= {"existing"}


def test_source_change_retains_partial_archive_without_publishing_manifest(case, monkeypatch):
    real = WORKER["HashReader"].read
    fired = []

    def changing(reader, count):
        body = real(reader, count)
        if not fired:
            fired.append(True)
            p = case[0]["database"] / "nested" / "PRIVATE_NAME"
            with p.open("ab") as stream:
                stream.write(b"changed")
        return body

    monkeypatch.setattr(WORKER["HashReader"], "read", changing)
    with pytest.raises(ValueError, match="COLD_SOURCE_CHANGED"):
        backup(case)
    assert fired and (case[1] / "database.tar").exists()
    assert not (case[1] / "manifest.json").exists()


def test_expired_reader_does_not_read_body():
    import io

    source = io.BytesIO(b"private")
    reader = WORKER["HashReader"](source, 7, time.monotonic() - 1)
    with pytest.raises(ValueError, match="COLD_TIME_LIMIT"):
        reader.read(7)
    assert source.tell() == 0


def test_postgres_style_sticky_directory_mode_is_preserved(case):
    case[0]["database"].chmod(0o1777)
    digest = backup(case)
    restore(case, digest)
    assert case[2]["database"].stat().st_mode & 0o7777 == 0o1777


def test_source_root_cannot_include_backup_output(case):
    case[0]["database"] = Path("/")
    with pytest.raises(ValueError, match="COLD_SCOPE_OVERLAP"):
        backup(case)
    assert not list(case[1].iterdir())


def test_failed_fsync_never_returns_a_retained_manifest_or_retries(case, monkeypatch):
    def fail(fd):
        raise OSError("synthetic storage failure")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError):
        backup(case)
    assert (case[1] / "database.tar").exists()
    assert not (case[1] / "manifest.json").exists()


def test_restore_rejects_a_linked_destination_before_writing_other_group(case):
    digest = backup(case)
    original = case[2]["runtime"]
    link = original.parent / "destination-link"
    link.symlink_to(original, target_is_directory=True)
    case[2]["runtime"] = link
    with pytest.raises(OSError):
        restore(case, digest)
    assert not list(case[2]["database"].iterdir())
    assert not list(original.iterdir())
