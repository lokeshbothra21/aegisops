"""Structured outputs for every node (E3.2). The model never returns free text at the top
level: it returns one of these, validated by Pydantic. Free text lives only inside fields.
"""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class Symptom(StrEnum):
    error_rate = "error_rate"
    latency = "latency"
    saturation = "saturation"
    availability = "availability"
    queue_lag = "queue_lag"
    unknown = "unknown"


class RootCauseCategory(StrEnum):
    """PROJECT.md §8.2."""

    bad_deploy = "bad_deploy"
    config_regression = "config_regression"
    dependency_down = "dependency_down"
    dependency_errors = "dependency_errors"
    datastore_failure = "datastore_failure"
    pool_exhaustion = "pool_exhaustion"
    memory_leak = "memory_leak"
    cpu_saturation = "cpu_saturation"
    queue_lag = "queue_lag"
    latency_regression = "latency_regression"
    app_bug = "app_bug"
    no_incident = "no_incident"


class EvidenceRef(BaseModel):
    """A pointer into the telemetry store that the verifier (E4.1) can check."""

    kind: Literal["span", "log", "metric", "change"]
    ref_id: str = Field(description="trace_id, log signature, metric name, or change event ts")
    claim: str = Field(max_length=300)
    claimed_value: float | None = Field(
        default=None, description="a number the claim asserts, if any"
    )


class Triage(BaseModel):
    service: str = Field(max_length=128)
    symptom: Symptom
    window_minutes: int = Field(default=15, ge=1, le=240)
    summary: str = Field(max_length=400)


class ToolRequest(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class Hypothesis(BaseModel):
    id: str = Field(pattern=r"^H[1-3]$")
    statement: str = Field(max_length=300)
    tools_to_run: list[ToolRequest] = Field(default_factory=list, max_length=6)


class Hypotheses(BaseModel):
    items: list[Hypothesis] = Field(min_length=1, max_length=3)

    @field_validator("items")
    @classmethod
    def unique_ids(cls, items: list[Hypothesis]) -> list[Hypothesis]:
        if len({h.id for h in items}) != len(items):
            raise ValueError("hypothesis ids must be unique")
        return items


class Finding(BaseModel):
    hypothesis_id: str
    supports: bool
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=8)
    note: str = Field(default="", max_length=400)


class Findings(BaseModel):
    items: list[Finding] = Field(default_factory=list, max_length=3)


class RootCause(BaseModel):
    service: str = Field(max_length=128)
    category: RootCauseCategory
    statement: str = Field(max_length=600)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=10)
    partial: bool = Field(default=False, description="true when produced after a budget breach")


class ToolCallRecord(BaseModel):
    """What the investigate node actually ran, for the audit trail and the verifier."""

    hypothesis_id: str
    tool: str
    args: dict[str, Any]
    ok: bool
    bytes: int
    error: str | None = None


class Budget(BaseModel):
    max_tool_calls: int = 15
    max_tokens: int = 60_000
    max_seconds: float = 180.0


class Usage(BaseModel):
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    llm_calls: int = 0
    model_fallbacks: int = 0

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out
