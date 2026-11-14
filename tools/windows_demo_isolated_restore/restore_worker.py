"""Restore validated archives to fresh clone mounts; adjust PID only on the clone."""

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any


def restore(config: Any, archive_source: bytes) -> dict[str, Any]:
    if not isinstance(config, dict) or set(config) != {"manifest_sha256"}:
        raise ValueError("RESTORE_CONFIG_INVALID")
    namespace: dict[str, Any] = {"__name__": "isolated_restore_library"}
    exec(compile(archive_source, "<cold-archive>", "exec"), namespace)
    manifest_path = Path("/bundle/manifest.json")
    if manifest_path.stat().st_size > namespace["MAX_MANIFEST"]:
        raise ValueError("COLD_MANIFEST_INVALID")
    body = manifest_path.read_bytes()
    if hashlib.sha256(body).hexdigest() != config["manifest_sha256"]:
        raise ValueError("COLD_MANIFEST_HASH_MISMATCH")
    manifest = json.loads(body)
    namespace["validate_manifest"](manifest)
    for group in ("database", "runtime"):
        space = os.statvfs("/clone/" + group)
        if (
            space.f_bavail * space.f_frsize
            < manifest["groups"][group]["logical_bytes"] + 128 * 1024**2
        ):
            raise ValueError("RESTORE_CLONE_SPACE_INSUFFICIENT")
    counts = namespace["restore_bundle"](
        Path("/bundle"),
        {"database": Path("/clone/database"), "runtime": Path("/clone/runtime")},
        retained_manifest_sha256=config["manifest_sha256"],
        enabled=True,
    )
    entries = {r["path"]: r for r in manifest["groups"]["database"]["entries"]}
    pgdata = Path("/clone/database/18/docker")
    meta = entries.get("18/docker", {})
    if (
        meta.get("kind") != "directory"
        or type(meta.get("uid")) is not int
        or type(meta.get("gid")) is not int
        or not 1 <= meta["uid"] <= 65535
        or not 0 <= meta["gid"] <= 65535
        or (pgdata / "PG_VERSION").read_bytes() != b"18\n"
    ):
        raise ValueError("RESTORE_PG_LAYOUT_INVALID")
    if any((pgdata / name).exists() for name in ("standby.signal", "recovery.signal")):
        raise ValueError("RESTORE_REPLICA_LAYOUT_UNSUPPORTED")
    pid = pgdata / "postmaster.pid"
    pid_removed = False
    expected = entries.get("18/docker/postmaster.pid")
    if expected is not None:
        fd = os.open(pid, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size > 8192
                or hashlib.sha256(stream.read(8193)).hexdigest() != expected["sha256"]
            ):
                raise ValueError("RESTORE_CLONE_PID_INVALID")
        pid.unlink()  # Fixed clone mount only, after exact byte/metadata verification.
        pid_removed = True
    elif pid.exists():
        raise ValueError("RESTORE_CLONE_PID_INVALID")
    return dict(
        status="CLONE_BYTES_AND_METADATA_RESTORED",
        file_counts=counts,
        pg_uid=meta["uid"],
        pg_gid=meta["gid"],
        clone_pid_removed=pid_removed,
        source_changed=False,
    )


if __name__ == "__main__":
    try:
        value = restore(globals().get("CONFIG"), globals().get("ARCHIVE_SOURCE", b""))
        code = 0
    except Exception as error:
        reason = "RESTORE_WORKER_UNAVAILABLE"
        if type(error) is ValueError and str(error) in {
            "RESTORE_CLONE_SPACE_INSUFFICIENT",
            "RESTORE_CONFIG_INVALID",
            "RESTORE_PG_LAYOUT_INVALID",
            "RESTORE_REPLICA_LAYOUT_UNSUPPORTED",
            "RESTORE_CLONE_PID_INVALID",
            "COLD_MANIFEST_HASH_MISMATCH",
            "COLD_ARCHIVE_HASH_MISMATCH",
            "COLD_ARCHIVE_SIZE_MISMATCH",
            "COLD_RESTORE_NOT_EMPTY",
            "COLD_MANIFEST_INVALID",
            "COLD_RESTORE_METADATA_MISMATCH",
        }:
            reason = str(error)
        value = dict(status="BLOCKED", failure_category=reason)
        code = 2
    print(json.dumps(value, sort_keys=True))
    raise SystemExit(code)
