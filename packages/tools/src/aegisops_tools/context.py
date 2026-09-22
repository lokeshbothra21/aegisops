"""Per-run context every tool receives: which mode we are in and what "now" means.

Live mode: `scenario_id` is None and `now` is the wall clock.
Replay mode: `scenario_id` selects captured rows and `now` is frozen at the capture
window's end. The agent never sets these; the run that owns it does (ADR-004).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

MAX_WINDOW_MINUTES = 240


@dataclass(frozen=True)
class ToolContext:
    scenario_id: str | None = None
    frozen_now: datetime | None = None

    @property
    def now(self) -> datetime:
        return self.frozen_now or datetime.now(UTC)

    def window(self, minutes: int, *, ending_minutes_ago: int = 0) -> tuple[datetime, datetime]:
        """[start, end) for the last `minutes`, optionally shifted back by `ending_minutes_ago`."""
        minutes = max(1, min(int(minutes), MAX_WINDOW_MINUTES))
        end = self.now - timedelta(minutes=max(0, int(ending_minutes_ago)))
        return end - timedelta(minutes=minutes), end
