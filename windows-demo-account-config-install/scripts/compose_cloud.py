"""Run Compose with the cloud proxy's existing DNS and public CA configuration.

No credential values are read from files or printed. Predefined Docker proxy build
arguments are forwarded through the environment and are not image configuration.
"""

import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit


def main() -> int:
    proxy = os.environ.get("HTTPS_PROXY", "")
    host = urlsplit(proxy).hostname
    ca_path = os.environ.get("SSL_CERT_FILE", "")
    if not host or not ca_path or not Path(ca_path).is_file():
        print(
            "Cloud proxy and public CA bundle are required; use standard Compose on local machines.",
            file=sys.stderr,
        )
        return 2
    try:
        address = socket.gethostbyname(host)
    except OSError:
        print("Cloud proxy DNS resolution failed.", file=sys.stderr)
        return 2
    environment = os.environ.copy()
    environment["CLOUD_PROXY_HOST"] = host
    environment["CLOUD_PROXY_IP"] = address
    environment.setdefault("DOCKER_CONFIG", "/workspace/.cache/docker")
    environment.setdefault("COMPOSE_PARALLEL_LIMIT", "2")
    Path(environment["DOCKER_CONFIG"]).mkdir(parents=True, exist_ok=True)
    command = [
        "docker",
        "compose",
        "-f",
        "infra/docker-compose.yml",
        "-f",
        "infra/docker-compose.cloud.yml",
        *sys.argv[1:],
    ]
    return subprocess.run(
        command, cwd=Path(__file__).resolve().parents[1], env=environment, check=False
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
