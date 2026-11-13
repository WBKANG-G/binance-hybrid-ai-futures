"""Explicit offline PG/runtime byte-copy candidate; no service control or network.

Caller must freeze BOTH sources first. This module cannot attest writer quiescence.
Windows plan package does not invoke or distribute this mutation candidate.
"""

import hashlib
import json
import os
import stat
import tarfile
import time
from pathlib import Path
from typing import Any, BinaryIO

MAX_FILES = 50000
MAX_BYTES = 8 * 1024**3
MAX_FILE = 2 * 1024**3
MAX_MANIFEST = 16 * 1024**2
GROUPS = ("database", "runtime")
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def require(ok: bool, code: str) -> None:
    if not ok:
        raise ValueError(code)


def directory(path: Path) -> int:
    require(path.is_absolute(), "COLD_PATH_INVALID")
    fd = os.open("/", DIR_FLAGS)
    try:
        for part in path.parts[1:]:
            require(part not in {"", ".", ".."}, "COLD_PATH_INVALID")
            next_fd = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def metadata(info: os.stat_result) -> dict[str, int]:
    require(0 <= info.st_uid <= 65535 and 0 <= info.st_gid <= 65535, "COLD_OWNER_INVALID")
    require(
        stat.S_IMODE(info.st_mode) <= (0o3777 if stat.S_ISDIR(info.st_mode) else 0o777)
        and info.st_mtime_ns >= 0,
        "COLD_MODE_INVALID",
    )
    return dict(
        uid=info.st_uid, gid=info.st_gid, mode=stat.S_IMODE(info.st_mode), mtime_ns=info.st_mtime_ns
    )


def unchanged(before: os.stat_result, after: os.stat_result) -> None:
    require(
        (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
            before.st_gid,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        == (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_uid,
            after.st_gid,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ),
        "COLD_SOURCE_CHANGED",
    )


class HashReader:
    def __init__(self, source: BinaryIO, size: int, deadline: float):
        self.source, self.remaining, self.deadline = source, size, deadline
        self.digest = hashlib.sha256()

    def read(self, count: int) -> bytes:
        require(time.monotonic() <= self.deadline, "COLD_TIME_LIMIT")
        require(0 <= count <= 1024**2 and count <= self.remaining, "COLD_READ_LIMIT")
        body = self.source.read(count)
        require(len(body) == count, "COLD_SOURCE_CHANGED")
        self.remaining -= len(body)
        self.digest.update(body)
        return body


def hash_file(path: Path, deadline: float) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        require(
            stat.S_ISREG(before.st_mode)
            and before.st_nlink == 1
            and before.st_size <= MAX_BYTES + 128 * 1024**2,
            "COLD_ARCHIVE_INVALID",
        )
        result = hashlib.sha256()
        with os.fdopen(fd, "rb", closefd=False) as stream:
            while True:
                require(time.monotonic() <= deadline, "COLD_TIME_LIMIT")
                body = stream.read(1024**2)
                if not body:
                    break
                result.update(body)
        unchanged(before, os.fstat(fd))
        return result.hexdigest()
    finally:
        os.close(fd)


def write_group(source: Path, target: Path, deadline: float) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    total = 0
    source_fd = directory(source)
    root_info = os.fstat(source_fd)
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with (
            os.fdopen(fd, "wb") as output,
            tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive,
        ):

            def visit(parent: int, prefix: str, depth: int) -> None:
                nonlocal total
                require(depth <= 32, "COLD_DEPTH_LIMIT")
                require(time.monotonic() <= deadline, "COLD_TIME_LIMIT")
                start = os.fstat(parent)
                with os.scandir(parent) as iterator:
                    names: list[str] = []
                    for entry in iterator:
                        require(len(names) < MAX_FILES, "COLD_COUNT_LIMIT")
                        names.append(entry.name)
                for name in sorted(names):
                    require(len(records) < MAX_FILES, "COLD_COUNT_LIMIT")
                    require(
                        name not in {"", ".", ".."}
                        and "\\" not in name
                        and len(name.encode()) <= 255,
                        "COLD_PATH_INVALID",
                    )
                    path = prefix + "/" + name if prefix else name
                    require(len(path.encode()) <= 2048, "COLD_PATH_INVALID")
                    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    require(
                        stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode),
                        "COLD_LINK_OR_SPECIAL_FILE",
                    )
                    item: dict[str, Any] = dict(path=path, **metadata(info))
                    tar = tarfile.TarInfo(path)
                    tar.uid, tar.gid, tar.mode, tar.mtime = (
                        info.st_uid,
                        info.st_gid,
                        item["mode"],
                        info.st_mtime,
                    )
                    if stat.S_ISDIR(info.st_mode):
                        item.update(kind="directory", size=0, sha256=None)
                        tar.type = tarfile.DIRTYPE
                        archive.addfile(tar)
                        records.append(item)
                        child = os.open(name, DIR_FLAGS, dir_fd=parent)
                        try:
                            unchanged(info, os.fstat(child))
                            visit(child, path, depth + 1)
                        finally:
                            os.close(child)
                    else:
                        require(info.st_nlink == 1 and info.st_size <= MAX_FILE, "COLD_FILE_LIMIT")
                        total += info.st_size
                        require(total <= MAX_BYTES, "COLD_BYTE_LIMIT")
                        file_fd = os.open(
                            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent
                        )
                        with os.fdopen(file_fd, "rb") as stream:
                            unchanged(info, os.fstat(stream.fileno()))
                            reader = HashReader(stream, info.st_size, deadline)
                            tar.size = info.st_size
                            archive.addfile(tar, reader)  # type: ignore[arg-type]
                            require(reader.remaining == 0, "COLD_SOURCE_CHANGED")
                            unchanged(info, os.fstat(stream.fileno()))
                        item.update(
                            kind="file", size=info.st_size, sha256=reader.digest.hexdigest()
                        )
                        records.append(item)
                    unchanged(info, os.stat(name, dir_fd=parent, follow_symlinks=False))
                unchanged(start, os.fstat(parent))

            visit(source_fd, "", 0)
            unchanged(root_info, os.fstat(source_fd))
        # Tar close writes trailing blocks before the durability barrier.
        sync_fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(sync_fd)
        finally:
            os.close(sync_fd)
        return dict(
            root=metadata(root_info),
            entries=records,
            logical_bytes=total,
            archive_bytes=target.stat().st_size,
            archive_sha256=hash_file(target, deadline),
        )
    finally:
        os.close(source_fd)


