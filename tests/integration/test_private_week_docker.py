"""Fresh logical PG/runtime clone + actual RO tmpfs signing/archive, Nov 16 only."""

import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from services.execution.demo_private_read_scope import validate_private_read_scope
from tests.fixtures.copy_transition_runner import seed
from tests.integration.test_cold_backup_docker_drill import PG_IMAGE, ROOT, ready, sql
from tests.integration.test_private_read_keys_docker import (
    HELPER_IMAGE,
    KEY,
    SECRET,
    docker,
)
from tests.integration.test_private_read_keys_docker import (
    payload as key_payload,
)
from tests.unit.test_copy_account_content import account_body

EXTRAS = (
    ("services.execution.demo_evidence_snapshot", "services/execution/demo_evidence_snapshot.py"),
    (
        "services.execution.demo_copy_transition_review",
        "services/execution/demo_copy_transition_review.py",
    ),
    (
        "services.execution.demo_copy_clock_rehearsal",
        "services/execution/demo_copy_clock_rehearsal.py",
    ),
    (
        "services.execution.demo_copy_baseline_evidence",
        "services/execution/demo_copy_baseline_evidence.py",
    ),
    (
        "services.execution.demo_copy_account_content",
        "services/execution/demo_copy_account_content.py",
    ),
    (
        "services.execution.demo_copy_evidence_rehearsal",
        "services/execution/demo_copy_evidence_rehearsal.py",
    ),
    (
        "services.execution.demo_copy_private_week_review",
        "services/execution/demo_copy_private_week_review.py",
    ),
)


def memory(body):
    code = ""
    for name, path in EXTRAS:
        raw = (ROOT / path).read_bytes()
        encoded = base64.b64encode(raw).decode()
        code += f"\nbody=base64.b64decode({encoded!r});assert hashlib.sha256(body).hexdigest()=={hashlib.sha256(raw).hexdigest()!r}\nmodule=types.ModuleType({name!r});module.__file__='/memory/'+{path!r};module.__package__={name.rpartition('.')[0]!r};sys.modules[{name!r}]=module;exec(compile(body,'<week-private-candidate>','exec'),module.__dict__)\n"
    return key_payload(code + body)


