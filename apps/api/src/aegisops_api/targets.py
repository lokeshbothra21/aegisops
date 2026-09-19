"""Target-system specifics (PROJECT.md changelog 1.0.3: values that name a particular
system live in `config/targets/<name>.yaml`, never in code).

Today: the OpenTelemetry Demo's feature flags and the service each one affects.
"""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class FlagSpec(BaseModel):
    service: str
    description: str = ""


class TargetConfig(BaseModel):
    name: str
    flags: dict[str, FlagSpec] = Field(default_factory=dict)

    def service_for_flag(self, flag: str) -> str | None:
        spec = self.flags.get(flag)
        return spec.service if spec else None


@lru_cache
def load_target(path: str) -> TargetConfig:
    with Path(path).open() as f:
        return TargetConfig.model_validate(yaml.safe_load(f))
