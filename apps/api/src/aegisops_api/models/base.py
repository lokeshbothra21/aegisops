from datetime import datetime

from sqlalchemy import BigInteger, DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampedRow:
    """Mixin: surrogate key + creation time, shared by every table (PROJECT.md §6)."""

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# Tables owned by other libraries (LangGraph's Postgres checkpointer creates and migrates
# its own `checkpoint*` tables). Alembic autogenerate and the drift test skip them.
FOREIGN_TABLE_PREFIXES = ("checkpoint",)


def include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Alembic `include_object` hook: ignore tables/indexes that are not ours."""
    if type_ == "table" and name is not None and name.startswith(FOREIGN_TABLE_PREFIXES):
        return False
    table = getattr(obj, "table", None)
    if type_ == "index" and str(getattr(table, "name", "")).startswith(FOREIGN_TABLE_PREFIXES):
        return False
    return True
