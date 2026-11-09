"""Synthetic CLI tests model the container's private RAM credential mount.

Filesystem/mount rejection is tested without this fixture in unit tests and with
real tmpfs in the isolated Docker lifecycle probe. No real credentials are used.
"""

import pytest

from scripts import demo_key_session as keys


@pytest.fixture(autouse=True)
def private_credential_mount(tmp_path, monkeypatch):
    root = tmp_path / "synthetic-private-ram"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(keys, "DIRECTORY", root)
    monkeypatch.setattr(keys, "_tmpfs", lambda: True)
