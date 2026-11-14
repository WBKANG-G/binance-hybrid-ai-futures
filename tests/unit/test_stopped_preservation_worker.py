"""Preservation worker explicit intent, layout, failure sanitization and PID retention."""

import json

import pytest

from tests.unit.test_stopped_preservation import package


@pytest.mark.parametrize(
    "case,reason",
    [
        ("valid", None),
        ("no-intent", "COLD_WORKER_FAILED"),
        ("extra-config", "COLD_WORKER_FAILED"),
        ("wrong-layout", "COLD_WORKER_FAILED"),
        ("wrong-version", "COLD_PG_VERSION_INVALID"),
        ("linked-version", "COLD_WORKER_FAILED"),
        ("symlink", "COLD_LINK_OR_SPECIAL_FILE"),
        ("xattr", "COLD_EXTENDED_METADATA_UNSUPPORTED"),
    ],
)
def test_worker_preserves_stale_pid_without_rewriting_source(tmp_path, case, reason):
    import os

    files = package()
    database = tmp_path / "database"
    runtime = tmp_path / "runtime"
    output = tmp_path / "output"
    pgdata = database / "18/docker"
    pgdata.mkdir(parents=True)
    runtime.mkdir()
    output.mkdir()
    (pgdata / "PG_VERSION").write_bytes(b"17\n" if case == "wrong-version" else b"18\n")
    pid = pgdata / "postmaster.pid"
    pid.write_bytes(b"1\nSYNTHETIC_STALE_PID\n")
    (runtime / "state").write_bytes(b"SYNTHETIC_STATE")
    if case == "linked-version":
        (pgdata / "PG_VERSION").unlink()
        (pgdata / "PG_VERSION").symlink_to(pid)
    if case == "symlink":
        (runtime / "link").symlink_to(pid)
    if case == "xattr":
        os.setxattr(pid, "user.synthetic", b"metadata")
    # Test-only filesystem mapping; released worker retains fixed container paths.
    source = files["preserve_driver.py"].decode()
    for old, new in (
        ("/source/database", database),
        ("/source/runtime", runtime),
        ("/output", output),
    ):
        source = source.replace(old, str(new))
    namespace = {"__name__": "worker_test"}
    exec(compile(source, "<test-fixed-path-map>", "exec"), namespace)
    config = dict(
        pgdata_relative="wrong" if case == "wrong-layout" else "18/docker",
        postgresql_major=18,
        preserve_unclean_stopped_source=case != "no-intent",
    )
    if case == "extra-config":
        config["PRIVATE_SENTINEL"] = True
    result, code = namespace["collect"](config, files["cold_archive.py"])
    assert code == (2 if reason else 0)
    assert "SYNTHETIC_STALE_PID" not in json.dumps(result) and "PRIVATE_SENTINEL" not in json.dumps(
        result
    )
    assert pid.read_bytes() == b"1\nSYNTHETIC_STALE_PID\n"
    if reason:
        assert result["failure_category"] == reason
    else:
        import tarfile

        with tarfile.open(output / "database.tar") as archive:
            member = next(m for m in archive.getmembers() if m.name.endswith("/postmaster.pid"))
            assert archive.extractfile(member).read() == pid.read_bytes()
        assert result["status"] == "COLD_ARCHIVES_CREATED"
        assert len(result["manifest_sha256"]) == 64
