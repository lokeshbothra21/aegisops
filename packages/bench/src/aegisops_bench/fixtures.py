"""Export a captured scenario to a compressed fixture and import it elsewhere (E1.6).

Format: gzip'd JSON Lines, one line per row, with a `_table` field, plus a header line
holding the scenarios row. Portable across Postgres instances (CI, Supabase) without
pg_dump; ids are not preserved (rows are re-inserted).
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from aegisops_tools.db import session_factory

TABLES = ["spans", "logs", "metric_points", "change_events", "service_edges", "incidents"]
SKIP_COLUMNS = {"id", "created_at"}
TIMESTAMP_COLUMNS = {"ts", "start_ts", "window_start", "window_end", "opened_at", "closed_at"}
JSONB_COLUMNS = {"attrs", "before", "after"}


def _bind(col: str) -> str:
    return f"CAST(:{col} AS jsonb)" if col in JSONB_COLUMNS else f":{col}"


def _coerce(rec: dict[str, Any]) -> dict[str, Any]:
    """asyncpg wants datetime objects for timestamp columns and JSON text for jsonb."""
    out = dict(rec)
    for k, v in rec.items():
        if k in TIMESTAMP_COLUMNS and isinstance(v, str):
            out[k] = datetime.fromisoformat(v)
        elif isinstance(v, (dict, list)) and k in JSONB_COLUMNS:
            out[k] = json.dumps(v)
    return out


def _jsonable(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def _prepare(out_dir: Path, key: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{key}.jsonl.gz"


async def export_scenario(engine: AsyncEngine, key: str, out_dir: Path) -> Path:
    path = _prepare(out_dir, key)
    async with session_factory(engine)() as s:
        f = gzip.open(path, "wt")
        meta = (
            (await s.execute(text("SELECT * FROM scenarios WHERE key = :k"), {"k": key}))
            .mappings()
            .first()
        )
        if meta is None:
            raise KeyError(f"scenario {key!r} not captured")
        f.write(
            json.dumps(
                {
                    "_table": "scenarios",
                    **{k: _jsonable(v) for k, v in meta.items() if k not in SKIP_COLUMNS},
                }
            )
            + "\n"
        )
        for table in TABLES:
            select_sql = f"SELECT * FROM {table} WHERE scenario_id = :k"  # noqa: S608 - fixed list
            rows = (await s.execute(text(select_sql), {"k": key})).mappings()
            for row in rows:
                rec = {
                    "_table": table,
                    **{k: _jsonable(v) for k, v in row.items() if k not in SKIP_COLUMNS},
                }
                if table == "incidents":
                    rec.pop("alert_rule_id", None)  # rule ids differ per database
                f.write(json.dumps(rec, default=str) + "\n")
        f.close()
    return path


async def import_scenario(
    engine: AsyncEngine, path: Path, *, replace: bool = True
) -> dict[str, int]:
    counts: dict[str, int] = {}
    async with session_factory(engine)() as s:
        f = gzip.open(path, "rt")
        header = json.loads(f.readline())
        key = header["key"]
        if replace:
            for table in [*TABLES, "scenarios"]:
                col = "key" if table == "scenarios" else "scenario_id"
                await s.execute(text(f"DELETE FROM {table} WHERE {col} = :k"), {"k": key})  # noqa: S608
        cols = [c for c in header if c != "_table"]
        names, binds = ", ".join(cols), ", ".join(_bind(c) for c in cols)
        meta_sql = f"INSERT INTO scenarios ({names}) VALUES ({binds})"  # noqa: S608 - our own export
        await s.execute(
            text(meta_sql),
            _coerce({c: header[c] for c in cols}),
        )
        for line in f:
            rec = json.loads(line)
            table = rec.pop("_table")
            rec = _coerce(rec)
            cols = list(rec)
            placeholders = ", ".join(
                f"CAST(:{c} AS jsonb)" if c in ("attrs", "before", "after") else f":{c}"
                for c in cols
            )
            row_sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"  # noqa: S608
            await s.execute(text(row_sql), rec)
            counts[table] = counts.get(table, 0) + 1
        f.close()
        await s.commit()
    return counts
