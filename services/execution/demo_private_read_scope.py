"""Pure Docker-inspect policy for caller-owned, synthetic private read helpers.

Does not discover resources, create containers, load credentials or run Docker.
The external controller must independently supply fresh IDs and forbidden sources.
"""

import re
from typing import Any

from packages.persistence.demo_read_audit import require
from services.execution.demo_copy_volatile_credentials import CREDENTIAL_ENV


def validate_private_read_scope(
    holder: dict[str, Any],
    reader: dict[str, Any],
    registrar: dict[str, Any],
    *,
    image: str,
    keys_volume: str,
    runtime_volume: str,
    socket_volume: str,
    owned_container_ids: set[str],
    forbidden_container_ids: set[str],
    forbidden_volume_names: set[str],
    enable_review: bool = False,
) -> dict[str, Any]:
    require(enable_review is True, "PRIVATE_SCOPE_REVIEW_DISABLED")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", image) is not None, "PRIVATE_SCOPE_IMAGE_INVALID")
    names = (keys_volume, runtime_volume, socket_volume)
    require(
        len(set(names)) == 3
        and not set(names) & forbidden_volume_names
        and re.fullmatch(r"demo-read-private-keys-[0-9a-f]{32}", keys_volume) is not None
        and re.fullmatch(r"demo-copy-review-runtime-[0-9a-f]{32}", runtime_volume) is not None
        and re.fullmatch(r"demo-copy-review-socket-[0-9a-f]{32}", socket_volume) is not None,
        "PRIVATE_SCOPE_VOLUME_IDENTITY_INVALID",
    )
    ids = {obj.get("Id") for obj in (holder, reader, registrar)}
    require(
        len(ids) == 3
        and ids == owned_container_ids
        and not ids & forbidden_container_ids
        and all(isinstance(i, str) and re.fullmatch(r"[0-9a-f]{64}", i) for i in ids),
        "PRIVATE_SCOPE_CONTAINER_IDENTITY_INVALID",
    )
    for role, obj, spec in (
        ("key-holder", holder, {"/run/demo-keys": (keys_volume, False)}),
        (
            "read-helper",
            reader,
            {
                "/run/demo-keys": (keys_volume, False),
                "/review-runtime": (runtime_volume, True),
                "/review-socket": (socket_volume, False),
            },
        ),
        ("registrar", registrar, {"/run/demo-keys": (keys_volume, True)}),
    ):
        config, host, state = obj["Config"], obj["HostConfig"], obj["State"]
        require(
            obj["Image"] == image
            and config["User"] == "10001:10001"
            and config["Labels"]["com.docker.compose.project"] == "demo-read-private"
            and config["Labels"]["com.docker.compose.service"] == role,
            "PRIVATE_SCOPE_ROLE_OR_IMAGE_INVALID",
        )
        require(
            host["NetworkMode"] == "none"
            and host["ReadonlyRootfs"] is True
            and host["Privileged"] is False
            and set(host["CapDrop"] or []) == {"ALL"}
            and not host["CapAdd"]
            and host["RestartPolicy"] == {"Name": "no", "MaximumRetryCount": 0}
            and set(host["SecurityOpt"] or [])
            in ({"no-new-privileges"}, {"no-new-privileges:true"})
            and host["PidMode"] == ""
            and host["IpcMode"] == "private"
            and not host["Devices"]
            and not host["PortBindings"]
            and not host["PublishAllPorts"]
            and not host.get("Tmpfs")
            and host["LogConfig"]["Type"] == "none"
            and not host.get("GroupAdd")
            and 0 < host["Memory"] <= 512 * 1024 * 1024
            and 0 < host["PidsLimit"] <= 64,
            "PRIVATE_SCOPE_SANDBOX_INVALID",
        )
        require(
            state["Paused"] is False
            and state["Dead"] is False
            and state["Restarting"] is False
            and state["Status"] in {"created", "running"}
            and state["Running"] is (state["Status"] == "running"),
            "PRIVATE_SCOPE_STATE_INVALID",
        )
        require(
            config["Entrypoint"] == ["python"]
            and config["Cmd"]
            == (
                ["-I", "-B", "-c", "import signal; signal.pause()"]
                if role == "key-holder"
                else ["-I", "-B", "-"]
            ),
            "PRIVATE_SCOPE_COMMAND_INVALID",
        )
        require(
            len(config["Env"] or []) <= 64
            and all(
                isinstance(e, str) and len(e) <= 4096 and e.split("=", 1)[0] not in CREDENTIAL_ENV
                for e in config["Env"] or []
            ),
            "PRIVATE_SCOPE_ENV_CREDENTIALS_FORBIDDEN",
        )
        mounts = obj["Mounts"]
        require(
            len(mounts) == len(spec) and len({m["Destination"] for m in mounts}) == len(spec),
            "PRIVATE_SCOPE_MOUNT_SET_INVALID",
        )
        for mount in mounts:
            destination = mount["Destination"]
            require(
                destination in spec
                and mount["Type"] == "volume"
                and mount["Name"] == spec[destination][0]
                and mount["RW"] is spec[destination][1],
                "PRIVATE_SCOPE_MOUNT_INVALID",
            )
    return dict(
        private_read_scope="OWNED_RO_KEY_AND_FRESH_COPY_MOUNTS_CHECKED",
        registrar_has_only_key_volume=True,
        reader_key_mount_readonly=True,
        network_enabled=False,
        docker_socket_mounted=False,
        external_controller_freshness_attested=True,
        copy_isolation_independently_verified=False,
        remote_account_identity_verified=False,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
    )
