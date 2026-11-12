"""Small stdlib-only loader, embedded into the Windows binary stdin runner."""

import hashlib
import io
import json
import runpy
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


def load(expected: str) -> int:
    body = sys.stdin.buffer.read(8 * 1024 * 1024 + 1)
    if len(body) > 8 * 1024 * 1024 or hashlib.sha256(body).hexdigest() != expected:
        raise ValueError("candidate archive invalid")
    with tempfile.TemporaryDirectory(prefix="demo-read-candidate-") as folder:
        root = Path(folder)
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            entries = archive.infolist()
            if len(entries) > 512 or sum(e.file_size for e in entries) > 16 * 1024 * 1024:
                raise ValueError("candidate archive limit")
            seen = set()
            for entry in entries:
                name = PurePosixPath(entry.filename)
                if (
                    name.is_absolute()
                    or ".." in name.parts
                    or "\\" in entry.filename
                    or ":" in entry.filename
                    or entry.filename in seen
                    or entry.is_dir()
                    or (entry.external_attr >> 16) & 0o170000 not in (0, 0o100000)
                ):
                    raise ValueError("candidate archive entry invalid")
                seen.add(entry.filename)
                path = root.joinpath(*name.parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as out:
                    out.write(archive.read(entry))
                path.chmod(0o600)
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(root))
        try:
            runpy.run_module("scripts.review_demo_read_deployment", run_name="__main__")
        except SystemExit as result:
            return int(result.code or 0)
        finally:
            sys.path.pop(0)
    return 0


if __name__ == "__main__":
    try:
        status = load(sys.argv[1])
    except Exception:
        print(
            json.dumps(
                dict(
                    read_deployment_review="BLOCKED_KEEP_PAUSED",
                    stop_reason="CANDIDATE_LOADER_REFUSED",
                    orders_enabled=False,
                    live_trading_enabled=False,
                    execution_approved=False,
                )
            )
        )
        status = 1
    raise SystemExit(status)
