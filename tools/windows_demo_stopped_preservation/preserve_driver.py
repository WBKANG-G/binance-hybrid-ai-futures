"""Preserve stopped PG18 bytes, including stale PID/WAL; never claim clean shutdown."""

import json
import os
import re
import stat
import time
from pathlib import Path
from typing import Any


def collect(config: Any, archive_source: bytes) -> tuple[dict[str, Any], int]:
    report: dict[str, Any] = dict(
        status="BLOCKED",
        failure_category="COLD_WORKER_FAILED",
        manifest_sha256=None,
        archives={},
        file_counts={},
        source_changed=False,
    )
    try:
        if not isinstance(config, dict) or set(config) != {
            "pgdata_relative",
            "postgresql_major",
            "preserve_unclean_stopped_source",
        }:
            return report, 2
        if (
            config["preserve_unclean_stopped_source"] is not True
            or type(config["postgresql_major"]) is not int
            or config["postgresql_major"] != 18
        ):
            return report, 2
        relative = config["pgdata_relative"]
        if not isinstance(relative, str) or re.fullmatch(r"18/docker", relative) is None:
            return report, 2
        namespace: dict[str, Any] = {"__name__": "cold_archive_worker"}
        exec(compile(archive_source, "<cold-archive>", "exec"), namespace)
        pgdata = Path("/source/database") / relative
        fd = namespace["directory"](pgdata)
        try:
            version_fd = os.open("PG_VERSION", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            with os.fdopen(version_fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 16:
                    raise ValueError("COLD_PG_VERSION_INVALID")
                version = stream.read(17)
            if version != str(config["postgresql_major"]).encode() + b"\n":
                raise ValueError("COLD_PG_VERSION_INVALID")
        finally:
            os.close(fd)
        # Space check on the Docker output filesystem before copying any bodies.
        estimated = 0
        estimate_deadline = time.monotonic() + 30
        for root in (Path("/source/database"), Path("/source/runtime")):
            count = 0
            for current, dirs, files in os.walk(root, followlinks=False):
                if time.monotonic() > estimate_deadline:
                    raise ValueError("COLD_TIME_LIMIT")
                count += len(dirs) + len(files)
                if count > namespace["MAX_FILES"]:
                    raise ValueError("COLD_COUNT_LIMIT")
                for name in [*dirs, *files]:
                    item = (Path(current) / name).lstat()
                    if stat.S_ISLNK(item.st_mode):
                        raise ValueError("COLD_LINK_OR_SPECIAL_FILE")
                    if stat.S_ISREG(item.st_mode):
                        estimated += item.st_size
        space = os.statvfs("/output")
        if space.f_bavail * space.f_frsize < estimated + 128 * 1024**2:
            raise ValueError("COLD_OUTPUT_SPACE_INSUFFICIENT")
        digest = namespace["create_bundle"](
            {"database": Path("/source/database"), "runtime": Path("/source/runtime")},
            Path("/output"),
            enabled=True,
            sources_frozen_by_caller=True,
        )
        body = (Path("/output") / "manifest.json").read_bytes()
        manifest = json.loads(body)
        report.update(
            status="COLD_ARCHIVES_CREATED",
            failure_category="NONE",
            manifest_sha256=digest,
            archives={
                name: dict(sha256=item["archive_sha256"], bytes=item["archive_bytes"])
                for name, item in manifest["groups"].items()
            },
            file_counts={
                name: sum(r["kind"] == "file" for r in item["entries"])
                for name, item in manifest["groups"].items()
            },
        )
        return report, 0
    except Exception as error:
        allowed = {
            "COLD_POSTMASTER_PID_PRESENT",
            "COLD_PG_VERSION_INVALID",
            "COLD_OUTPUT_SPACE_INSUFFICIENT",
            "COLD_COUNT_LIMIT",
            "COLD_LINK_OR_SPECIAL_FILE",
            "COLD_EXTENDED_METADATA_UNSUPPORTED",
            "COLD_SOURCE_CHANGED",
            "COLD_BYTE_LIMIT",
            "COLD_FILE_LIMIT",
            "COLD_DEPTH_LIMIT",
            "COLD_TIME_LIMIT",
            "COLD_OUTPUT_NOT_EMPTY",
            "COLD_MODE_INVALID",
            "COLD_OWNER_INVALID",
        }
        if type(error) is ValueError and str(error) in allowed:
            report["failure_category"] = str(error)
        return report, 2


if __name__ == "__main__":
    result, status = collect(globals().get("CONFIG"), globals().get("ARCHIVE_SOURCE", b""))
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(status)
