"""Untrusted-content wrapping (E9.1, PROJECT.md §8.5, §11).

Everything a tool returns came from the target system, which an attacker may control
(a log line can say "ignore previous instructions"). The model must treat it as DATA.
Two mechanisms: the payload is serialised as JSON inside a tagged envelope, and the
system prompt states that content inside the envelope is never an instruction.

Defence in depth: the closing tag cannot be forged from inside the payload because
JSON serialisation escapes `<` and `>`; and the whole payload is capped at MAX_BYTES,
truncating lists rather than failing, so a hostile or noisy service cannot blow the
model's context.
"""

import json
from typing import Any

MAX_BYTES = 4096
OPEN = '<telemetry untrusted="true">'
CLOSE = "</telemetry>"


def _dumps(payload: Any) -> str:
    # ensure_ascii + explicit angle-bracket escaping: a payload can never contain a literal tag
    return (
        json.dumps(payload, separators=(",", ":"), default=str)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def _lists(
    obj: Any, path: tuple[Any, ...] = (), depth: int = 0
) -> list[tuple[tuple[Any, ...], int]]:
    """(path, length) of every list with > 1 item, up to three levels deep."""
    found: list[tuple[tuple[Any, ...], int]] = []
    if depth > 3:
        return found
    if isinstance(obj, list):
        if len(obj) > 1:
            found.append((path, len(obj)))
        for i, v in enumerate(obj):
            found += _lists(v, (*path, i), depth + 1)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            found += _lists(v, (*path, k), depth + 1)
    return found


def _trim(obj: Any, path: tuple[Any, ...], keep: int) -> None:
    for key in path[:-1]:
        obj = obj[key]
    obj[path[-1]] = obj[path[-1]][:keep]


def shrink(payload: Any, limit: int = MAX_BYTES) -> Any:
    """Return `payload` if it fits in `limit` bytes, otherwise trim the longest list (at any
    depth up to three) by a quarter, repeatedly, and mark `truncated: true`."""
    if len(_dumps(payload)) <= limit or not isinstance(payload, dict):
        return payload
    out: dict[str, Any] = json.loads(json.dumps(payload, default=str))  # deep copy, JSON-safe
    for _ in range(200):
        if len(_dumps(out)) <= limit:
            break
        lists = _lists(out)
        if not lists:
            break
        path, length = max(lists, key=lambda pl: pl[1])
        _trim(out, path, max(1, length * 3 // 4))
    out["truncated"] = True
    return out


def wrap_untrusted(payload: Any) -> str:
    """Serialise `payload` (shrunk to MAX_BYTES) inside the untrusted envelope."""
    return f"{OPEN}{_dumps(shrink(payload))}{CLOSE}"
