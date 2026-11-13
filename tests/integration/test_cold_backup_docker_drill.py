"""Fresh networkless PG18 volumes only: cold byte backup, restore, independent rescan."""

import json
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
PG_IMAGE = "sha256:c293117fcecda7344b5480222e813b9f673d7abd69b1dd95eff239b768b04f59"
HELPER_IMAGE = "hybrid-read-code:fb371aeabcc9-ea6d3f52e6bb"


def run(args, *, stdin=None, timeout=45):
    result = subprocess.run(
        ["docker", *args], input=stdin, text=True, capture_output=True, timeout=timeout
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def ready(container, socket="/var/run/postgresql"):
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        # pg_isready can succeed against the temporary initdb server before hybrid exists.
        process = subprocess.run(
            ["docker", "exec", container, "cat", "/proc/1/comm"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if process.returncode == 0 and process.stdout.strip() == "postgres":
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    container,
                    "psql",
                    "-X",
                    "-q",
                    "-t",
                    "-A",
                    "-h",
                    socket,
                    "-U",
                    "dev",
                    "-d",
                    "hybrid",
                    "-c",
                    "SELECT 1",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip() == "1":
                return
        time.sleep(0.25)
    pytest.fail("Synthetic PG startup did not complete")


def sql(container, query, socket="/var/run/postgresql"):
    return run(
        [
            "exec",
            "-i",
            container,
            "psql",
            "-h",
            socket,
            "-U",
            "dev",
            "-d",
            "hybrid",
            "-X",
            "-q",
            "-t",
            "-A",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        stdin=query,
    )


def test_cold_copy_restores_whole_pg_and_runtime_to_new_networkless_volumes():
    for image in (PG_IMAGE, HELPER_IMAGE):
        if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode:
            pytest.skip("Required cached synthetic image missing")
    prefix = "cold-drill-" + uuid4().hex[:12]
    volumes = {
        name: prefix + "-" + name
        for name in ("db", "runtime", "backup", "clone-db", "clone-runtime", "rescan")
    }
    created = []
    containers = []
    source = None
    code = (ROOT / "tools/cold_backup_candidate.py").read_text()

    def helper(mounts, body):
        args = [
            "run",
            "-i",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "DAC_OVERRIDE",
            "--cap-add",
            "CHOWN",
            "--cap-add",
            "FOWNER",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "0:0",
            "--entrypoint",
            "python",
        ]
        for name, target, readonly in mounts:
            args += [
                "--mount",
                f"type=volume,source={volumes[name]},target={target},volume-nocopy"
                + (",readonly" if readonly else ""),
            ]
        return run([*args, HELPER_IMAGE, "-I", "-B", "-"], stdin=code + "\n" + body, timeout=180)

    try:
        for volume in volumes.values():
            run(["volume", "create", volume])
            created.append(volume)
        source = run(
            [
                "run",
                "-d",
                "--name",
                prefix + "-source",
                "--network",
                "none",
                "--restart",
                "no",
                "--no-healthcheck",
                "--env",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                "--env",
                "POSTGRES_USER=dev",
                "--env",
                "POSTGRES_DB=hybrid",
                "--mount",
                f"type=volume,source={volumes['db']},target=/var/lib/postgresql",
                PG_IMAGE,
                "-c",
                "listen_addresses=",
            ]
        )
        containers.append(source)
        ready(source)
        sql(
            source,
            """
CREATE TABLE demo_accounts (account_id text PRIMARY KEY,state jsonb NOT NULL);
INSERT INTO demo_accounts SELECT 'DEMO-WINDOWS-ISOLATED-BTC-V1', jsonb_build_object('paused',true,'orders',(SELECT jsonb_object_agg(i::text,'CLOSED') FROM generate_series(1,34) i),'requests','{"a":"UNKNOWN","b":"UNKNOWN","c":"UNKNOWN","d":"SUBMITTING"}'::jsonb);
CREATE TABLE demo_events (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, payload text);
INSERT INTO demo_events(payload) SELECT repeat('PRIVATE_SYNTHETIC_BODY',1000) FROM generate_series(1,100);
CREATE TABLE demo_outbox (id int PRIMARY KEY, account_id text REFERENCES demo_accounts(account_id));
INSERT INTO demo_outbox VALUES (1,'DEMO-WINDOWS-ISOLATED-BTC-V1');
CREATE TABLE read_response_evidence_events_v2 (id int PRIMARY KEY, body text);
INSERT INTO read_response_evidence_events_v2 VALUES (1,'PRIVATE_SYNTHETIC_EVIDENCE');
CREATE FUNCTION evidence_immutable_v2() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'immutable'; END $$;
CREATE TRIGGER evidence_immutable BEFORE UPDATE OR DELETE ON read_response_evidence_events_v2 FOR EACH ROW EXECUTE FUNCTION evidence_immutable_v2();
""",
        )
        query = """BEGIN READ ONLY; SELECT encode(sha256(convert_to(jsonb_build_object(
'accounts',(SELECT jsonb_agg(t ORDER BY account_id) FROM demo_accounts t),
'events',(SELECT jsonb_agg(t ORDER BY id) FROM demo_events t),
'outbox',(SELECT jsonb_agg(t ORDER BY id) FROM demo_outbox t),
'evidence',(SELECT jsonb_agg(t ORDER BY id) FROM read_response_evidence_events_v2 t),
'function',(SELECT pg_get_functiondef('evidence_immutable_v2()'::regprocedure)),
'triggers',(SELECT count(*) FROM pg_trigger WHERE tgrelid='read_response_evidence_events_v2'::regclass AND NOT tgisinternal),
'sequence',(SELECT last_value FROM demo_events_id_seq)
)::text,'UTF8')),'hex'); COMMIT;"""
        logical_before = sql(source, query)
        owner = run(["exec", source, "sh", "-c", 'stat -c "%u:%g" "$PGDATA"'])
        assert owner and all(p.isdecimal() for p in owner.split(":"))
        helper(
            [("runtime", "/runtime", False)],
            """
import sqlite3
root=Path('/runtime')
(root/'shared-weight').mkdir()
db=sqlite3.connect(root/'shared-weight'/'state.db')
db.execute('CREATE TABLE sample (value text)')
db.execute("INSERT INTO sample VALUES ('PRIVATE_SYNTHETIC_RUNTIME')")
db.commit(); db.close()
os.chown(root,10001,10001); os.chmod(root,0o700)
print('SYNTHETIC_RUNTIME_CREATED')
""",
        )
        run(["stop", "--timeout", "30", source])
        assert (
            run(["inspect", "--format", "{{.State.Running}} {{.State.ExitCode}}", source])
            == "false 0"
        )
        control = run(
            [
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "DAC_OVERRIDE",
                "--user",
                "0:0",
                "--mount",
                f"type=volume,source={volumes['db']},target=/source,readonly",
                "--env",
                "LC_ALL=C",
                "--entrypoint",
                "pg_controldata",
                PG_IMAGE,
                "/source/18/docker",
            ]
        )
        assert any(
            line.startswith("Database cluster state:")
            and line.split(":", 1)[1].strip() == "shut down"
            for line in control.splitlines()
        )
        digest = helper(
            [
                ("db", "/source/database", True),
                ("runtime", "/source/runtime", True),
                ("backup", "/output", False),
            ],
            "print(create_bundle({'database':Path('/source/database'),'runtime':Path('/source/runtime')},Path('/output'),enabled=True,sources_frozen_by_caller=True))",
        )
        assert len(digest) == 64
        restored = json.loads(
            helper(
                [
                    ("backup", "/backup", True),
                    ("clone-db", "/restore/database", False),
                    ("clone-runtime", "/restore/runtime", False),
                ],
                f"print(json.dumps(restore_bundle(Path('/backup'),{{'database':Path('/restore/database'),'runtime':Path('/restore/runtime')}},retained_manifest_sha256='{digest}',enabled=True)))",
            )
        )
        rescan = helper(
            [
                ("clone-db", "/source/database", True),
                ("clone-runtime", "/source/runtime", True),
                ("rescan", "/output", False),
            ],
            "print(create_bundle({'database':Path('/source/database'),'runtime':Path('/source/runtime')},Path('/output'),enabled=True,sources_frozen_by_caller=True))",
        )
        assert (
            rescan == digest
        )  # All PG/runtime bytes, directory/file metadata and tar digests match.
        clone = run(
            [
                "run",
                "-d",
                "--name",
                prefix + "-clone",
                "--network",
                "none",
                "--restart",
                "no",
                "--no-healthcheck",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--user",
                owner,
                "--mount",
                f"type=volume,source={volumes['clone-db']},target=/var/lib/postgresql",
                "--entrypoint",
                "postgres",
                PG_IMAGE,
                "-D",
                "/var/lib/postgresql/18/docker",
                "-c",
                "listen_addresses=",
                "-c",
                "unix_socket_directories=/tmp",
            ]
        )
        containers.append(clone)
        ready(clone, "/tmp")
        assert sql(clone, query, "/tmp") == logical_before
        assert (
            sql(
                clone,
                "SELECT jsonb_object_length FROM (SELECT count(*) AS jsonb_object_length FROM demo_accounts CROSS JOIN LATERAL jsonb_object_keys(state->'orders')) t;",
                "/tmp",
            )
            == "34"
        )
        checked = helper(
            [("clone-runtime", "/runtime", True)],
            """
import sqlite3
with sqlite3.connect('file:/runtime/shared-weight/state.db?mode=ro&immutable=1',uri=True) as db:
    assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    assert db.execute('SELECT count(*) FROM sample').fetchone()[0]==1
print('RESTORED_SQLITE_READ_ONLY_VALID')
""",
        )
        assert checked == "RESTORED_SQLITE_READ_ONLY_VALID"
        assert run(["inspect", "--format", "{{.HostConfig.NetworkMode}}", clone]) == "none"
        assert run(["inspect", "--format", "{{.State.Running}}", source]) == "false"
        report = dict(
            scope="FRESH_SYNTHETIC_NETWORKLESS_PG18_ONLY_NOT_ACTUAL_WINDOWS",
            postgres_image=PG_IMAGE,
            helper_image=HELPER_IMAGE,
            source_clean_shutdown_verified=True,
            both_source_mounts_read_only=True,
            key_volume_mounted=False,
            restore_destinations_fresh_volumes=True,
            whole_restored_manifest_matches=True,
            restored_pg_logical_hash_matches=True,
            restored_sqlite_read_only_check=True,
            restored_file_counts=restored,
            source_stayed_stopped=True,
            clone_network_mode="none",
            original_windows_touched=False,
            execution_approved=False,
            orders_enabled=False,
            live_trading_enabled=False,
        )
        target = os.environ.get("COLD_DRILL_REPORT")
        if target:
            Path(target).write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    finally:
        for container in reversed(containers):
            subprocess.run(["docker", "rm", "--force", container], capture_output=True, timeout=20)
        for volume in reversed(created):
            subprocess.run(["docker", "volume", "rm", volume], capture_output=True, timeout=20)
