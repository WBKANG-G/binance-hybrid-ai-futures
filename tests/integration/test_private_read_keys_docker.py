"""Fresh cloud Docker tmpfs: RO reader, key-only registrar, no original volumes/HTTP."""

import base64
import hashlib
import json
import os
import selectors
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from services.execution.demo_private_read_scope import validate_private_read_scope
from tests.integration.test_cold_backup_docker_drill import HELPER_IMAGE, ROOT

MODULES = (
    ("scripts.demo_key_session", "scripts/demo_key_session.py"),
    ("packages.persistence.demo_read_recovery", "packages/persistence/demo_read_recovery.py"),
    (
        "packages.persistence.demo_response_evidence",
        "packages/persistence/demo_response_evidence.py",
    ),
    (
        "services.execution.demo_response_evidence_transport",
        "services/execution/demo_response_evidence_transport.py",
    ),
    (
        "services.execution.demo_copy_response_archive",
        "services/execution/demo_copy_response_archive.py",
    ),
    (
        "services.execution.demo_read_credential_context",
        "services/execution/demo_read_credential_context.py",
    ),
    (
        "services.execution.demo_copy_volatile_credentials",
        "services/execution/demo_copy_volatile_credentials.py",
    ),
)
KEY = "SYNTHETIC_TMPFS_DOCKER_KEY"
SECRET = "SYNTHETIC_TMPFS_DOCKER_SECRET"


def docker(*args, body=None, timeout=40):
    result = subprocess.run(
        ["docker", *args], input=body, text=True, capture_output=True, timeout=timeout
    )
    assert result.returncode == 0, result.stderr
    assert KEY not in result.stdout + result.stderr and SECRET not in result.stdout + result.stderr
    return result.stdout.strip()


def payload(body):
    sources = [
        (
            name,
            path,
            base64.b64encode((ROOT / path).read_bytes()).decode(),
            hashlib.sha256((ROOT / path).read_bytes()).hexdigest(),
        )
        for name, path in MODULES
    ]
    return (
        """import sys,base64,hashlib,types
sys.path.insert(0,'/app')
for name,path,encoded,pin in SOURCES:
 body=base64.b64decode(encoded)
 assert hashlib.sha256(body).hexdigest()==pin
 module=types.ModuleType(name);module.__file__='/in-memory-review/'+path;module.__package__=name.rpartition('.')[0]
 sys.modules[name]=module
 exec(compile(body,'<private-key-candidate>','exec'),module.__dict__)
""".replace(
            "for name,path,encoded,pin in SOURCES:",
            "for name,path,encoded,pin in " + repr(sources) + ":",
        )
        + body
    )


