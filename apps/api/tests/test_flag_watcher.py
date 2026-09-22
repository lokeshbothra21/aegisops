"""flagd file watcher: baseline, diff, service mapping, missing file (E2.1)."""

import json
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from aegisops_api.db import create_engine, create_session_factory
from aegisops_api.jobs.flag_watcher import ACTOR, FlagWatcher, diff_variants, read_variants
from aegisops_api.models import ChangeEvent, ChangeType
from aegisops_api.settings import Settings
from aegisops_api.targets import load_target

DOC = {
    "flags": {
        "paymentFailure": {"defaultVariant": "off", "variants": {"off": 0, "100%": 1}},
        "cartFailure": {"defaultVariant": "off", "variants": {"off": False, "on": True}},
        "mysteryFlag": {"defaultVariant": "a", "variants": {"a": 1, "b": 2}},
    }
}


def _write(path: Path, doc: dict, mtime: float) -> None:  # type: ignore[type-arg]
    path.write_text(json.dumps(doc))
    os.utime(path, (mtime, mtime))  # deterministic mtime so ticks see a change


def test_read_and_diff() -> None:
    before = {"f": {"variant": "off", "value": 0}}
    after = {"f": {"variant": "on", "value": 1}, "g": {"variant": "x", "value": 9}}
    changes = diff_variants(before, after)
    assert [c[0] for c in changes] == ["f", "g"]
    assert changes[0][1]["variant"] == "off" and changes[0][2]["variant"] == "on"


def test_target_config_maps_flags_to_services() -> None:
    t = load_target("config/targets/otel-demo.yaml")
    assert t.name == "otel-demo"
    assert t.service_for_flag("paymentFailure") == "payment"
    assert t.service_for_flag("kafkaQueueProblems") == "checkout"
    assert t.service_for_flag("nope") is None
    assert len(t.flags) == 15


async def test_watcher_records_flag_changes(settings: Settings, tmp_path: Path) -> None:
    path = tmp_path / "demo.flagd.json"
    _write(path, DOC, 1_000_000)
    watcher = FlagWatcher(path=path, target=load_target("config/targets/otel-demo.yaml"))
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    marker = uuid4().hex  # find our rows among others: put it in the doc as a fake flag name
    try:
        async with factory() as s:
            assert await watcher.tick(s) == 0  # baseline records nothing
            assert await watcher.tick(s) == 0  # unchanged mtime -> no work
            doc = json.loads(json.dumps(DOC))
            doc["flags"]["paymentFailure"]["defaultVariant"] = "100%"
            doc["flags"]["mysteryFlag"]["defaultVariant"] = "b"
            doc["flags"][marker] = {"defaultVariant": "z", "variants": {"z": 0}}  # new flag
            _write(path, doc, 1_000_001)
            assert await watcher.tick(s) == 3
            await s.commit()
            new_ids = [
                r.id
                for r in (
                    await s.scalars(
                        select(ChangeEvent)
                        .where(ChangeEvent.actor == ACTOR)
                        .order_by(ChangeEvent.id.desc())
                        .limit(3)
                    )
                ).all()
            ]
            rows = (
                await s.scalars(
                    select(ChangeEvent)
                    .where(ChangeEvent.type == ChangeType.flag)
                    .order_by(ChangeEvent.id.desc())
                    .limit(3)
                )
            ).all()
            from sqlalchemy import delete

            await s.execute(
                delete(ChangeEvent).where(ChangeEvent.id.in_(new_ids))
            )  # not real live changes
            await s.commit()
    finally:
        await engine.dispose()
    by_flag = {r.after["flag"]: r for r in rows}
    pay = by_flag["paymentFailure"]
    assert pay.service == "payment" and pay.actor == ACTOR
    assert pay.before == {"flag": "paymentFailure", "variant": "off", "value": 0}
    assert pay.after == {"flag": "paymentFailure", "variant": "100%", "value": 1}
    assert by_flag["mysteryFlag"].service is None  # unknown flag: kept, service unknown
    assert by_flag[marker].before == {"flag": marker}  # flag appeared: empty "before"


async def test_missing_file_is_logged_not_fatal(settings: Settings, tmp_path: Path) -> None:
    watcher = FlagWatcher(
        path=tmp_path / "nope.json", target=load_target("config/targets/otel-demo.yaml")
    )
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as s:
            assert await watcher.tick(s) == 0
    finally:
        await engine.dispose()


def test_read_variants_tolerates_missing_variant() -> None:
    p = Path(__file__).parent / "fixtures" / "_tmp_flags.json"
    p.write_text(json.dumps({"flags": {"x": {"variants": {"a": 1}}}}))
    try:
        assert read_variants(p) == {"x": {"variant": None, "value": None}}
    finally:
        p.unlink()
