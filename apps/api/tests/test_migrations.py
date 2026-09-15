"""The schema in the database must equal the models: no drift, no hand edits."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, inspect

from aegisops_api.db import create_engine
from aegisops_api.models import Base
from aegisops_api.settings import Settings


def _tables_and_diff(conn: Connection) -> tuple[set[str], list[object]]:
    tables = set(inspect(conn).get_table_names())
    ctx = MigrationContext.configure(conn)
    return tables, compare_metadata(ctx, Base.metadata)


async def test_migrated_schema_matches_models(settings: Settings) -> None:
    engine = create_engine(settings)
    try:
        async with engine.connect() as conn:
            tables, diff = await conn.run_sync(_tables_and_diff)
    finally:
        await engine.dispose()
    assert {"spans", "logs", "metric_points", "alembic_version"} <= tables
    assert diff == [], f"models and migrations have drifted: {diff}"
