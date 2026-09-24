"""bench/scenarios.yaml as typed data (E7.1, PROJECT.md §12.1)."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

DEFAULT_PATH = Path(__file__).resolve().parents[4] / "bench" / "scenarios.yaml"


class ScenarioSet(StrEnum):
    dev = "dev"
    held_out = "held-out"
    noise = "noise"
    security = "security"


class FlagFault(BaseModel):
    type: Literal["flag"] = "flag"
    flag: str
    variant: str
    revert: str = "off"


class OverlayFault(BaseModel):
    """Custom fault (S9-S12): a compose/env overlay under infra/faults, built in Week 8."""

    type: Literal["overlay"] = "overlay"
    name: str
    available: bool = False


class NoFault(BaseModel):
    type: Literal["none"] = "none"


class InjectLog(BaseModel):
    """S13: a crafted log line ingested through the API alongside a real fault."""

    service: str
    body: str
    severity_num: int = 17


class Timing(BaseModel):
    warmup_s: int = Field(default=120, ge=0)
    hold_s: int = Field(default=180, ge=30)
    recover_s: int = Field(default=240, ge=0)
    incident_timeout_s: int = Field(default=180, ge=30)


class ScenarioSpec(BaseModel):
    key: str = Field(pattern=r"^(S1[0-3]|S[1-9]|N[1-3])$")
    title: str
    set: ScenarioSet
    fault: FlagFault | OverlayFault | NoFault = Field(discriminator="type")
    inject_log: InjectLog | None = None
    expected_service: str | None = None
    expected_category: str | None = None
    expected_actions: list[str] = Field(default_factory=list)
    timing: Timing = Field(default_factory=Timing)
    notes: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> ScenarioSpec:
        if self.set is ScenarioSet.noise:
            if self.expected_category not in (None, "no_incident") or self.expected_actions:
                raise ValueError(f"{self.key}: noise scenarios expect no_incident and no actions")
        elif self.expected_service is None or self.expected_category is None:
            raise ValueError(
                f"{self.key}: fault scenarios need expected_service and expected_category"
            )
        return self

    @property
    def fault_type(self) -> str:
        return self.fault.type


class Catalogue(BaseModel):
    scenarios: list[ScenarioSpec]

    @model_validator(mode="after")
    def _unique(self) -> Catalogue:
        keys = [s.key for s in self.scenarios]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate scenario keys")
        return self

    def get(self, key: str) -> ScenarioSpec:
        for s in self.scenarios:
            if s.key == key:
                return s
        raise KeyError(key)

    def by_set(self, which: ScenarioSet) -> list[ScenarioSpec]:
        return [s for s in self.scenarios if s.set is which]


def load_catalogue(path: str | Path = DEFAULT_PATH) -> Catalogue:
    with Path(path).open() as f:
        return Catalogue.model_validate(yaml.safe_load(f))
