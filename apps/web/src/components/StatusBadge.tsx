import type { IncidentStatus, RunStatus } from "@/lib/api";
import { words } from "@/lib/format";

import { Badge, type Tone } from "./ui";

const TONE: Record<IncidentStatus | RunStatus, Tone> = {
  open: "bad",
  investigating: "info",
  awaiting_approval: "warn",
  remediating: "info",
  resolved: "ok",
  failed: "bad",
  running: "info",
  succeeded: "ok",
  budget_exceeded: "warn",
};

export function StatusBadge({ status }: { status: IncidentStatus | RunStatus }) {
  return <Badge tone={TONE[status] ?? "neutral"}>{words(status)}</Badge>;
}
