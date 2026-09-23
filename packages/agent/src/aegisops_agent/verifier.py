"""Evidence verification (E4.1, ADR-008): code checks every reference the model cited.

For each EvidenceRef in the root cause:
  span    ref_id is a trace id (32 hex) or span id (16 hex) that must exist in `spans`
  log     ref_id is a log signature or a body fragment that must match a log line;
          a numeric claim is compared with the number of matching lines
  metric  ref_id names a quantity the tools expose (error_rate, calls, errors, p95_ms,
          p50_ms, p99_ms, error_count, memory_bytes, cpu_utilization, container_memory_pct)
          or a raw metric name; a numeric claim is recomputed and must be within ±20 %
  change  ref_id must match a change event in the window (ISO timestamp within 60 s, or a
          flag / service / type token that appears in the event)

Unverifiable refs are dropped with a reason. confidence := claimed x verified / cited
(0 when nothing was cited). Deterministic, no model involved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aegisops_agent.schemas import (
    DroppedRef,
    EvidenceRef,
    RootCause,
    Verification,
    VerifiedRootCause,
)
from aegisops_tools import telemetry
from aegisops_tools.context import ToolContext
from aegisops_tools.telemetry import log_signature

TOLERANCE = 0.20
WINDOWS_MINUTES = (5, 15, 30, 60)  # a numeric claim may come from any standard tool window
ABS_TOLERANCE = 0.01  # for values near zero (rates)
HEX32 = re.compile(r"^[0-9a-f]{32}$")
HEX16 = re.compile(r"^[0-9a-f]{16}$")


def within_tolerance(claimed: float, actual: float) -> bool:
    if abs(claimed - actual) <= ABS_TOLERANCE:
        return True
    if actual == 0:
        return False
    return abs(claimed - actual) / abs(actual) <= TOLERANCE


@dataclass
class Verifier:
    factory: async_sessionmaker[AsyncSession]
    ctx: ToolContext
    window_minutes: int = 15

    async def verify(self, rc: RootCause) -> VerifiedRootCause:
        kept: list[EvidenceRef] = []
        dropped: list[DroppedRef] = []
        async with self.factory() as session:
            for ref in rc.evidence_refs:
                reason = await self._check(session, rc.service, ref)
                if reason is None:
                    kept.append(ref)
                else:
                    dropped.append(DroppedRef(ref=ref, reason=reason))
        cited, verified = len(rc.evidence_refs), len(kept)
        confidence = round(rc.confidence * (verified / cited), 4) if cited else 0.0
        return VerifiedRootCause(
            **rc.model_dump(exclude={"evidence_refs", "confidence"}),
            evidence_refs=kept,
            confidence=confidence,
            claimed_confidence=rc.confidence,
            verification=Verification(cited=cited, verified=verified, dropped=dropped),
        )

    async def _check(self, session: AsyncSession, service: str, ref: EvidenceRef) -> str | None:
        start, end = self.ctx.window(self.window_minutes)
        if ref.kind == "span":
            return await self._check_span(session, ref)
        if ref.kind == "log":
            return await self._check_log(session, service, ref, start, end)
        if ref.kind == "metric":
            return await self._check_metric(session, service, ref)
        return await self._check_change(session, ref, start, end)

    async def _check_span(self, session: AsyncSession, ref: EvidenceRef) -> str | None:
        rid = ref.ref_id.strip().lower()
        if HEX32.match(rid):
            col = "trace_id"
        elif HEX16.match(rid):
            col = "span_id"
        else:
            return "span ref is not a trace id (32 hex) or span id (16 hex)"
        n = await session.scalar(
            text(
                """
                SELECT count(*) FROM spans
                WHERE ((:col = 'trace_id' AND trace_id = :rid)
                    OR (:col = 'span_id' AND span_id = :rid))
                  AND scenario_id IS NOT DISTINCT FROM :sc
                """
            ),
            {"col": col, "rid": rid, "sc": self.ctx.scenario_id},
        )
        return None if n else f"no span with {col}={rid} in the store"

    async def _check_log(
        self, session: AsyncSession, service: str, ref: EvidenceRef, start: datetime, end: datetime
    ) -> str | None:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT body FROM logs
                    WHERE service = :service AND ts >= :start AND ts < :end
                      AND scenario_id IS NOT DISTINCT FROM :sc
                    ORDER BY ts DESC LIMIT 2000
                    """
                ),
                {"service": service, "start": start, "end": end, "sc": self.ctx.scenario_id},
            )
        ).scalars()
        needle = ref.ref_id.strip().lower()
        matches = sum(
            1 for body in rows if needle in body.lower() or needle == log_signature(body).lower()
        )
        if matches == 0:
            return f"no log line for service {service!r} matching {ref.ref_id!r} in the window"
        if ref.claimed_value is not None and not within_tolerance(ref.claimed_value, matches):
            return f"claimed {ref.claimed_value:g} matching log lines, found {matches}"
        return None

    async def _check_metric(
        self, session: AsyncSession, service: str, ref: EvidenceRef
    ) -> str | None:
        """The ref must have data; a numeric claim must match the recomputation for at least one
        standard window (the model's number came from a tool call with some window)."""
        name = ref.ref_id.strip()
        actuals: dict[int, float] = {}
        for w in WINDOWS_MINUTES:
            v = await self._recompute(session, service, name, w)
            if v is not None:
                actuals[w] = v
        if not actuals:
            return f"metric {name!r} has no data for service {service!r} in the last hour"
        if ref.claimed_value is None or any(
            within_tolerance(ref.claimed_value, a) for a in actuals.values()
        ):
            return None
        seen = ", ".join(f"{a:g}@{w}m" for w, a in actuals.items())
        return f"claimed {name} = {ref.claimed_value:g}, recomputed {seen} (tolerance +/-20%)"

    async def _recompute(
        self, session: AsyncSession, service: str, name: str, w: int
    ) -> float | None:
        """The value of a quantity the tools expose over the last `w` minutes; None when no data."""
        if name in ("error_rate", "calls", "errors"):
            r = await telemetry.get_error_rate(session, self.ctx, service, window_minutes=w)
            v = r.get(name)
            return float(v) if v is not None else None
        if name in ("p50_ms", "p95_ms", "p99_ms"):
            r = await telemetry.get_latency_percentiles(
                session, self.ctx, service, window_minutes=w
            )
            v = r["now"].get(name)
            return float(v) if v is not None else None
        if name == "error_count":
            r = await telemetry.get_error_rate(session, self.ctx, service, window_minutes=w)
            return (
                float(sum(p["errors"] for p in r["errors_per_minute"]))
                if r["errors_per_minute"]
                else None
            )
        if name in ("memory_bytes", "cpu_utilization", "container_memory_pct"):
            r = await telemetry.get_container_metrics(session, self.ctx, service, window_minutes=w)
            key = "memory_percent" if name == "container_memory_pct" else name
            pts = r.get(key) or []
            return float(pts[-1]["value"]) if pts else None
        # raw metric name in metric_points (latest value for the service in the window)
        start, end = self.ctx.window(w)
        v = await session.scalar(
            text(
                """
                SELECT value FROM metric_points
                WHERE metric_name = :name
                  AND (service = :service OR attrs->'otel.resource'->>'container.name' = :service)
                  AND ts >= :start AND ts < :end AND scenario_id IS NOT DISTINCT FROM :sc
                ORDER BY ts DESC LIMIT 1
                """
            ),
            {
                "name": name,
                "service": service,
                "start": start,
                "end": end,
                "sc": self.ctx.scenario_id,
            },
        )
        return float(v) if v is not None else None

    async def _check_change(
        self, session: AsyncSession, ref: EvidenceRef, start: datetime, end: datetime
    ) -> str | None:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT ts, type, service, before::text, after::text FROM change_events
                    WHERE ts >= :start AND ts < :end AND scenario_id IS NOT DISTINCT FROM :sc
                    """
                ),
                {"start": start - timedelta(minutes=60), "end": end, "sc": self.ctx.scenario_id},
            )
        ).all()
        if not rows:
            return "no change events in the window"
        rid = ref.ref_id.strip()
        try:
            when = datetime.fromisoformat(rid.replace("Z", "+00:00"))
        except ValueError:
            when = None
        if when is not None:
            if any(abs((ts - when).total_seconds()) <= 60 for ts, *_ in rows):
                return None
            return f"no change event within 60 s of {rid}"
        tokens = [
            t.lower()
            for t in re.split(r"[\s:,;()\[\]\"']+", rid)
            if len(t) >= 4 and t not in ("flag", "changed", "change", "event", "->")
        ]
        if not tokens:
            return "change ref has no identifying token (flag name, service, type or timestamp)"
        for _ts, typ, svc, before, after in rows:
            hay = " ".join(str(x) for x in (typ, svc, before, after)).lower()
            if any(tok in hay for tok in tokens):
                return None
        return f"no change event matching {rid!r} in the window"
