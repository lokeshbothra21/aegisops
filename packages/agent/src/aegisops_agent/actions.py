"""Actions (E5.3) behind approval: the only code in AegisOps that changes the target system.

NOT exposed over MCP (ADR-007). Reachable only from the `execute` node, which the graph
wires only after `approval` (E9.2, enforced by a structure test).

Every request is validated against config/policy.yaml before any backend is touched
(E9.3): allowlisted flags/services, strict value patterns, replicas <= max. Backends:
  LiveBackend    atomic flag file replace + Docker Engine API over the Unix socket, fixed
                 endpoints, no shell, no free-form arguments
  ReplayBackend  returns a simulated outcome (public demo, CI, benchmark; ADR-004)
Every attempt, accepted or rejected, is written to `audit_log` (E9.6).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx
import structlog
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aegisops_agent.remediation import Action, Policy

log = structlog.get_logger()
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")  # flags and service names
VARIANT = re.compile(r"^[A-Za-z0-9%._-]{1,16}$")
VERSION = re.compile(r"^(previous|[A-Za-z0-9._-]{1,64})$")
LABEL_PROJECT = "com.docker.compose.project"
LABEL_SERVICE = "com.docker.compose.service"


class ActionRejectedError(Exception):
    """Validation failed; nothing was executed."""


class Outcome(BaseModel):
    ok: bool
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    details: str = ""
    simulated: bool = False
    alert_cleared: bool | None = None  # replay only: the recorded result of the post-check
    duration_ms: int = 0


def validate(policy: Policy, action: Action, params: dict[str, Any]) -> dict[str, Any]:
    """Return clean params or raise ActionRejectedError. Unknown keys are dropped."""
    if action is Action.none:
        raise ActionRejectedError("nothing to execute")
    rule = policy.actions.get(action.value)
    if rule is None:
        raise ActionRejectedError(f"action {action.value!r} has no policy entry")
    if action is Action.toggle_flag:
        flag, variant = str(params.get("flag", "")), str(params.get("variant", ""))
        if not NAME.match(flag) or flag not in rule.allowed_flags:
            raise ActionRejectedError(f"flag {flag!r} is not allowlisted")
        if not VARIANT.match(variant):
            raise ActionRejectedError(f"variant {variant!r} is not a valid variant")
        return {"flag": flag, "variant": variant}
    service = str(params.get("service", ""))
    if not NAME.match(service) or service not in rule.allowed_services:
        raise ActionRejectedError(f"service {service!r} is not allowlisted for {action.value}")
    if action is Action.restart_service:
        return {"service": service}
    if action is Action.scale_service:
        try:
            replicas = int(params.get("replicas", 0))
        except (TypeError, ValueError) as exc:
            raise ActionRejectedError("replicas must be an integer") from exc
        if not 1 <= replicas <= rule.max_replicas:
            raise ActionRejectedError(f"replicas {replicas} outside 1..{rule.max_replicas}")
        return {"service": service, "replicas": replicas}
    version = str(params.get("to_version", "previous"))
    if not VERSION.match(version):
        raise ActionRejectedError(f"to_version {version!r} is not a valid version")
    return {"service": service, "to_version": version}


class Backend(Protocol):
    async def run(self, action: Action, params: dict[str, Any]) -> Outcome: ...


@dataclass
class ReplayBackend:
    """Simulated execution: nothing is changed; the recorded post-check result is returned."""

    alert_cleared: bool = True

    async def run(self, action: Action, params: dict[str, Any]) -> Outcome:
        return Outcome(
            ok=True,
            action=action.value,
            params=params,
            simulated=True,
            details="replay: no change made to any system",
            alert_cleared=self.alert_cleared,
        )


@dataclass
class LiveBackend:
    """Real changes to the local demo. Flag edits are atomic file replaces (flagd reloads them
    within ~2 s, verified live); container operations are fixed Docker Engine endpoints."""

    flagd_path: Path
    docker_socket: str | None = None
    compose_project: str = "opentelemetry-demo"
    http: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        if self.http is None:
            if not self.docker_socket:
                raise ActionRejectedError("no Docker socket configured for container actions")
            self.http = httpx.AsyncClient(
                transport=httpx.AsyncHTTPTransport(uds=self.docker_socket),
                base_url="http://docker",
                timeout=60,
            )
        return self.http

    async def run(self, action: Action, params: dict[str, Any]) -> Outcome:
        if action is Action.toggle_flag:
            return self._toggle(params["flag"], params["variant"])
        if action is Action.restart_service:
            return await self._restart(params["service"])
        # the demo pins container_name (no scale) and ships one image tag (no rollback target)
        return Outcome(
            ok=False,
            action=action.value,
            params=params,
            details=f"{action.value} is not supported by the local demo target",
        )

    def _toggle(self, flag: str, variant: str) -> Outcome:
        doc = json.loads(self.flagd_path.read_text())
        spec = doc["flags"].get(flag)
        if spec is None:
            return Outcome(
                ok=False,
                action="toggle_flag",
                params={"flag": flag},
                details=f"flag {flag!r} not in the flag file",
            )
        if variant not in spec["variants"]:
            return Outcome(
                ok=False,
                action="toggle_flag",
                params={"flag": flag, "variant": variant},
                details=f"variant {variant!r} not one of {sorted(spec['variants'])}",
            )
        before = spec["defaultVariant"]
        spec["defaultVariant"] = variant
        tmp = self.flagd_path.with_suffix(".aegis.tmp")
        tmp.write_text(json.dumps(doc, indent=2) + "\n")
        tmp.replace(self.flagd_path)  # atomic: flagd never reads a half-written file
        return Outcome(
            ok=True,
            action="toggle_flag",
            params={"flag": flag, "variant": variant},
            details=f"{flag}: {before} -> {variant}",
        )

    async def _restart(self, service: str) -> Outcome:
        c = self._client()
        filters = json.dumps(
            {"label": [f"{LABEL_PROJECT}={self.compose_project}", f"{LABEL_SERVICE}={service}"]}
        )
        r = await c.get("/containers/json", params={"filters": filters})
        r.raise_for_status()
        ids = [x["Id"] for x in r.json()]
        if not ids:
            return Outcome(
                ok=False,
                action="restart_service",
                params={"service": service},
                details="no container found",
            )
        for cid in ids:
            rr = await c.post(f"/containers/{cid}/restart", params={"t": 10})
            if rr.status_code not in (204, 304):
                return Outcome(
                    ok=False,
                    action="restart_service",
                    params={"service": service},
                    details=f"docker restart returned {rr.status_code}",
                )
        return Outcome(
            ok=True,
            action="restart_service",
            params={"service": service},
            details=f"restarted {len(ids)} container(s)",
        )


@dataclass
class AuditWriter:
    """Append-only audit trail (E9.6): tool calls, approvals, action attempts."""

    factory: async_sessionmaker[AsyncSession]

    async def write(
        self,
        *,
        run_id: int | None,
        node: str,
        tool: str,
        args: dict[str, Any],
        ok: bool,
        actor: str,
        duration_ms: int = 0,
    ) -> None:
        async with self.factory() as s:
            await s.execute(
                text("""
                    INSERT INTO audit_log (ts, run_id, node, tool, args, duration_ms, ok, actor)
                    VALUES (:ts, :run_id, :node, :tool, CAST(:args AS jsonb), :d, :ok, :actor)
                """),
                {
                    "ts": datetime.now(UTC),
                    "run_id": run_id,
                    "node": node,
                    "tool": tool,
                    "args": json.dumps(args, default=str),
                    "d": duration_ms,
                    "ok": ok,
                    "actor": actor,
                },
            )
            await s.commit()


@dataclass
class ActionExecutor:
    """Validate, pick the backend (replay for captured scenarios, live otherwise), run, audit."""

    policy: Policy
    replay: Backend = field(default_factory=ReplayBackend)
    live: Backend | None = None
    audit: AuditWriter | None = None

    async def execute(
        self,
        action: Action,
        params: dict[str, Any],
        *,
        replay: bool,
        run_id: int | None,
        actor: str,
    ) -> Outcome:
        t0 = time.monotonic()
        try:
            clean = validate(self.policy, action, params)
        except ActionRejectedError as exc:
            out = Outcome(ok=False, action=action.value, params=params, details=f"rejected: {exc}")
            await self._audit(run_id, out, actor, t0)
            return out
        backend = self.replay if replay else self.live
        if backend is None:
            out = Outcome(
                ok=False, action=action.value, params=clean, details="no live backend configured"
            )
        else:
            try:
                out = await backend.run(action, clean)
            except (httpx.HTTPError, OSError, ActionRejectedError) as exc:
                out = Outcome(
                    ok=False,
                    action=action.value,
                    params=clean,
                    details=f"{type(exc).__name__}: {exc}",
                )
        out.duration_ms = int((time.monotonic() - t0) * 1000)
        await self._audit(run_id, out, actor, t0)
        log.info(
            "action.executed",
            action=action.value,
            ok=out.ok,
            simulated=out.simulated,
            details=out.details,
        )
        return out

    async def _audit(self, run_id: int | None, out: Outcome, actor: str, t0: float) -> None:
        if self.audit is not None:
            await self.audit.write(
                run_id=run_id,
                node="execute",
                tool=out.action,
                args={**out.params, "details": out.details, "simulated": out.simulated},
                ok=out.ok,
                actor=actor,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
