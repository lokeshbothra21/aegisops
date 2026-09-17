#!/usr/bin/env python3
"""Set a demo feature flag's default variant in demo.flagd.json (flagd hot-reloads the file).

Usage: flag.py <path/to/demo.flagd.json> <flag> <variant>
       e.g. paymentFailure 100%   |   paymentFailure off
Used by `make flag`; the agent's toggle_flag action (E5.3) will share this logic.
"""

import json
import sys


def main(path: str, name: str, variant: str) -> None:
    with open(path) as f:
        doc = json.load(f)
    try:
        flag = doc["flags"][name]
    except KeyError:
        sys.exit(f"unknown flag {name!r}; known: {sorted(doc['flags'])}")
    if variant not in flag["variants"]:
        sys.exit(f"{name}: variant {variant!r} not in {list(flag['variants'])}")
    previous = flag["defaultVariant"]
    flag["defaultVariant"] = variant
    with open(path, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    print(f"{name}: {previous} -> {variant}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
