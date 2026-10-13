"""aegis-scenario: list | run | capture-export | import | investigate

aegis-scenario list
aegis-scenario show S1
aegis-scenario run S1 --api http://localhost:8000 --admin-token $AEGIS_ADMIN_TOKEN
aegis-scenario export S1            -> bench/fixtures/S1.jsonl.gz
aegis-scenario import bench/fixtures/S1.jsonl.gz
aegis-scenario investigate S1 [--recorded cassette.yaml]   (replay with frozen now = window_end)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from aegisops_bench.catalogue import (
    DEFAULT_PATH,
    Catalogue,
    FlagFault,
    NoFault,
    OverlayFault,
    load_catalogue,
)
from aegisops_bench.fixtures import export_scenario, import_scenario
from aegisops_bench.runner import Runner
from aegisops_tools.db import engine_from_env, session_factory

FIXTURES = DEFAULT_PATH.parent / "fixtures"
FLAGD = DEFAULT_PATH.parents[1] / "opentelemetry-demo" / "src" / "flagd" / "demo.flagd.json"


async def _run(args: argparse.Namespace) -> int:
    cat = load_catalogue(args.catalogue)
    spec = cat.get(args.key)
    runner = Runner(
        api=args.api.rstrip("/"),
        admin_token=args.admin_token,
        flagd_path=Path(args.flagd),
        ingest_token=args.ingest_token,
    )
    rep = await runner.run(spec)
    print(json.dumps(rep.model_dump(mode="json"), indent=2))
    return 0 if (rep.incident_id or spec.expected_service is None) else 1


async def _export(args: argparse.Namespace) -> int:
    engine = engine_from_env()
    try:
        path = await export_scenario(engine, args.key, Path(args.out))
    finally:
        await engine.dispose()
    print(path)
    return 0


async def _import(args: argparse.Namespace) -> int:
    engine = engine_from_env()
    try:
        counts = await import_scenario(engine, Path(args.path))
    finally:
        await engine.dispose()
    print(json.dumps(counts))
    return 0


async def replay_clock(engine: AsyncEngine, key: str) -> tuple[str | None, datetime] | None:
    """(expected_service, frozen_now) for a captured scenario, or None if not captured.

    "Now" is when the expected service's first incident opened: the agent investigates as
    of the alert, so a revert later in the window cannot outrank the fault in change
    correlation. Falls back to the window end when no such incident was captured."""
    async with session_factory(engine)() as s:
        row = (
            await s.execute(
                text("SELECT expected_service, window_end FROM scenarios WHERE key = :k"),
                {"k": key},
            )
        ).first()
        if row is None:
            return None
        # the expected service's own incident; other services' incidents in the window may be
        # cascades or false positives (a flagd-ui memory alert opened before the S1 fault)
        opened = await s.scalar(
            text(
                "SELECT min(opened_at) FROM incidents WHERE scenario_id = :k "
                "AND (CAST(:svc AS text) IS NULL OR service = CAST(:svc AS text))"
            ),
            {"k": key, "svc": row[0]},
        )
    return row[0], (opened or row[1])


async def _investigate_cmd(args: argparse.Namespace) -> list[str] | None:
    """Build the aegis-investigate argv for a captured scenario (None if not captured)."""
    engine = engine_from_env()
    try:
        clock = await replay_clock(engine, args.key)
    finally:
        await engine.dispose()
    if clock is None:
        return None
    service, window_end = clock
    cmd = [
        "uv",
        "run",
        "aegis-investigate",
        "--service",
        service or "unknown",
        "--alert",
        args.alert or f"replay of {args.key}",
        "--scenario",
        args.key,
        "--frozen-now",
        window_end.isoformat(),
    ]
    if args.recorded:
        cmd += ["--recorded", args.recorded]
    return cmd


def _investigate(args: argparse.Namespace) -> int:
    cmd = asyncio.run(_investigate_cmd(args))
    if cmd is None:
        print(f"scenario {args.key} not captured", file=sys.stderr)
        return 2
    return subprocess.call(cmd)  # noqa: S603 - fixed argv, no shell


def _describe_fault(fault: FlagFault | OverlayFault | NoFault) -> str:
    if isinstance(fault, FlagFault):
        return f"flag {fault.flag} = {fault.variant} (reverts to {fault.revert})"
    if isinstance(fault, OverlayFault):
        return f"overlay {fault.name}" + ("" if fault.available else " (not built yet)")
    return "none"


def _show(cat: Catalogue, key: str) -> int:
    """Print one scenario's definition. Unset fields are left out; the key match ignores case."""
    spec = next((s for s in cat.scenarios if s.key.upper() == key.upper()), None)
    if spec is None:
        valid = ", ".join(s.key for s in cat.scenarios)
        print(f"unknown scenario {key!r}; valid keys: {valid}", file=sys.stderr)
        return 1
    # only the timing fields the YAML sets; the defaults are not the scenario's own
    timing = ", ".join(f"{k} {v}s" for k, v in spec.timing.model_dump(exclude_unset=True).items())
    inject = None
    if spec.inject_log is not None:
        log = spec.inject_log
        inject = f"{log.service} log (severity {log.severity_num}): {log.body}"
    rows = [
        ("key", spec.key),
        ("title", spec.title),
        ("set", spec.set.value),
        ("fault", _describe_fault(spec.fault)),
        ("inject log", inject),
        ("expected service", spec.expected_service),
        ("expected category", spec.expected_category),
        ("expected actions", ", ".join(spec.expected_actions) or None),
        ("timing", timing or None),
        ("notes", spec.notes or None),
    ]
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        if value is not None:
            print(f"{label:{width}s}  {value}")
    return 0


def main() -> None:
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    p = argparse.ArgumentParser(prog="aegis-scenario")
    p.add_argument("--catalogue", default=str(DEFAULT_PATH))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sh = sub.add_parser("show")
    sh.add_argument("key")
    r = sub.add_parser("run")
    r.add_argument("key")
    r.add_argument("--api", default=os.environ.get("AEGIS_API", "http://localhost:8000"))
    r.add_argument("--admin-token", default=os.environ.get("AEGIS_ADMIN_TOKEN", ""))
    r.add_argument("--ingest-token", default=os.environ.get("AEGIS_INGEST_TOKEN", ""))
    r.add_argument("--flagd", default=os.environ.get("AEGIS_FLAGD_CONFIG_PATH", str(FLAGD)))
    e = sub.add_parser("export")
    e.add_argument("key")
    e.add_argument("--out", default=str(FIXTURES))
    i = sub.add_parser("import")
    i.add_argument("path")
    v = sub.add_parser("investigate")
    v.add_argument("key")
    v.add_argument("--alert", default=None)
    v.add_argument("--recorded", default=None)
    args = p.parse_args()
    if args.cmd == "list":
        for sc in load_catalogue(args.catalogue).scenarios:
            print(
                f"{sc.key:4s} {sc.set.value:9s} {sc.fault_type:8s} "
                f"{sc.expected_service or '-':16s} {sc.expected_category or '-':20s} {sc.title}"
            )
        sys.exit(0)
    if args.cmd == "show":
        sys.exit(_show(load_catalogue(args.catalogue), args.key))
    if args.cmd == "investigate":
        sys.exit(_investigate(args))
    fn = {"run": _run, "export": _export, "import": _import}[args.cmd]
    sys.exit(asyncio.run(fn(args)))


if __name__ == "__main__":
    main()
