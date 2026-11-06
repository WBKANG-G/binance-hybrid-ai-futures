"""A new migration must update the exact API readiness revision too."""

from alembic.config import Config
from alembic.script import ScriptDirectory

from packages.persistence.health import EXPECTED_SCHEMA_REVISION


def test_api_readiness_revision_equals_current_alembic_head():
    assert (
        EXPECTED_SCHEMA_REVISION
        == ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    )
