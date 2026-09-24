"""Run one scenario against a live demo + API and capture the window (E7.2 + E1.6).

    t0            warm-up (baseline traffic)
    t_fault       apply the fault (flag flip / overlay), optionally inject a log line (S13)
    t_incident    first incident for the expected service opens (or timeout)
    hold          keep the fault on
    t_revert      revert the fault
    recover       wait for the incident to auto-resolve (or give up)
    capture       POST /admin/capture tags [t0, t_end) with the scenario key

Everything the benchmark later scores is recorded in the returned RunReport.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import structlog
from pydantic import BaseModel

from aegisops_bench.catalogue import FlagFault, InjectLog, OverlayFault, ScenarioSpec
from aegisops_bench.flags import set_flag

log = structlog.get_logger()


class RunReport(BaseModel):
    key: str
    started_at: datetime
    fault_at: datetime | None = None
    incident_at: datetime | None = None
    incident_id: int | None = None
    ttd_s: float | None = None  # fault -> incident open
    reverted_at: datetime | None = None
    resolved_at: datetime | None = None
    ended_at: datetime | None = None
    captured: dict[str, int] = {}
    notes: list[str] = []


@dataclass
class Runner:
    api: str
    admin_token: str
    flagd_path: Path
    http: httpx.AsyncClient = field(default_factory=lambda: httpx.AsyncClient(timeout=30))
    sleep: Any = asyncio.sleep  # injectable for tests
    now: Any = lambda: datetime.now(UTC)

    def _hdr(self) -> dict[str, str]:
        return {"X-Admin-Token": self.admin_token}

    async def apply_fault(self, spec: ScenarioSpec) -> str | None:
        f = spec.fault
        if isinstance(f, FlagFault):
            return set_flag(self.flagd_path, f.flag, f.variant)
        if isinstance(f, OverlayFault):
            if not f.available:
                raise RuntimeError(f"{spec.key}: overlay {f.name!r} is not built yet (Week 8)")
            raise NotImplementedError("overlay faults arrive with E7.3")
        return None

    async def revert_fault(self, spec: ScenarioSpec) -> None:
        f = spec.fault
        if isinstance(f, FlagFault):
            set_flag(self.flagd_path, f.flag, f.revert)

    async def inject_log(self, inj: InjectLog) -> None:
        """S13: one OTLP/JSON log record through the normal ingest path."""
        ns = str(int(self.now().timestamp() * 1e9))
        payload = {
            "resourceLogs": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": inj.service}}
                        ]
                    },
                    "scopeLogs": [
                        {
                            "scope": {"name": "aegis-bench"},
                            "logRecords": [
                                {
                                    "timeUnixNano": ns,
                                    "severityNumber": inj.severity_num,
                                    "severityText": "ERROR",
                                    "body": {"stringValue": inj.body},
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        r = await self.http.post(f"{self.api}/ingest/v1/logs", json=payload)
        r.raise_for_status()

    async def wait_for_incident(
        self, service: str, timeout_s: int, since: datetime
    ) -> dict[str, Any] | None:
        deadline = self.now() + timedelta(seconds=timeout_s)
        while self.now() < deadline:
            r = await self.http.get(f"{self.api}/api/v1/incidents", params={"limit": 50})
            if r.status_code == 200:
                for inc in r.json()["items"]:
                    opened = datetime.fromisoformat(inc["opened_at"])
                    if inc["service"] == service and inc["scenario_id"] is None and opened >= since:
                        return dict(inc)
            await self.sleep(5)
        return None

    async def wait_for_resolution(self, incident_id: int, timeout_s: int) -> datetime | None:
        deadline = self.now() + timedelta(seconds=timeout_s)
        while self.now() < deadline:
            r = await self.http.get(f"{self.api}/api/v1/incidents/{incident_id}")
            if r.status_code == 200 and r.json()["status"] in ("resolved", "failed"):
                closed = r.json()["closed_at"]
                return datetime.fromisoformat(closed) if closed else self.now()
            await self.sleep(10)
        return None

    async def capture(
        self, spec: ScenarioSpec, start: datetime, end: datetime, notes: str
    ) -> dict[str, int]:
        body = {
            "key": spec.key,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "title": spec.title,
            "fault_type": spec.fault_type,
            "expected_service": spec.expected_service,
            "expected_category": spec.expected_category,
            "expected_actions": spec.expected_actions,
            "notes": notes,
        }
        r = await self.http.post(f"{self.api}/api/v1/admin/capture", json=body, headers=self._hdr())
        r.raise_for_status()
        tagged: dict[str, int] = r.json()["tagged"]
        return tagged

    async def run(self, spec: ScenarioSpec) -> RunReport:
        t = spec.timing
        rep = RunReport(key=spec.key, started_at=self.now())
        log.info("scenario.start", key=spec.key, warmup_s=t.warmup_s)
        await self.sleep(t.warmup_s)
        rep.fault_at = self.now()
        previous = await self.apply_fault(spec)
        log.info("scenario.fault", key=spec.key, fault=spec.fault.model_dump(), previous=previous)
        if spec.inject_log:
            await self.inject_log(spec.inject_log)
            rep.notes.append("injected log line")
        try:
            if spec.expected_service:
                inc = await self.wait_for_incident(
                    spec.expected_service, t.incident_timeout_s, rep.fault_at
                )
                if inc:
                    rep.incident_id, rep.incident_at = (
                        inc["id"],
                        datetime.fromisoformat(inc["opened_at"]),
                    )
                    rep.ttd_s = round((rep.incident_at - rep.fault_at).total_seconds(), 1)
                    log.info(
                        "scenario.incident", key=spec.key, incident_id=inc["id"], ttd_s=rep.ttd_s
                    )
                else:
                    rep.notes.append(f"no incident within {t.incident_timeout_s}s")
            else:
                # noise scenario: any incident during the hold is a false positive
                inc = await self.wait_for_incident("*", 1, rep.fault_at)
            await self.sleep(t.hold_s)
        finally:
            await self.revert_fault(spec)
            rep.reverted_at = self.now()
            log.info("scenario.revert", key=spec.key)
        if rep.incident_id:
            rep.resolved_at = await self.wait_for_resolution(rep.incident_id, t.recover_s)
            if rep.resolved_at is None:
                rep.notes.append(f"not resolved within {t.recover_s}s")
        else:
            await self.sleep(min(t.recover_s, 60))
        rep.ended_at = self.now()
        rep.captured = await self.capture(
            spec,
            rep.started_at,
            rep.ended_at + timedelta(seconds=1),
            json.dumps(rep.model_dump(mode="json")),
        )
        log.info("scenario.captured", key=spec.key, tagged=rep.captured)
        return rep
