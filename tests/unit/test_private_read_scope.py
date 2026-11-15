"""No Docker calls: strict metadata policy rejects expanded private helper scope."""

import copy

import pytest

from packages.persistence.demo_order_quota import LocalQuotaStopped
from services.execution.demo_private_read_scope import validate_private_read_scope


def records():
    token = "a" * 32
    names = (
        "demo-read-private-keys-" + token,
        "demo-copy-review-runtime-" + token,
        "demo-copy-review-socket-" + token,
    )
    image = "sha256:" + "b" * 64
    values = []
    for i, role in enumerate(("key-holder", "read-helper", "registrar")):
        mounts = [dict(Type="volume", Name=names[0], Destination="/run/demo-keys", RW=i == 2)]
        if i == 1:
            mounts += [
                dict(Type="volume", Name=names[1], Destination="/review-runtime", RW=True),
                dict(Type="volume", Name=names[2], Destination="/review-socket", RW=False),
            ]
        values.append(
            dict(
                Id=str(i + 1) * 64,
                Image=image,
                Mounts=mounts,
                State=dict(
                    Status="created", Running=False, Paused=False, Dead=False, Restarting=False
                ),
                Config=dict(
                    User="10001:10001",
                    Labels={
                        "com.docker.compose.project": "demo-read-private",
                        "com.docker.compose.service": role,
                    },
                    Env=["PYTHON_VERSION=3.12"],
                    Entrypoint=["python"],
                    Cmd=["-I", "-B", "-c", "import signal; signal.pause()"]
                    if i == 0
                    else ["-I", "-B", "-"],
                ),
                HostConfig=dict(
                    NetworkMode="none",
                    ReadonlyRootfs=True,
                    Privileged=False,
                    CapDrop=["ALL"],
                    CapAdd=[],
                    RestartPolicy=dict(Name="no", MaximumRetryCount=0),
                    SecurityOpt=["no-new-privileges"],
                    PidMode="",
                    IpcMode="private",
                    Devices=[],
                    PortBindings={},
                    PublishAllPorts=False,
                    LogConfig=dict(Type="none"),
                    Memory=256 * 1024 * 1024,
                    PidsLimit=32,
                ),
            )
        )
    kw = dict(
        image=image,
        keys_volume=names[0],
        runtime_volume=names[1],
        socket_volume=names[2],
        owned_container_ids={v["Id"] for v in values},
        forbidden_container_ids={"f" * 64},
        forbidden_volume_names={"original-db", "original-runtime"},
        enable_review=True,
    )
    return values, kw


def test_disabled_precedes_metadata_access():
    with pytest.raises(LocalQuotaStopped, match="REVIEW_DISABLED"):
        validate_private_read_scope(
            None,
            None,
            None,
            image="",
            keys_volume="",
            runtime_volume="",
            socket_volume="",
            owned_container_ids=set(),
            forbidden_container_ids=set(),
            forbidden_volume_names=set(),
        )


def test_only_owned_readonly_keys_and_fresh_copy_mounts_are_accepted():
    values, kw = records()
    report = validate_private_read_scope(*values, **kw)
    assert report["reader_key_mount_readonly"] and report["registrar_has_only_key_volume"]
    assert not report["network_enabled"] and not report["copy_isolation_independently_verified"]


@pytest.mark.parametrize(
    "fault",
    [
        "rw_key",
        "extra_mount",
        "source_alias",
        "host_network",
        "root",
        "capability",
        "restart",
        "privileged",
        "writable_root",
        "secret_env",
        "cmd_secret",
        "log_driver",
        "unknown_container",
        "socket_write",
        "holder_runtime",
        "host_pid",
        "host_ipc",
        "extra_group",
        "paused",
    ],
)
def test_expanded_or_aliased_scope_is_rejected(fault):
    values, kw = records()
    reader = values[1]
    if fault == "rw_key":
        reader["Mounts"][0]["RW"] = True
    if fault == "extra_mount":
        reader["Mounts"].append(
            dict(Type="bind", Name="", Destination="/var/run/docker.sock", RW=True)
        )
    if fault == "source_alias":
        kw["forbidden_volume_names"].add(kw["runtime_volume"])
    if fault == "host_network":
        reader["HostConfig"]["NetworkMode"] = "host"
    if fault == "root":
        reader["Config"]["User"] = "0:0"
    if fault == "capability":
        reader["HostConfig"]["CapAdd"] = ["SYS_ADMIN"]
    if fault == "restart":
        reader["HostConfig"]["RestartPolicy"]["Name"] = "always"
    if fault == "privileged":
        reader["HostConfig"]["Privileged"] = True
    if fault == "writable_root":
        reader["HostConfig"]["ReadonlyRootfs"] = False
    if fault == "secret_env":
        reader["Config"]["Env"].append("BINANCE_DEMO_API_KEY=SYNTHETIC_MUST_NOT_LOG")
    if fault == "cmd_secret":
        reader["Config"]["Cmd"].append("SYNTHETIC_MUST_NOT_LOG")
    if fault == "log_driver":
        reader["HostConfig"]["LogConfig"]["Type"] = "json-file"
    if fault == "unknown_container":
        kw["owned_container_ids"].remove(reader["Id"])
    if fault == "socket_write":
        reader["Mounts"][2]["RW"] = True
    if fault == "holder_runtime":
        values[0]["Mounts"].append(copy.deepcopy(reader["Mounts"][1]))
    if fault == "host_pid":
        reader["HostConfig"]["PidMode"] = "host"
    if fault == "host_ipc":
        reader["HostConfig"]["IpcMode"] = "host"
    if fault == "extra_group":
        reader["HostConfig"]["GroupAdd"] = ["0"]
    if fault == "paused":
        reader["State"]["Paused"] = True
    with pytest.raises(LocalQuotaStopped) as error:
        validate_private_read_scope(*values, **kw)
    assert "SYNTHETIC_MUST_NOT_LOG" not in str(error.value)
