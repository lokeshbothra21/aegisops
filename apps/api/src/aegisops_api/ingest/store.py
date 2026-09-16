"""Bulk insert of converted rows. One `executemany` per request, one transaction.

SQLAlchemy 2.0 batches `insert(...)` + list-of-dicts into multi-row INSERTs
("insertmanyvalues"), which is what makes NFR-04 (≥ 500 spans/s) cheap.
"""

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from aegisops_api.ingest.convert import Row
from aegisops_api.models import Log, MetricPoint, Span


async def insert_spans(session: AsyncSession, rows: list[Row]) -> None:
    if rows:
        await session.execute(insert(Span), rows)


async def insert_logs(session: AsyncSession, rows: list[Row]) -> None:
    if rows:
        await session.execute(insert(Log), rows)


async def insert_metric_points(session: AsyncSession, rows: list[Row]) -> None:
    if rows:
        await session.execute(insert(MetricPoint), rows)
