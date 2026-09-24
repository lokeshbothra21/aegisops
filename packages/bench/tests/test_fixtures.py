"""Export a captured scenario to a fixture and import it back (E1.6)."""

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text

from aegisops_bench.fixtures import export_scenario, import_scenario
from aegisops_tools.db import engine_from_env, session_factory
from aegisops_tools.testing import NOW, delete_scenario, seed_payment_failure


async def test_export_import_roundtrip(tmp_path: Path) -> None:
    engine = engine_from_env()
    sc = f"FX-{uuid4().hex[:6]}"
    try:
        await seed_payment_failure(engine, sc)
        async with session_factory(engine)() as s:
            await s.execute(
                text("""
                INSERT INTO scenarios (key, title, fault_type, expected_service, expected_category, expected_actions, window_start, window_end)
                VALUES (:k, 'fixture test', 'flag', 'payment', 'dependency_errors', ARRAY['toggle_flag'], :ws, :we)
            """),
                {"k": sc, "ws": NOW - timedelta(minutes=80), "we": NOW},
            )
            await s.commit()
            before = {
                t: await s.scalar(
                    text(f"SELECT count(*) FROM {t} WHERE scenario_id = :k"), {"k": sc}
                )
                for t in ("spans", "logs", "metric_points", "change_events", "service_edges")
            }
        path = await export_scenario(engine, sc, tmp_path)
        assert path.name == f"{sc}.jsonl.gz" and path.stat().st_size > 200
        counts = await import_scenario(engine, path)  # replace=True: deletes and re-inserts
        async with session_factory(engine)() as s:
            after = {
                t: await s.scalar(
                    text(f"SELECT count(*) FROM {t} WHERE scenario_id = :k"), {"k": sc}
                )
                for t in before
            }
            meta = (
                await s.execute(
                    text("SELECT title, expected_actions FROM scenarios WHERE key = :k"), {"k": sc}
                )
            ).first()
            attrs = await s.scalar(
                text(
                    "SELECT attrs->'otel.events'->0->'attrs'->>'exception.message' FROM spans WHERE scenario_id = :k AND service = 'payment'"
                ),
                {"k": sc},
            )
    finally:
        await delete_scenario(engine, sc)
        async with session_factory(engine)() as s:
            await s.execute(text("DELETE FROM scenarios WHERE key = :k"), {"k": sc})
            await s.commit()
        await engine.dispose()
    assert before == after and sum(before.values()) > 20
    assert {t: n for t, n in counts.items() if t != "incidents"} == before
    assert meta is not None and meta[0] == "fixture test" and list(meta[1]) == ["toggle_flag"]
    assert attrs == "Payment request failed. Invalid token."  # JSONB survived the round trip


async def test_replay_clock_is_the_first_incident_time() -> None:
    from datetime import UTC, datetime

    from aegisops_bench.cli import replay_clock

    engine = engine_from_env()
    key = f"RC-{uuid4().hex[:6]}"
    t_end = datetime(2036, 4, 4, 12, 0, tzinfo=UTC)
    try:
        async with session_factory(engine)() as s:
            await s.execute(
                text("""
                INSERT INTO scenarios (key, title, fault_type, expected_service, expected_actions, window_start, window_end)
                VALUES (:k, 't', 'flag', 'payment', ARRAY[]::varchar[], :ws, :we)
            """),
                {"k": key, "ws": t_end - timedelta(minutes=10), "we": t_end},
            )
            await s.commit()
            assert await replay_clock(engine, key) == (
                "payment",
                t_end,
            )  # no incident yet -> window end
            await s.execute(
                text("""
                INSERT INTO incidents (opened_at, service, status, autonomy_level, scenario_id)
                VALUES (:t1, 'checkout', 'resolved', 1, :k), (:t0, 'payment', 'resolved', 1, :k)
            """),
                {"t0": t_end - timedelta(minutes=7), "t1": t_end - timedelta(minutes=6), "k": key},
            )
            await s.commit()
            assert await replay_clock(engine, key) == ("payment", t_end - timedelta(minutes=7))
            assert await replay_clock(engine, "nope") is None
    finally:
        async with session_factory(engine)() as s:
            await s.execute(text("DELETE FROM incidents WHERE scenario_id = :k"), {"k": key})
            await s.execute(text("DELETE FROM scenarios WHERE key = :k"), {"k": key})
            await s.commit()
        await engine.dispose()
