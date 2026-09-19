"""flagd config watcher (E2.1): every change of a flag's default variant becomes a
`change_events(type=flag)` row.

flagd reads `demo.flagd.json` and hot-reloads it; so do we. The watcher polls the
file's mtime (cheap, dependency-free), and on change diffs `defaultVariant` per flag
against the last snapshot. The first read is the baseline and records nothing.
Changes made while the API is down are not seen; that is acceptable for a laptop
tool and is noted in the runbook.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from aegisops_api.models import ChangeEvent, ChangeType
from aegisops_api.targets import TargetConfig

log = structlog.get_logger()
ACTOR = "flagd-file"  # edited on disk by a human, `make flag`, or (later) the agent's action


def read_variants(path: Path) -> dict[str, dict[str, Any]]:
    """flag -> {"variant": defaultVariant, "value": variants[defaultVariant]}."""
    doc = json.loads(path.read_text())
    out: dict[str, dict[str, Any]] = {}
    for name, flag in doc.get("flags", {}).items():
        variant = flag.get("defaultVariant")
        out[name] = {"variant": variant, "value": flag.get("variants", {}).get(variant)}
    return out


def diff_variants(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """Flags whose default variant changed, as (flag, before, after)."""
    changed = []
    for name in sorted(set(before) | set(after)):
        b, a = before.get(name, {}), after.get(name, {})
        if b.get("variant") != a.get("variant"):
            changed.append((name, b, a))
    return changed


@dataclass
class FlagWatcher:
    path: Path
    target: TargetConfig
    _mtime: float | None = field(default=None, repr=False)
    _snapshot: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)

    async def tick(self, session: AsyncSession) -> int:
        """Return the number of change events recorded this tick."""
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            log.warning("flag_watcher.missing", path=str(self.path))
            return 0
        if mtime == self._mtime:
            return 0
        current = read_variants(self.path)
        if self._mtime is None:  # baseline
            self._mtime, self._snapshot = mtime, current
            log.info("flag_watcher.baseline", flags=len(current))
            return 0
        changes = diff_variants(self._snapshot, current)
        now = datetime.now(UTC)
        for flag, before, after in changes:
            session.add(
                ChangeEvent(
                    ts=now,
                    type=ChangeType.flag,
                    service=self.target.service_for_flag(flag),
                    before={"flag": flag, **before},
                    after={"flag": flag, **after},
                    actor=ACTOR,
                )
            )
            log.info(
                "change_event.flag",
                flag=flag,
                **{"from": before.get("variant"), "to": after.get("variant")},
            )
        self._mtime, self._snapshot = mtime, current
        return len(changes)