def test_actual_tmpfs_reader_isolation_registration_lock_and_mount_lifetime():
    if subprocess.run(["docker", "image", "inspect", HELPER_IMAGE], capture_output=True).returncode:
        pytest.skip("Cached synthetic image unavailable")
    image = docker("image", "inspect", HELPER_IMAGE, "--format", "{{.Id}}")
    token = uuid4().hex
    keys_volume = "demo-read-private-keys-" + token
    runtime_volume = "demo-copy-review-runtime-" + token
    socket_volume = "demo-copy-review-socket-" + token
    containers = []
    volumes = []
    proc = None
    original_ids = set(docker("ps", "--all", "--quiet", "--no-trunc").splitlines())
    original_volumes = set(docker("volume", "ls", "--format", "{{.Name}}").splitlines())

    def inspect(cid):
        return json.loads(docker("inspect", cid))[0]

    def scope(holder, reader, registrar):
        return validate_private_read_scope(
            inspect(holder),
            inspect(reader),
            inspect(registrar),
            image=image,
            keys_volume=keys_volume,
            runtime_volume=runtime_volume,
            socket_volume=socket_volume,
            owned_container_ids={holder, reader, registrar},
            forbidden_container_ids=original_ids,
            forbidden_volume_names=original_volumes,
            enable_review=True,
        )

    def create(role, mounts, holder=False):
        args = [
            "create",
            "--pull=never",
            "--interactive",
            "--network",
            "none",
            "--restart",
            "no",
            "--read-only",
            "--memory",
            "384m",
            "--pids-limit",
            "32",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--log-driver",
            "none",
            "--user",
            "10001:10001",
            "--label",
            "com.docker.compose.project=demo-read-private",
            "--label",
            "com.docker.compose.service=" + role,
            "--entrypoint",
            "python",
        ]
        for source, destination, rw in mounts:
            args += [
                "--mount",
                f"type=volume,source={source},target={destination},volume-nocopy"
                + ("" if rw else ",readonly"),
            ]
        args += [image, "-I", "-B"] + (["-c", "import signal; signal.pause()"] if holder else ["-"])
        cid = docker(*args)
        containers.append(cid)
        return cid

    key_ro = [(keys_volume, "/run/demo-keys", False)]
    key_rw = [(keys_volume, "/run/demo-keys", True)]
    read_mounts = key_ro + [
        (runtime_volume, "/review-runtime", True),
        (socket_volume, "/review-socket", False),
    ]

    def register_body():
        return payload(
            """
import json
from scripts import demo_key_session as keys
answers=iter(CREDS)
keys.getpass.getpass=lambda *args:next(answers)
keys.register()
print(json.dumps({'registered':True,'persistent_storage':False}))
""".replace("CREDS", repr((KEY, SECRET)))
        )

    try:
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
            keys_volume,
        )
        volumes.append(keys_volume)
        for volume in (runtime_volume, socket_volume):
            docker("volume", "create", volume)
            volumes.append(volume)
        holder = create("key-holder", key_ro, holder=True)
        reader = create("read-helper", read_mounts)
        registrar = create("registrar", key_rw)
        policy = scope(holder, reader, registrar)
        assert policy["reader_key_mount_readonly"]
        docker("start", holder)
        assert json.loads(
            docker("start", "--attach", "--interactive", registrar, body=register_body())
        )["registered"]
        program = payload("""
import json,signal
from pathlib import Path
from services.execution.demo_copy_volatile_credentials import PrivateReadCredentialLease
from services.execution.demo_read_credential_context import LocalReadCredentialContext
from scripts import demo_key_session as keys
released=False
def release(*args):
 global released
 released=True
signal.signal(signal.SIGUSR1,release)
signal.signal(signal.SIGALRM,lambda *args:(_ for _ in ()).throw(RuntimeError('SYNTHETIC_GATE_TIMEOUT')))
signal.alarm(25)
lease=PrivateReadCredentialLease('DEMO-test','DEMO-private',enable_private_read=True,copy_only=True)
context=None
try:
 original=Path('/run/demo-keys/session.json').read_bytes()
 context=LocalReadCredentialContext('DEMO-test','DEMO-private',lease._credentials,enable_local_context=True,copy_only=True)
 read=context.prepare();context.consume(read);context.verify_consumed(read)
 print(json.dumps({'lease_open':True,'signature_checked':True}),flush=True)
 while not released:signal.pause()
 lease.verify();context.verify_consumed(read)
 assert Path('/run/demo-keys/session.json').read_bytes()==original
 print(json.dumps({'same_file':True,'readonly_tmpfs_verified':True,'credentials_printed':False}),flush=True)
finally:
 signal.alarm(0)
 if context is not None:context.close()
 lease.close()
""")
        proc = subprocess.Popen(
            ["docker", "start", "--attach", "--interactive", reader],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        proc.stdin.write(program)
        proc.stdin.close()
        proc.stdin = None
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        assert selector.select(20), "Synthetic reader did not report readiness"
        line = proc.stdout.readline()
        assert json.loads(line)["lease_open"]
        selector.close()
        clear = create("registrar", key_rw)
        scope(holder, reader, clear)
        blocked = payload("""
import json
from scripts import demo_key_session as keys
try:keys.clear();raise RuntimeError('SYNTHETIC_LOCK_DID_NOT_BLOCK_CLEAR')
except keys.SessionKeyError as error:
 assert str(error)=='KEY_SESSION_REGISTRATION_IN_PROGRESS'
 print(json.dumps({'registration_and_clear_blocked_by_reader':True}))
""")
        assert json.loads(docker("start", "--attach", "--interactive", clear, body=blocked))[
            "registration_and_clear_blocked_by_reader"
        ]
        docker("kill", "--signal=SIGUSR1", reader)
        stdout, stderr = proc.communicate(timeout=20)
        assert proc.returncode == 0 and not stderr, stderr
        assert json.loads(stdout)["readonly_tmpfs_verified"]
        assert KEY not in line + stdout + stderr and SECRET not in line + stdout + stderr
        renew = create("registrar", key_rw)
        renew_body = payload(
            """
import json
from scripts import demo_key_session as keys
keys.clear()
answers=iter(CREDS);keys.getpass.getpass=lambda *args:next(answers)
keys.register()
print(json.dumps({'lock_released_and_explicit_test_registration_succeeded':True}))
""".replace("CREDS", repr((KEY, SECRET)))
        )
        assert json.loads(docker("start", "--attach", "--interactive", renew, body=renew_body))[
            "lock_released_and_explicit_test_registration_succeeded"
        ]
        expired = create("read-helper", read_mounts)
        expired_body = payload("""
import json
from datetime import datetime,UTC,timedelta
from services.execution.demo_copy_volatile_credentials import PrivateReadCredentialLease
from services.execution.demo_runtime_storage import RuntimeClocks
from packages.persistence.demo_order_quota import LocalQuotaStopped
try:
 PrivateReadCredentialLease('DEMO-test','DEMO-private',enable_private_read=True,copy_only=True,
  clocks=RuntimeClocks(wall_clock=lambda:datetime.now(UTC)+timedelta(hours=13)))
 raise RuntimeError('SYNTHETIC_EXPIRED_FILE_ACCEPTED')
except LocalQuotaStopped as error:
 assert str(error)=='PRIVATE_READ_EXPIRED_OR_CLOCK_CHANGED'
 print(json.dumps({'expired_session_denied':True}))
""")
        assert json.loads(docker("start", "--attach", "--interactive", expired, body=expired_body))[
            "expired_session_denied"
        ]
        # No container still mounts the tmpfs after this holder stops.
        docker("stop", "--time", "2", holder)
        missing = create("read-helper", read_mounts)
        missing_body = payload("""
import json
from services.execution.demo_copy_volatile_credentials import PrivateReadCredentialLease
from packages.persistence.demo_order_quota import LocalQuotaStopped
try:
 PrivateReadCredentialLease('DEMO-test','DEMO-private',enable_private_read=True,copy_only=True)
 raise RuntimeError('SYNTHETIC_SESSION_SURVIVED_LAST_MOUNT')
except LocalQuotaStopped as error:
 assert str(error)=='PRIVATE_READ_SINGLE_SESSION_FILE_REQUIRED'
 print(json.dumps({'last_unmount_drops_session':True}))
""")
        assert json.loads(docker("start", "--attach", "--interactive", missing, body=missing_body))[
            "last_unmount_drops_session"
        ]
        report = dict(
            real_docker_tmpfs=True,
            network_mode="none",
            root_filesystem_readonly=True,
            read_helper_key_mount_readonly=True,
            registrar_has_only_key_volume=True,
            no_credential_environment_or_arguments=True,
            docker_log_driver="none",
            shared_lease_blocks_clear=True,
            explicit_close_releases_lock=True,
            expired_session_denied=True,
            last_unmount_drops_session=True,
            real_credentials_used=False,
            actual_http_attempts=0,
            original_containers_touched=False,
            host_swap_or_memory_snapshot_protection_verified=False,
            scope_policy=policy,
        )
        if os.getenv("PRIVATE_READ_DOCKER_REPORT"):
            Path(os.environ["PRIVATE_READ_DOCKER_REPORT"]).write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n"
            )
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
        for cid in reversed(containers):
            subprocess.run(["docker", "rm", "--force", cid], capture_output=True, timeout=15)
        for volume in reversed(volumes):
            subprocess.run(["docker", "volume", "rm", volume], capture_output=True, timeout=15)
    assert set(docker("ps", "--all", "--quiet", "--no-trunc").splitlines()) == original_ids
    assert set(docker("volume", "ls", "--format", "{{.Name}}").splitlines()) == original_volumes
