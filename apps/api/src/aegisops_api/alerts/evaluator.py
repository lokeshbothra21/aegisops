"""Rule evaluator (E2.3, ADR-008): code decides that there is an incident, never the LLM.

Every tick:
  for each enabled rule → reader(metric) over [now - window_s, now) → {service: value}
  for each service the rule applies to:
      breach = value comparator threshold
      streak[(rule, service)] = consecutive breaching ticks
      streak reached for_windows and no active incident → open one
          (summary names rule, value, threshold)
      healthy for `recovery_windows` ticks and the incident is still `open`
          (never investigated) → auto-resolve
Streaks live in memory: a restart resets them, which only delays a fire by a few ticks.
"""

import operator
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
import yaml
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aegisops_api.alerts.readers import READERS, Readings
from aegisops_api.incidents.lifecycle import active_incident, open_incident, transition
from aegisops_api.models import AlertRule, Comparator, Incident, IncidentStatus

log = structlog.get_logger()

COMPARE: dict[Comparator, Callable[[float, float], bool]] = {
    Comparator.gt: operator.gt,
    Comparator.gte: operator.ge,
    Comparator.lt: operator.lt,
    Comparator.lte: operator.le,
}


class RuleSpec(BaseModel):
    name: str
    metric: str
    comparator: Comparator
    threshold: float
    window_s: int = 300
    for_windows: int = 1
    service: str | None = None
    enabled: bool = True


class RulesFile(BaseModel):
    rules: list[RuleSpec]


def load_rules_file(path: str) -> list[RuleSpec]:
    with Path(path).open() as f:
        return RulesFile.model_validate(yaml.safe_load(f)).rules


async def ensure_default_rules(session: AsyncSession, specs: list[RuleSpec]) -> int:
    """Insert rules missing by name; never overwrite existing rows (operators tune in the DB)."""
    existing = set((await session.scalars(select(AlertRule.name))).all())
    added = 0
    for spec in specs:
        if spec.metric not in READERS:
            raise ValueError(f"rule {spec.name}: unknown metric {spec.metric!r}")
        if spec.name in existing:
            continue
        session.add(AlertRule(**spec.model_dump()))
        added += 1
    return added


@dataclass
class Evaluation:
    """One (rule, service) result of a tick, for logs and tests."""

    rule: str
    service: str
    value: float
    breach: bool
    streak: int
    opened_incident_id: int | None = None
    resolved_incident_id: int | None = None


@dataclass
class Evaluator:
    scenario_id: str | None = None
    recovery_windows: int = 3
    breach_streak: dict[tuple[int, str], int] = field(default_factory=dict)
    healthy_streak: dict[tuple[int, str], int] = field(default_factory=dict)

    async def tick(self, session: AsyncSession, *, now: datetime | None = None) -> list[Evaluation]:
        now = now or datetime.now(UTC)
        rules = (await session.scalars(select(AlertRule).where(AlertRule.enabled.is_(True)))).all()
        results: list[Evaluation] = []
        for rule in rules:
            reader = READERS.get(rule.metric)
            if reader is None:  # a rule row with a typo must not stop the tick
                log.warning("alerts.unknown_metric", rule=rule.name, metric=rule.metric)
                continue
            readings = await reader(
                session, now - timedelta(seconds=rule.window_s), now, self.scenario_id
            )
            results.extend(await self._apply(session, rule, readings, now))
        opened = [r for r in results if r.opened_incident_id]
        if opened:
            log.info("alerts.opened", incidents=[(r.rule, r.service, r.value) for r in opened])
        return results

    async def _apply(
        self, session: AsyncSession, rule: AlertRule, readings: Readings, now: datetime
    ) -> list[Evaluation]:
        compare = COMPARE[rule.comparator]
        out: list[Evaluation] = []
        for service, value in sorted(readings.items()):
            if rule.service is not None and rule.service != service:
                continue
            key = (rule.id, service)
            breach = compare(value, rule.threshold)
            ev = Evaluation(rule.name, service, value, breach, 0)
            if breach:
                self.healthy_streak[key] = 0
                self.breach_streak[key] = self.breach_streak.get(key, 0) + 1
                ev.streak = self.breach_streak[key]
                if ev.streak >= rule.for_windows:
                    ev.opened_incident_id = await self._maybe_open(
                        session, rule, service, value, now
                    )
            else:
                self.breach_streak[key] = 0
                self.healthy_streak[key] = self.healthy_streak.get(key, 0) + 1
                ev.streak = -self.healthy_streak[key]
                if self.healthy_streak[key] >= self.recovery_windows:
                    ev.resolved_incident_id = await self._maybe_resolve(
                        session, rule, service, value, now
                    )
            out.append(ev)
        return out

    async def _maybe_open(
        self, session: AsyncSession, rule: AlertRule, service: str, value: float, now: datetime
    ) -> int | None:
        if (
            await active_incident(session, service=service, scenario_id=self.scenario_id)
            is not None
        ):
            return None
        summary = (
            f"{rule.name}: {rule.metric} for {service} = {value:.4g} {rule.comparator.value} "
            f"{rule.threshold:g} over {rule.window_s}s for {rule.for_windows} evaluations"
        )
        incident = await open_incident(
            session,
            service=service,
            alert_rule_id=rule.id,
            summary=summary,
            scenario_id=self.scenario_id,
            now=now,
        )
        log.info("incident.opened", id=incident.id, service=service, rule=rule.name, value=value)
        return incident.id

    async def _maybe_resolve(
        self, session: AsyncSession, rule: AlertRule, service: str, value: float, now: datetime
    ) -> int | None:
        incident = await active_incident(session, service=service, scenario_id=self.scenario_id)
        if (
            incident is None
            or incident.status is not IncidentStatus.open
            or incident.alert_rule_id != rule.id
        ):
            return None  # investigated incidents belong to the agent / a human
        transition(incident, IncidentStatus.resolved, now=now)
        incident.summary = (
            f"{incident.summary} | auto-resolved: {rule.metric} = {value:.4g} "
            f"healthy for {self.recovery_windows} evaluations"
        )
        log.info("incident.auto_resolved", id=incident.id, service=service, rule=rule.name)
        return incident.id


async def open_incidents(session: AsyncSession) -> list[Incident]:
    return list(
        (
            await session.scalars(select(Incident).where(Incident.status == IncidentStatus.open))
        ).all()
    )
