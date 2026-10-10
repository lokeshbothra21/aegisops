"use client";

import { useState } from "react";

import { api, type Run } from "@/lib/api";
import { absoluteTime, percent } from "@/lib/format";
import { useOperator } from "@/lib/operator";
import { humanAction } from "@/lib/timeline";

import { ErrorNote } from "./ErrorNote";
import { Badge, Button, Card, Notice, type Tone } from "./ui";

const RISK: Record<string, Tone> = { low: "ok", medium: "warn", high: "bad" };

/** The proposed fix, the policy's verdict, and (for an operator) approve / reject. */
export function RemediationCard({
  run,
  policy,
  onDecided,
}: {
  run: Run;
  policy: string | null;
  onDecided: (run: Run) => void;
}) {
  const operator = useOperator();
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const rem = run.remediation;

  if (!rem) {
    return (
      <Card title="Proposed fix">
        <p className="text-sm text-muted">
          {run.status === "running" ? "Proposed after the evidence check." : "No safe action proposed."}
        </p>
      </Card>
    );
  }

  const pending = rem.decision === "pending" && run.status === "awaiting_approval";

  async function decide(decision: "approve" | "reject") {
    if (!operator) return;
    setBusy(decision);
    setError(null);
    try {
      onDecided(await api.decide(run.id, decision, operator.token, operator.name));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card title="Proposed fix" aside={<Badge tone={RISK[rem.risk] ?? "neutral"}>{rem.risk} risk</Badge>}>
      <p className="mb-2 font-medium">{humanAction(rem.action)}</p>
      <div className="mb-3 flex flex-wrap gap-1.5">
        {Object.entries(rem.params).map(([k, v]) => (
          <span key={k} className="rounded-md bg-surface-2 px-2 py-0.5 font-mono text-xs">
            {k}: {String(v)}
          </span>
        ))}
      </div>
      {rem.rationale ? <p className="mb-3 text-sm text-muted">{rem.rationale}</p> : null}
      <p className="mb-3 text-xs text-muted">
        Confidence {percent(rem.confidence)}
        {policy ? ` · Policy: ${policy}` : null}
      </p>

      {pending && operator ? (
        <div className="space-y-2">
          <div className="flex gap-2">
            <Button className="flex-1" disabled={busy !== null} onClick={() => decide("approve")}>
              {busy === "approve" ? "Approving…" : "Approve"}
            </Button>
            <Button variant="danger" className="flex-1" disabled={busy !== null} onClick={() => decide("reject")}>
              {busy === "reject" ? "Rejecting…" : "Reject"}
            </Button>
          </div>
          {error ? <ErrorNote error={error} /> : null}
        </div>
      ) : null}
      {pending && !operator ? (
        <Notice tone="warn">Waiting for an operator to approve or reject. Visitors can&apos;t approve.</Notice>
      ) : null}
      {rem.decision === "not_offered" ? (
        <Notice>Public demo: the fix is proposed, never executed. On a real deployment an operator approves it here.</Notice>
      ) : null}
      {["approved", "rejected", "auto"].includes(rem.decision) ? (
        <Notice tone={rem.decision === "rejected" ? "bad" : "ok"}>
          {rem.decision === "auto"
            ? "Approved automatically by policy"
            : `${rem.decision === "approved" ? "Approved" : "Rejected"} by ${rem.decided_by ?? "operator"}`}
          {rem.decided_at ? ` · ${absoluteTime(rem.decided_at)}` : null}
        </Notice>
      ) : null}
    </Card>
  );
}
