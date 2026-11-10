"""A separate connection pool in the existing isolated test schema."""

from sqlalchemy import create_engine, text

from packages.persistence.demo_shared_checkpoint import SharedWeightAnchorStore


def provision_anchor(store):
    with store.engine.connect() as connection:
        schema = connection.execute(text("SELECT current_schema()")).scalar_one()
    assert schema.startswith("phase4_") and schema.replace("_", "").isalnum()
    engine = create_engine(
        store.engine.url,
        connect_args={"options": "-csearch_path=" + schema},
        pool_size=3,
        max_overflow=2,
    )
    anchors = SharedWeightAnchorStore(engine)
    anchors.provision()
    return anchors
