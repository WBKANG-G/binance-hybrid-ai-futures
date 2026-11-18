"""Read-only comparison of stopped source volume trees with a pinned cold manifest."""

import hashlib
import json
import os
import stat
import time
from pathlib import Path


def compare(manifest, roots, library):
    result = dict(
        status="BLOCKED",
        failure_category="SOURCE_COMPARE_FAILED",
        passes=0,
        file_counts={},
        logical_bytes={},
        source_data_written=False,
        logical_account_row_queried=False,
    )
    try:
        ns = {"__name__": "source_compare_read_library"}
        exec(compile(library, "<pinned-read-library>", "exec"), ns)
        require, metadata, unchanged = ns["require"], ns["metadata"], ns["unchanged"]
        ns["validate_manifest"](manifest)
        require(set(roots) == {"database", "runtime"}, "COMPARE_ROOT_SCOPE_INVALID")
        deadline = time.monotonic() + 120
        totals = {}
        for _ in range(2):
            for group in ("database", "runtime"):
                detail = manifest["groups"][group]
                expected = {e["path"]: e for e in detail["entries"]}
                seen, count, total = set(), 0, 0
                root_fd = ns["directory"](Path(roots[group]))
                try:
                    require(
                        metadata(os.fstat(root_fd)) == detail["root"], "COMPARE_METADATA_MISMATCH"
                    )

                    def visit(parent, prefix, depth, expected=expected, seen=seen):
                        nonlocal count, total
                        require(depth <= 32, "COMPARE_DEPTH_LIMIT")
                        require(time.monotonic() <= deadline, "COMPARE_TIME_LIMIT")
                        before_dir = os.fstat(parent)
                        require(not os.listxattr(parent), "COMPARE_EXTENDED_METADATA")
                        names = []
                        with os.scandir(parent) as entries:
                            for entry in entries:
                                require(len(names) < ns["MAX_FILES"], "COMPARE_COUNT_LIMIT")
                                names.append(entry.name)
                        for name in sorted(names):
                            require(
                                name not in {"", ".", ".."} and "\\" not in name,
                                "COMPARE_PATH_INVALID",
                            )
                            path = prefix + "/" + name if prefix else name
                            require(path in expected and path not in seen, "COMPARE_TREE_MISMATCH")
                            seen.add(path)
                            item = expected[path]
                            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                            require(
                                stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode),
                                "COMPARE_LINK_OR_SPECIAL",
                            )
                            require(
                                metadata(info)
                                == {k: item[k] for k in ("uid", "gid", "mode", "mtime_ns")},
                                "COMPARE_METADATA_MISMATCH",
                            )
                            if stat.S_ISDIR(info.st_mode):
                                require(item["kind"] == "directory", "COMPARE_TREE_MISMATCH")
                                child = os.open(name, ns["DIR_FLAGS"], dir_fd=parent)
                                try:
                                    unchanged(info, os.fstat(child))
                                    visit(child, path, depth + 1)
                                finally:
                                    os.close(child)
                            else:
                                require(
                                    item["kind"] == "file"
                                    and info.st_nlink == 1
                                    and info.st_size == item["size"],
                                    "COMPARE_SIZE_OR_LINK_MISMATCH",
                                )
                                require(info.st_size <= ns["MAX_FILE"], "COMPARE_BYTE_LIMIT")
                                count += 1
                                total += info.st_size
                                require(total <= ns["MAX_BYTES"], "COMPARE_BYTE_LIMIT")
                                fd = os.open(
                                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent
                                )
                                with os.fdopen(fd, "rb") as source:
                                    unchanged(info, os.fstat(source.fileno()))
                                    require(
                                        not os.listxattr(source.fileno()),
                                        "COMPARE_EXTENDED_METADATA",
                                    )
                                    h = hashlib.sha256()
                                    size = 0
                                    while True:
                                        require(time.monotonic() <= deadline, "COMPARE_TIME_LIMIT")
                                        chunk = source.read(1024**2)
                                        if not chunk:
                                            break
                                        size += len(chunk)
                                        require(size <= info.st_size, "COMPARE_SOURCE_CHANGED")
                                        h.update(chunk)
                                    unchanged(info, os.fstat(source.fileno()))
                                require(
                                    size == item["size"] and h.hexdigest() == item["sha256"],
                                    "COMPARE_CONTENT_MISMATCH",
                                )
                            unchanged(info, os.stat(name, dir_fd=parent, follow_symlinks=False))
                        unchanged(before_dir, os.fstat(parent))

                    visit(root_fd, "", 0)
                    require(
                        seen == set(expected) and total == detail["logical_bytes"],
                        "COMPARE_TREE_MISMATCH",
                    )
                    require(
                        metadata(os.fstat(root_fd)) == detail["root"], "COMPARE_METADATA_MISMATCH"
                    )
                    totals[group] = (count, total)
                finally:
                    os.close(root_fd)
            result["passes"] += 1
        result.update(
            status="STOPPED_SOURCE_TREES_MATCH_PINNED_BACKUP",
            failure_category="NONE",
            file_counts={g: v[0] for g, v in totals.items()},
            logical_bytes={g: v[1] for g, v in totals.items()},
        )
    except Exception as error:
        text = str(error)
        if (
            type(error) is ValueError
            and text.startswith(("COMPARE_", "COLD_"))
            and text.replace("_", "").isascii()
            and text.replace("_", "").isalnum()
            and len(text) <= 100
        ):
            result["failure_category"] = text
    return result


if __name__ == "__main__":
    value = compare(
        globals().get("MANIFEST"),
        {"database": "/source/database", "runtime": "/source/runtime"},
        globals().get("READ_LIBRARY", b""),
    )
    print(json.dumps(value, sort_keys=True))
    raise SystemExit(0 if value["status"] == "STOPPED_SOURCE_TREES_MATCH_PINNED_BACKUP" else 2)
