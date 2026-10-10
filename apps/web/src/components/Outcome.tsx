import type { Run } from "@/lib/api";
import { awaitingVerification } from "@/lib/timeline";

import { Card, Notice, Spinner } from "./ui";

/** What the approved fix did, and whether the alert cleared on post-fix data. */
export function Outcome({ run }: { run: Run }) {
  const rem = run.remediation;
  const outcome = rem?.outcome;
  if (!rem?.executed_at || !outcome) return null;
  const ok = outcome.ok === true;
  const after = typeof outcome.value_after === "number" ? outcome.value_after : null;
  return (
    <Card title="Outcome">
      <div className="space-y-3">
        <Notice tone={ok ? "ok" : "bad"}>
          {ok ? "Action ran" : "Action failed"}: {String(outcome.details ?? "")}
          {outcome.simulated ? " (simulated)" : ""}
        </Notice>
        {awaitingVerification(run) ? (
          <Spinner label="Re-checking the alert on post-fix data (about 90 s after the fix)…" />
        ) : null}
        {outcome.alert_cleared === true ? (
          <Notice tone="ok">Alert cleared after the fix. Incident resolved.</Notice>
        ) : null}
        {outcome.alert_cleared === false ? (
          <Notice tone="bad">
            Still breaching after the fix{after !== null ? ` (value ${after.toFixed(2)})` : ""}. Incident marked failed.
          </Notice>
        ) : null}
      </div>
    </Card>
  );
}
