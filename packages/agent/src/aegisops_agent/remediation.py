"""Remediation proposal (E5.1) and the approval policy (E5.4, first cut). Deterministic.

Category -> action mapping from PROJECT.md §10.1, with one override that beats the table:
if change correlation puts a *flag* change on the affected service (or a direct neighbour)
first, the proposal is to toggle that flag back. Reverting the change that preceded the
alert is the most conservative operational fix (ADR-005).

The policy decides whether the proposal may auto-execute: per-incident autonomy level x
risk x verified confidence; public mode never executes.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from aegisops_agent.schemas import ChangeCorrelation, RootCauseCategory, VerifiedRootCause


class Action(StrEnum):
    toggle_flag = "toggle_flag"
    restart_service = "restart_service"
    scale_service = "scale_service"
    rollback_deployment = "rollback_deployment"
    none = "none"


class Risk(StrEnum):
    low = "low"
    medium = "medium"


class Remediation(BaseModel):
    action: Action
    params: dict[str, Any] = Field(default_factory=dict)
    risk: Risk
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=600)
    alternatives: list[str] = Field(default_factory=list)


# §10.1 defaults (first action is the proposal, the rest are alternatives shown to the human)
CATEGORY_ACTIONS: dict[RootCauseCategory, tuple[list[Action], Risk]] = {
    RootCauseCategory.bad_deploy: ([Action.rollback_deployment], Risk.medium),
    RootCauseCategory.config_regression: (
        [Action.rollback_deployment, Action.toggle_flag],
        Risk.medium,
    ),
    RootCauseCategory.dependency_down: ([Action.restart_service, Action.toggle_flag], Risk.low),
    RootCauseCategory.dependency_errors: ([Action.restart_service, Action.toggle_flag], Risk.low),
    RootCauseCategory.datastore_failure: ([Action.restart_service], Risk.low),
    RootCauseCategory.pool_exhaustion: ([Action.restart_service], Risk.low),
    RootCauseCategory.memory_leak: ([Action.restart_service], Risk.low),
    RootCauseCategory.cpu_saturation: ([Action.scale_service], Risk.medium),
    RootCauseCategory.queue_lag: ([Action.scale_service], Risk.medium),
    RootCauseCategory.latency_regression: (
        [Action.rollback_deployment, Action.toggle_flag],
        Risk.medium,
    ),
    RootCauseCategory.app_bug: ([Action.toggle_flag, Action.none], Risk.low),
    RootCauseCategory.no_incident: ([Action.none], Risk.low),
}
FLAG_OVERRIDE_MIN_SCORE = 0.5


def _flag_change(corr: ChangeCorrelation | None, service: str) -> dict[str, Any] | None:
    """The top-scoring flag change on the service or a neighbour, if it scores well enough."""
    if corr is None:
        return None
    for ev in corr.events:
        if ev.type != "flag" or ev.score < FLAG_OVERRIDE_MIN_SCORE or ev.minutes_before_alert < 0:
            continue
        # summary is "flag: from -> to" (correlate._summary)
        name, _, rest = ev.summary.partition(": ")
        before, _, after = rest.partition(" -> ")
        if name and before and after:
            return {
                "flag": name,
                "from": before,
                "to": after,
                "service": ev.service,
                "score": ev.score,
            }
    return None


def propose(rc: VerifiedRootCause, corr: ChangeCorrelation | None) -> Remediation:
    """Deterministic proposal for a verified root cause (no model call)."""
    if rc.category is RootCauseCategory.no_incident or rc.confidence == 0.0:
        return Remediation(
            action=Action.none,
            risk=Risk.low,
            confidence=rc.confidence,
            rationale="no actionable root cause (no_incident or zero verified confidence)",
        )
    flag = _flag_change(corr, rc.service)
    if flag:
        return Remediation(
            action=Action.toggle_flag,
            params={"flag": flag["flag"], "variant": flag["from"], "service": flag["service"]},
            risk=Risk.low,
            confidence=rc.confidence,
            rationale=(
                f"Revert the flag change that preceded the alert: {flag['flag']} {flag['from']} -> "
                f"{flag['to']} on {flag['service']} (correlation {flag['score']:.2f}). "
                f"{rc.statement[:200]}"
            ),
            alternatives=[
                a.value for a in CATEGORY_ACTIONS[rc.category][0] if a is not Action.toggle_flag
            ],
        )
    actions, risk = CATEGORY_ACTIONS[rc.category]
    first = actions[0]
    params: dict[str, Any] = {"service": rc.service}
    if first is Action.scale_service:
        params["replicas"] = 2
    if first is Action.rollback_deployment:
        params["to_version"] = "previous"
    if first is Action.none:
        params = {}
    return Remediation(
        action=first,
        params=params,
        risk=risk,
        confidence=rc.confidence,
        rationale=f"{rc.category.value}: {rc.statement[:300]}",
        alternatives=[a.value for a in actions[1:]],
    )


# --- policy (E5.4 first cut) ----------------------------------------------------------


class AutonomyRule(BaseModel):
    auto: list[Risk] = Field(default_factory=list)
    min_confidence: float = 1.0


class ActionRule(BaseModel):
    risk: Risk
    allowed_flags: list[str] = Field(default_factory=list)
    allowed_services: list[str] = Field(default_factory=list)
    max_replicas: int = 3


class PublicMode(BaseModel):
    max_autonomy: int = 1
    execute_enabled: bool = False


class Policy(BaseModel):
    autonomy: dict[int, AutonomyRule]
    actions: dict[str, ActionRule]
    public_mode: PublicMode = Field(default_factory=PublicMode)


def load_policy(path: str | Path) -> Policy:
    with Path(path).open() as f:
        return Policy.model_validate(yaml.safe_load(f))


class PolicyDecision(BaseModel):
    auto: bool
    reason: str


def evaluate(
    policy: Policy, rem: Remediation, *, autonomy_level: int, public_mode: bool
) -> PolicyDecision:
    """May this proposal execute without a human? Never in public mode; never for `none`."""
    if rem.action is Action.none:
        return PolicyDecision(auto=False, reason="nothing to execute")
    if public_mode:
        return PolicyDecision(auto=False, reason="public mode: execution disabled, approval only")
    level = min(autonomy_level, max(policy.autonomy))
    rule = policy.autonomy.get(level, AutonomyRule())
    if rem.risk not in rule.auto:
        return PolicyDecision(
            auto=False, reason=f"autonomy level {level} waits for a human on {rem.risk.value} risk"
        )
    if rem.confidence < rule.min_confidence:
        return PolicyDecision(
            auto=False,
            reason=f"confidence {rem.confidence:.2f} < {rule.min_confidence:.2f} at level {level}",
        )
    arule = policy.actions.get(rem.action.value)
    if arule is None:
        return PolicyDecision(auto=False, reason=f"action {rem.action.value} has no policy entry")
    if rem.action is Action.toggle_flag and rem.params.get("flag") not in arule.allowed_flags:
        return PolicyDecision(
            auto=False, reason=f"flag {rem.params.get('flag')!r} not in the allowlist"
        )
    if (
        rem.action is not Action.toggle_flag
        and rem.params.get("service") not in arule.allowed_services
    ):
        return PolicyDecision(
            auto=False, reason=f"service {rem.params.get('service')!r} not in the allowlist"
        )
    return PolicyDecision(
        auto=True,
        reason=f"level {level}: {rem.risk.value} risk at {rem.confidence:.2f} may auto-execute",
    )