def test_fresh_pg_clone_and_private_tmpfs_preserve_old_day_week_and_originals():
    for image in (HELPER_IMAGE, PG_IMAGE):
        if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode:
            pytest.skip("Cached synthetic image unavailable")
    image_id = docker("image", "inspect", HELPER_IMAGE, "--format", "{{.Id}}")
    token = uuid4().hex
    names = {
        role: ("demo-read-private-keys-" if role == "keys" else "demo-copy-review-" + role + "-")
        + token
        for role in ("keys", "runtime", "socket")
    }
    names.update(
        {
            role: "private-week-" + role + "-" + token
            for role in ("source-db", "source-runtime", "source-socket", "clone-db")
        }
    )
    original_ids = set(docker("ps", "--all", "--quiet", "--no-trunc").splitlines())
    original_volumes = set(docker("volume", "ls", "--format", "{{.Name}}").splitlines())
    containers = []
    volumes = []

    def create(mounts, *, role=None, command=None, user="10001:10001", env=None, pg=False):
        args = [
            "create",
            "--pull=never",
            "--interactive",
            "--network",
            "none",
            "--restart",
            "no",
            "--memory",
            "512m",
            "--pids-limit",
            "64",
            "--log-driver",
            "none",
        ]
        if not pg:
            args += [
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--user",
                user,
                "--entrypoint",
                "python",
            ]
        if role:
            args += [
                "--label",
                "com.docker.compose.project=demo-read-private",
                "--label",
                "com.docker.compose.service=" + role,
            ]
        for key, value in (env or {}).items():
            args += ["--env", key + "=" + value]
        for source, dest, rw in mounts:
            args += [
                "--mount",
                f"type=volume,source={source},target={dest},volume-nocopy"
                + ("" if rw else ",readonly"),
            ]
        args += [PG_IMAGE if pg else image_id] + (command or ["-I", "-B", "-"])
        cid = docker(*args)
        containers.append(cid)
        return cid

    def run(cid, code):
        return docker("start", "--attach", "--interactive", cid, body=code, timeout=180)

    def inspect(cid):
        return json.loads(docker("inspect", cid))[0]

    try:
        for role, name in names.items():
            if role == "keys":
                docker(
                    "volume",
                    "create",
                    "--driver",
                    "local",
                    "--opt",
                    "type=tmpfs",
                    "--opt",
                    "device=tmpfs",
                    "--opt",
                    "o=size=65536,mode=0700,uid=10001,gid=10001,noexec,nosuid,nodev",
                    name,
                )
            else:
                docker("volume", "create", name)
            volumes.append(name)
        # Root preparer touches only new, empty runtime/socket volumes; no keys/DB/source data.
        prep_args = [
            "run",
            "--rm",
            "--interactive",
            "--network",
            "none",
            "--read-only",
            "--log-driver",
            "none",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "CHOWN",
            "--cap-add",
            "FOWNER",
            "--user",
            "0:0",
            "--entrypoint",
            "python",
        ]
        for role in ("source-runtime", "source-socket", "runtime", "socket"):
            prep_args += [
                "--mount",
                f"type=volume,source={names[role]},target=/{role},volume-nocopy",
            ]
        docker(
            *prep_args,
            image_id,
            "-I",
            "-B",
            "-",
            body="import os\nfor path in ('/source-runtime','/runtime'):\n os.chown(path,10001,10001);os.chmod(path,0o700)\nfor path in ('/source-socket','/socket'):os.chmod(path,0o777)\n",
        )

        def pg(database, socket, dest):
            return create(
                [(names[database], "/var/lib/postgresql", True), (names[socket], dest, True)],
                pg=True,
                env=dict(
                    POSTGRES_USER="dev",
                    POSTGRES_PASSWORD="dev",
                    POSTGRES_DB="hybrid",
                    POSTGRES_HOST_AUTH_METHOD="trust",
                ),
                command=[
                    "postgres",
                    "-c",
                    "listen_addresses=",
                    "-c",
                    "unix_socket_directories=/var/run/postgresql," + dest,
                    "-c",
                    "unix_socket_permissions=0777",
                ],
            )

        source_pg = pg("source-db", "source-socket", "/source-socket")
        docker("start", source_pg)
        ready(source_pg, "/source-socket")
        sql(
            source_pg,
            "CREATE TABLE read_response_evidence_events_v2 (id int);CREATE TABLE demo_accounts (id int);",
            "/source-socket",
        )
        source_api = create(
            [
                (names["source-runtime"], "/app/demo-export-state", True),
                (names["source-socket"], "/source-socket", False),
            ],
            env=dict(DATABASE_URL="postgresql+psycopg://dev@/hybrid?host=/source-socket"),
            command=["-I", "-B", "-c", "import signal; signal.pause()"],
        )
        docker("start", source_api)

        def seed_docker(*args, stdin=None):
            return docker(*args, body=stdin)

        seed(source_api, seed_docker)
        observation = memory("""
import json
from pathlib import Path
from sqlalchemy import create_engine,select
from packages.persistence.demo import accounts
from services.execution.demo_runtime_storage import RUNTIME_STORAGE_KEY
from services.execution.demo_evidence_snapshot import evidence_snapshot
engine=create_engine('postgresql+psycopg://dev@/hybrid?host=/source-socket',hide_parameters=True)
with engine.connect() as c:state=c.execute(select(accounts.c.state)).scalar_one()
binding=json.loads(state['operations'][RUNTIME_STORAGE_KEY])
snapshot=evidence_snapshot(engine,Path('/app/demo-export-state/demo-runtime-20261111/shared-weight'))
print(json.dumps({'binding':binding,'snapshot':snapshot},sort_keys=True))
engine.dispose()
""")
        source_result = json.loads(
            docker("exec", "--interactive", source_api, "python", "-I", "-B", "-", body=observation)
        )
        dump = subprocess.run(
            [
                "docker",
                "exec",
                source_pg,
                "pg_dump",
                "-h",
                "/source-socket",
                "-U",
                "dev",
                "-d",
                "hybrid",
                "--format=custom",
            ],
            capture_output=True,
            timeout=45,
        )
        assert dump.returncode == 0
        docker("stop", "--time", "2", source_api)
        docker("stop", "--time", "10", source_pg)
        copy_args = [
            "run",
            "--rm",
            "--interactive",
            "--network",
            "none",
            "--read-only",
            "--log-driver",
            "none",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "CHOWN",
            "--cap-add",
            "FOWNER",
            "--cap-add",
            "DAC_OVERRIDE",
            "--user",
            "0:0",
            "--entrypoint",
            "python",
            "--mount",
            f"type=volume,source={names['source-runtime']},target=/original,readonly,volume-nocopy",
            "--mount",
            f"type=volume,source={names['runtime']},target=/copy,volume-nocopy",
        ]
        copy_code = """
import os,shutil,stat
from pathlib import Path
source=Path('/original');target=Path('/copy')
for path in sorted(source.rglob('*'),key=lambda p:len(p.parts)):
 assert not path.is_symlink()
 dest=target/path.relative_to(source);info=path.stat()
 if path.is_dir():dest.mkdir(exist_ok=True)
 else:shutil.copy2(path,dest)
 os.chown(dest,info.st_uid,info.st_gid);os.chmod(dest,stat.S_IMODE(info.st_mode))
"""
        docker(*copy_args, image_id, "-I", "-B", "-", body=copy_code)
        clone_pg = pg("clone-db", "socket", "/review-socket")
        docker("start", clone_pg)
        ready(clone_pg, "/review-socket")
        restore = subprocess.run(
            [
                "docker",
                "exec",
                "--interactive",
                clone_pg,
                "pg_restore",
                "--clean",
                "--if-exists",
                "--no-owner",
                "-h",
                "/review-socket",
                "-U",
                "dev",
                "-d",
                "hybrid",
            ],
            input=dump.stdout,
            capture_output=True,
            timeout=45,
        )
        assert restore.returncode == 0, restore.stderr.decode()
        holder = create(
            [(names["keys"], "/run/demo-keys", False)],
            role="key-holder",
            command=["-I", "-B", "-c", "import signal; signal.pause()"],
        )
        registrar = create([(names["keys"], "/run/demo-keys", True)], role="registrar")
        reader = create(
            [
                (names["keys"], "/run/demo-keys", False),
                (names["runtime"], "/review-runtime", True),
                (names["socket"], "/review-socket", False),
            ],
            role="read-helper",
        )
        policy = validate_private_read_scope(
            inspect(holder),
            inspect(reader),
            inspect(registrar),
            image=image_id,
            keys_volume=names["keys"],
            runtime_volume=names["runtime"],
            socket_volume=names["socket"],
            owned_container_ids={holder, reader, registrar},
            forbidden_container_ids=original_ids | {source_api, source_pg, clone_pg},
            forbidden_volume_names=original_volumes
            | {
                names["source-db"],
                names["source-runtime"],
                names["source-socket"],
                names["clone-db"],
            },
            enable_review=True,
        )
        docker("start", holder)
        register = key_payload(
            "\nfrom scripts import demo_key_session as keys\nanswers=iter("
            + repr((KEY, SECRET))
            + ");keys.getpass.getpass=lambda *args:next(answers)\nkeys.register()\n"
        )
        run(registrar, register)
        cfg = json.dumps(
            dict(binding=source_result["binding"], pin=source_result["snapshot"]["sha256"])
        )
        worker = memory(
            """
import json
from pathlib import Path
from uuid import UUID,uuid4
from sqlalchemy import create_engine
from services.execution.demo_evidence_snapshot import evidence_snapshot
from services.execution.demo_copy_clock_rehearsal import rehearse_copy_clock
from services.execution.demo_copy_private_week_review import rehearse_private_week_copy
from services.execution.demo_copy_volatile_credentials import PrivateReadCredentialLease
CFG=json.loads(CONFIG_LITERAL)
url='postgresql+psycopg://dev@/hybrid?host=/review-socket'
reader=create_engine(url,hide_parameters=True,connect_args={'options':'-c default_transaction_read_only=on -c timezone=UTC'})
writer=create_engine(url,hide_parameters=True,connect_args={'options':'-c timezone=UTC'})
folder=Path('/review-runtime/demo-runtime-20261111/shared-weight');b=CFG['binding']
assert evidence_snapshot(reader,folder)['sha256']==CFG['pin']
clock=rehearse_copy_clock(reader,writer,'DEMO-WINDOWS-ISOLATED-BTC-V1',folder,b['scope'],UUID(b['journal']),UUID(b['checkpoint_installation']),CFG['pin'],apply=True,copy_only=True)
key_before=Path('/run/demo-keys/session.json').read_bytes()
lease=PrivateReadCredentialLease('DEMO-WINDOWS-ISOLATED-BTC-V1',b['scope'],enable_private_read=True,copy_only=True)
result=rehearse_private_week_copy(reader,writer,'DEMO-WINDOWS-ISOLATED-BTC-V1',folder,b['scope'],UUID(b['journal']),UUID(b['checkpoint_installation']),clock['after_snapshot_sha256'],folder/('synthetic-response-'+uuid4().hex),BODY_LITERAL,lease,copy_only=True,synthetic_only=True,acknowledge_week_boundary_observation=True)
assert Path('/run/demo-keys/session.json').read_bytes()==key_before
print(json.dumps({'clock':clock,'private_review':result,'key_file_unchanged':True},sort_keys=True))
reader.dispose();writer.dispose()
""".replace("CONFIG_LITERAL", repr(cfg)).replace(
                "BODY_LITERAL", repr(json.dumps(account_body()).encode())
            )
        )
        result = json.loads(run(reader, worker))
        assert (
            result["private_review"]["review_day_kst"]
            == result["private_review"]["review_week_kst"]
            == "2026-11-16"
        )
        assert (
            result["private_review"]["stored_day_kst"] == "2026-11-13"
            and result["private_review"]["stored_week_kst"] == "2026-11-09"
        )
        assert result["private_review"]["pipeline"]["private_tmpfs_credentials_used"]
        assert (
            result["private_review"]["pipeline"]["audit_events_after"] == 3
            and result["key_file_unchanged"]
        )
        docker("stop", "--time", "10", clone_pg)
        assert (
            inspect(clone_pg)["State"]["ExitCode"] == 0
            and not inspect(clone_pg)["State"]["OOMKilled"]
        )
        assert (
            not inspect(source_pg)["State"]["Running"]
            and not inspect(source_api)["State"]["Running"]
        )
        # Original runtime only ever re-mounted read-only; compare every original file.
        verify = create([(names["source-runtime"], "/original", False)])
        hashes = json.loads(
            run(
                verify,
                "import hashlib,json\nfrom pathlib import Path\nroot=Path('/original/demo-runtime-20261111/shared-weight')\nprint(json.dumps({str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()},sort_keys=True))\n",
            )
        )
        assert hashes == source_result["snapshot"]["files"]
        report = dict(
            actual_windows_touched=False,
            real_docker_pg_logical_clone=True,
            original_runtime_readonly_copy=True,
            original_runtime_hashes_preserved=True,
            originals_remained_stopped=True,
            clone_pg_stopped_cleanly=True,
            real_70_second_hold=True,
            actual_readonly_tmpfs_credential_lease=True,
            key_file_unchanged=True,
            synthetic_keys_only=True,
            actual_exchange_http_attempts=0,
            private_scope_policy=policy,
            result=result["private_review"],
        )
        if os.getenv("PRIVATE_WEEK_DOCKER_REPORT"):
            Path(os.environ["PRIVATE_WEEK_DOCKER_REPORT"]).write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n"
            )
    finally:
        for cid in reversed(containers):
            subprocess.run(["docker", "rm", "--force", cid], capture_output=True, timeout=15)
        for name in reversed(volumes):
            subprocess.run(["docker", "volume", "rm", name], capture_output=True, timeout=15)
    assert set(docker("ps", "--all", "--quiet", "--no-trunc").splitlines()) == original_ids
    assert set(docker("volume", "ls", "--format", "{{.Name}}").splitlines()) == original_volumes