def create_bundle(
    sources: dict[str, Path],
    output: Path,
    *,
    enabled: bool = False,
    sources_frozen_by_caller: bool = False,
) -> str:
    require(enabled is True and sources_frozen_by_caller is True, "COLD_EXPLICIT_FREEZE_REQUIRED")
    require(set(sources) == set(GROUPS), "COLD_SCOPE_INVALID")
    # No self-inclusion or overlapping source/output trees.
    paths = [*sources.values(), output]
    require(all(p.is_absolute() and ".." not in p.parts for p in paths), "COLD_PATH_INVALID")
    require(
        all(
            not a.is_relative_to(b) and not b.is_relative_to(a)
            for i, a in enumerate(paths)
            for b in paths[i + 1 :]
        ),
        "COLD_SCOPE_OVERLAP",
    )
    out_fd = directory(output)
    try:
        require(not os.listdir(out_fd), "COLD_OUTPUT_NOT_EMPTY")
        os.fchmod(out_fd, 0o700)
        deadline = time.monotonic() + 300
        groups = {
            group: write_group(sources[group], output / (group + ".tar"), deadline)
            for group in GROUPS
        }
        value = dict(
            protocol="COLD_OFFLINE_BYTE_COPY_CANDIDATE_V1",
            groups=groups,
            external_custody_verified=False,
            source_freeze_independently_verified=False,
            execution_approved=False,
            orders_enabled=False,
            live_trading_enabled=False,
        )
        body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        require(len(body) <= MAX_MANIFEST, "COLD_MANIFEST_LIMIT")
        fd = os.open(
            "manifest.json",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=out_fd,
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(out_fd)
        return hashlib.sha256(body).hexdigest()
    finally:
        os.close(out_fd)


def validate_manifest(value: Any) -> None:
    require(
        isinstance(value, dict)
        and set(value)
        == {
            "protocol",
            "groups",
            "external_custody_verified",
            "source_freeze_independently_verified",
            "execution_approved",
            "orders_enabled",
            "live_trading_enabled",
        },
        "COLD_MANIFEST_INVALID",
    )
    require(
        value["protocol"] == "COLD_OFFLINE_BYTE_COPY_CANDIDATE_V1"
        and set(value["groups"]) == set(GROUPS),
        "COLD_MANIFEST_INVALID",
    )
    require(
        all(
            value[k] is False
            for k in (
                "external_custody_verified",
                "source_freeze_independently_verified",
                "execution_approved",
                "orders_enabled",
                "live_trading_enabled",
            )
        ),
        "COLD_MANIFEST_INVALID",
    )
    for group in value["groups"].values():
        require(
            set(group) == {"root", "entries", "logical_bytes", "archive_bytes", "archive_sha256"},
            "COLD_MANIFEST_INVALID",
        )
        require(
            isinstance(group["entries"], list) and len(group["entries"]) <= MAX_FILES,
            "COLD_MANIFEST_INVALID",
        )
        seen = set()
        for item in group["entries"]:
            require(
                set(item) == {"path", "uid", "gid", "mode", "mtime_ns", "kind", "size", "sha256"},
                "COLD_MANIFEST_INVALID",
            )
            name = item["path"]
            require(
                isinstance(name, str)
                and bool(name)
                and not name.startswith("/")
                and "\\" not in name
                and all(p not in {"", ".", ".."} for p in name.split("/"))
                and name not in seen
                and len(name.encode()) <= 2048,
                "COLD_PATH_INVALID",
            )
            seen.add(name)
            require(
                item["kind"] in {"directory", "file"}
                and type(item["size"]) is int
                and 0 <= item["size"] <= MAX_FILE,
                "COLD_MANIFEST_INVALID",
            )
            require(item["kind"] == "file" or item["size"] == 0, "COLD_MANIFEST_INVALID")
        require(set(group["root"]) == {"uid", "gid", "mode", "mtime_ns"}, "COLD_MANIFEST_INVALID")
        for item in [group["root"], *group["entries"]]:
            for k, limit in (
                ("uid", 65535),
                ("gid", 65535),
                (
                    "mode",
                    0o3777 if item is group["root"] or item.get("kind") == "directory" else 0o777,
                ),
                ("mtime_ns", 2**63 - 1),
            ):
                require(type(item[k]) is int and 0 <= item[k] <= limit, "COLD_MANIFEST_INVALID")


def verify_restored_metadata(info: os.stat_result, expected: dict[str, Any]) -> None:
    require(
        metadata(info) == {key: expected[key] for key in ("uid", "gid", "mode", "mtime_ns")},
        "COLD_RESTORE_METADATA_MISMATCH",
    )


def restore_bundle(
    bundle: Path,
    destinations: dict[str, Path],
    *,
    retained_manifest_sha256: str,
    enabled: bool = False,
) -> dict[str, int]:
    require(enabled is True, "COLD_EXPLICIT_RESTORE_REQUIRED")
    require(
        isinstance(retained_manifest_sha256, str)
        and len(retained_manifest_sha256) == 64
        and all(c in "0123456789abcdef" for c in retained_manifest_sha256),
        "COLD_RETAINED_HASH_REQUIRED",
    )
    require(set(destinations) == set(GROUPS), "COLD_SCOPE_INVALID")
    all_paths = [bundle, *destinations.values()]
    require(all(p.is_absolute() and ".." not in p.parts for p in all_paths), "COLD_PATH_INVALID")
    require(
        all(
            not a.is_relative_to(b) and not b.is_relative_to(a)
            for i, a in enumerate(all_paths)
            for b in all_paths[i + 1 :]
        ),
        "COLD_SCOPE_OVERLAP",
    )
    bundle_fd = directory(bundle)
    fd = os.open("manifest.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=bundle_fd)
    try:
        info = os.fstat(fd)
        require(
            stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= MAX_MANIFEST,
            "COLD_MANIFEST_INVALID",
        )
        with os.fdopen(fd, "rb", closefd=False) as stream:
            body = stream.read(MAX_MANIFEST + 1)
        unchanged(info, os.fstat(fd))
    finally:
        os.close(fd)
        os.close(bundle_fd)
    require(
        hashlib.sha256(body).hexdigest() == retained_manifest_sha256, "COLD_MANIFEST_HASH_MISMATCH"
    )
    manifest = json.loads(body)
    validate_manifest(manifest)
    deadline = time.monotonic() + 300
    # Verify BOTH archives and all headers before writing either destination.
    for group in GROUPS:
        detail = manifest["groups"][group]
        path = bundle / (group + ".tar")
        require(hash_file(path, deadline) == detail["archive_sha256"], "COLD_ARCHIVE_HASH_MISMATCH")
        require(path.stat().st_size == detail["archive_bytes"], "COLD_ARCHIVE_SIZE_MISMATCH")
        expected = {r["path"]: r for r in detail["entries"]}
        seen = set()
        with tarfile.open(path, "r:") as archive:
            for member in archive:
                require(
                    member.name in expected and member.name not in seen,
                    "COLD_ARCHIVE_MEMBER_INVALID",
                )
                item = expected[member.name]
                require(
                    (member.isfile() and item["kind"] == "file")
                    or (member.isdir() and item["kind"] == "directory"),
                    "COLD_ARCHIVE_MEMBER_INVALID",
                )
                require(
                    member.size == item["size"]
                    and member.mode == item["mode"]
                    and member.uid == item["uid"]
                    and member.gid == item["gid"],
                    "COLD_ARCHIVE_MEMBER_INVALID",
                )
                seen.add(member.name)
                require(len(seen) <= MAX_FILES, "COLD_COUNT_LIMIT")
            require(seen == set(expected), "COLD_ARCHIVE_MEMBER_INVALID")
    handles = {}
    try:
        for group, destination in destinations.items():
            handles[group] = directory(destination)
            require(not os.listdir(handles[group]), "COLD_DESTINATION_NOT_EMPTY")
        counts = {}
        for group in GROUPS:
            detail = manifest["groups"][group]
            entries = {r["path"]: r for r in detail["entries"]}
            with tarfile.open(bundle / (group + ".tar"), "r:") as archive:
                for member in archive:
                    require(time.monotonic() <= deadline, "COLD_TIME_LIMIT")
                    item = entries[member.name]
                    parent = os.dup(handles[group])
                    try:
                        parts = member.name.split("/")
                        for part in parts[:-1]:
                            next_fd = os.open(part, DIR_FLAGS, dir_fd=parent)
                            os.close(parent)
                            parent = next_fd
                        if item["kind"] == "directory":
                            os.mkdir(parts[-1], 0o700, dir_fd=parent)
                        else:
                            source = archive.extractfile(member)
                            require(source is not None, "COLD_ARCHIVE_MEMBER_INVALID")
                            assert source is not None
                            fd = os.open(
                                parts[-1],
                                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                0o600,
                                dir_fd=parent,
                            )
                            reader = HashReader(source, item["size"], deadline)  # type: ignore[arg-type]
                            with os.fdopen(fd, "wb") as stream:
                                while reader.remaining:
                                    stream.write(reader.read(min(65536, reader.remaining)))
                                require(
                                    reader.digest.hexdigest() == item["sha256"],
                                    "COLD_FILE_HASH_MISMATCH",
                                )
                                stream.flush()
                                os.fchown(stream.fileno(), item["uid"], item["gid"])
                                os.fchmod(stream.fileno(), item["mode"])
                                os.utime(stream.fileno(), ns=(item["mtime_ns"], item["mtime_ns"]))
                                os.fsync(stream.fileno())
                                verify_restored_metadata(os.fstat(stream.fileno()), item)
                            source.close()
                    finally:
                        os.close(parent)
            # Restore directory metadata last, deepest first, preserving access via descriptors.
            for item in reversed(detail["entries"]):
                if item["kind"] == "directory":
                    directory_fd = directory(destinations[group] / item["path"])
                    try:
                        os.fchown(directory_fd, item["uid"], item["gid"])
                        os.fchmod(directory_fd, item["mode"])
                        os.utime(directory_fd, ns=(item["mtime_ns"], item["mtime_ns"]))
                        os.fsync(directory_fd)
                        verify_restored_metadata(os.fstat(directory_fd), item)
                    finally:
                        os.close(directory_fd)
            root = detail["root"]
            os.fchown(handles[group], root["uid"], root["gid"])
            os.fchmod(handles[group], root["mode"])
            os.utime(handles[group], ns=(root["mtime_ns"], root["mtime_ns"]))
            os.fsync(handles[group])
            verify_restored_metadata(os.fstat(handles[group]), root)
            counts[group] = sum(e["kind"] == "file" for e in detail["entries"])
        return counts
    finally:
        for descriptor in handles.values():
            os.close(descriptor)
