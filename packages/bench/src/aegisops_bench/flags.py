"""Set a demo feature flag's default variant by editing demo.flagd.json (flagd hot-reloads).

Same logic as infra/otel-demo/flag.py (the Makefile helper); the runner and, in Week 6,
the agent's toggle_flag action use this module.
"""

import json
from pathlib import Path


def set_flag(path: Path, name: str, variant: str) -> str:
    """Return the previous variant. Raises KeyError / ValueError for unknown flag / variant."""
    doc = json.loads(path.read_text())
    flag = doc["flags"][name]
    if variant not in flag["variants"]:
        raise ValueError(f"{name}: variant {variant!r} not in {sorted(flag['variants'])}")
    previous: str = flag["defaultVariant"]
    flag["defaultVariant"] = variant
    path.write_text(json.dumps(doc, indent=2) + "\n")
    return previous


def current_variant(path: Path, name: str) -> str:
    variant: str = json.loads(path.read_text())["flags"][name]["defaultVariant"]
    return variant
