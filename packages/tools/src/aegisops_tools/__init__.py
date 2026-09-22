"""aegisops-tools: read-only telemetry tools for the agent (PROJECT.md §9.1) and the
`aegis-telemetry` MCP server that exposes them (ADR-007).

Design constraints: every response <= 4 KB, numbers pre-aggregated in SQL, never raw
JSONB blobs, every payload wrapped as untrusted content (E9.1). The package talks to
Postgres with raw SQL only, so it does not depend on the API's ORM models.
"""

from aegisops_tools.context import ToolContext
from aegisops_tools.untrusted import MAX_BYTES, wrap_untrusted

__all__ = ["MAX_BYTES", "ToolContext", "wrap_untrusted"]
