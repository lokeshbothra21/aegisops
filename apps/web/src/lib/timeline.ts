// Pure functions turning a run's event stream into what the page shows. No React here, so the
// rules (ordering, dedupe, step states, the evidence view) are unit-tested on their own.

import type { EvidenceRef, RootCause, Run, RunEvent, RunStatus } from "./api";

/** The graph's nodes in order (packages/agent graph.py). Shown before they happen, as pending. */
export const PIPELINE = [
  "triage",
  "plan",
  "investigate",
  "correlate_changes",
  "root_cause",
  "verify_evidence",
  "remediate",
  "approval",
] as const;

/** Nodes that appear only after an approval, and the run's own events. */
export const AFTER = ["execute", "verification"] as const;
export const ALL_NODES = [...PIPELINE, ...AFTER, "run"] as const;

export const NODE_LABELS: Record<string, string> = {
  triage: "Triage",
  plan: "Plan",
  investigate: "Investigate",
  correlate_changes: "Correlate changes",
  root_cause: "Root cause",
  verify_evidence: "Verify evidence",
  remediate: "Propose a fix",
  approval: "Approval",
  execute: "Execute",
  verification: "Post-fix check",
  run: "Run",
};

/** Merge incoming events: unique by seq, ordered by seq. Reconnects may resend; that is fine. */
export function mergeEvents(current: RunEvent[], incoming: RunEvent[]): RunEvent[] {
  if (incoming.length === 0) return current;
  const bySeq = new Map(current.map((e) => [e.seq, e]));
  for (const e of incoming) bySeq.set(e.seq, e);
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq);
}

export const lastSeq = (events: RunEvent[]) => (events.length ? events[events.length - 1].seq : 0);

export type StepState = "pending" | "active" | "done" | "waiting" | "warn" | "failed";

export interface Step {
  node: string;
  label: string;
  state: StepState;
  events: RunEvent[];
  summary: string | null;
  ts: string | null;
}

const pct = (v: unknown) => (typeof v === "number" ? `${Math.round(v * 100)}%` : "?");
const str = (v: unknown) => (typeof v === "string" ? v : "");
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

/** One line of plain English per event. */
export function summarize(e: RunEvent): string {
  const p = e.payload ?? {};
  switch (`${e.node}:${e.type}`) {
    case "triage:output":
      return str(p.summary) || `Symptom: ${str(p.symptom)}`;
    case "plan:output":
      return `${arr(p.hypotheses).length} hypotheses to test`;
    case "investigate:output": {
      const s = arr(p.supported);
      return s.length ? `Evidence supports ${s.join(", ")}` : "No hypothesis clearly supported";
    }
    case "correlate_changes:output": {
      const top = arr(p.top);
      return top.length ? `Most likely change: ${String(top[0])}` : "No recent changes";
    }
    case "root_cause:output":
      return `${str(p.category).replaceAll("_", " ")} on ${str(p.service)}, claimed ${pct(p.confidence)}`;
    case "verify_evidence:output": {
      const dropped = arr(p.dropped).length;
      return `${p.verified ?? "?"} of ${p.cited ?? "?"} citations verified${dropped ? `, ${dropped} dropped` : ""}: ${pct(p.claimed_confidence)} → ${pct(p.confidence)}`;
    }
    case "remediate:output":
      return str(p.action) && p.action !== "none"
        ? `${humanAction(str(p.action))} (${str(p.risk)} risk)`
        : "No safe action to propose";
    case "approval:approval_requested":
      return "Waiting for a human to approve or reject";
    case "approval:approved":
      return `Approved by ${str(p.by) || "operator"}`;
    case "approval:rejected":
      return `Rejected by ${str(p.by) || "operator"}`;
    case "approval:skipped":
      return str(p.reason) || "Skipped";
    case "execute:output":
      return `${str(p.details)}${p.simulated ? " (simulated)" : ""}`;
    case "execute:failed":
      return `Failed: ${str(p.details)}`;
    case "verification:cleared":
      return "Alert cleared after the fix";
    case "verification:still_breaching":
      return `Still breaching after the fix (${typeof p.value_after === "number" ? p.value_after.toFixed(2) : "?"})`;
    case "run:end":
      return `Run ${str(p.status).replaceAll("_", " ")}`;
    case "run:failed":
      return `Run failed: ${str(p.error)}`;
    default:
      if (e.type === "llm_unavailable") return "Model unavailable: continued with a partial result";
      return e.type.replaceAll("_", " ");
  }
}

function stateOf(e: RunEvent): StepState {
  if (e.type === "failed" || e.type === "rejected") return "failed";
  if (e.type === "approval_requested") return "waiting";
  if (e.type === "llm_unavailable" || e.type === "still_breaching") return "warn";
  return "done";
}

/** The timeline: every pipeline node (pending until it reports), then whatever came after. */
export function buildSteps(events: RunEvent[], status: RunStatus | null): Step[] {
  const byNode = new Map<string, RunEvent[]>();
  for (const e of events) {
    if (e.node === "run") continue;
    byNode.set(e.node, [...(byNode.get(e.node) ?? []), e]);
  }
  const nodes = [...PIPELINE, ...AFTER.filter((n) => byNode.has(n))];
  let activeGiven = false;
  return nodes.map((node) => {
    const evs = byNode.get(node) ?? [];
    const last = evs[evs.length - 1];
    let state: StepState = last ? stateOf(last) : "pending";
    if (!last && status === "running" && !activeGiven) {
      state = "active";
      activeGiven = true;
    }
    return {
      node,
      label: NODE_LABELS[node] ?? node,
      state,
      events: evs,
      summary: last ? summarize(last) : null,
      ts: last?.ts ?? null,
    };
  });
}

export interface Hypothesis {
  id: string;
  text: string;
  supported: boolean;
}

export function hypotheses(events: RunEvent[]): Hypothesis[] {
  const plan = [...events].reverse().find((e) => e.node === "plan" && e.type === "output");
  const inv = [...events].reverse().find((e) => e.node === "investigate" && e.type === "output");
  const supported = new Set(arr(inv?.payload.supported).map(String));
  return arr(plan?.payload.hypotheses).map((text, i) => ({
    id: `H${i + 1}`,
    text: String(text),
    supported: supported.has(`H${i + 1}`),
  }));
}

export interface EvidenceView {
  statement: string;
  service: string;
  category: string;
  partial: boolean;
  claimed: number | null;
  verified: number | null;
  cited: number | null;
  kept: EvidenceRef[];
  dropped: { claim: string | null; reason: string }[];
}

/** The verified root cause: from the run row once stored (exact: which ref was dropped and
 *  why), else from the live events (counts and reasons only). */
export function evidenceView(run: Run | null, events: RunEvent[]): EvidenceView | null {
  const rc: RootCause | null = run?.root_cause ?? null;
  if (rc) {
    const v = rc.verification;
    return {
      statement: rc.statement,
      service: rc.service,
      category: rc.category,
      partial: Boolean(rc.partial),
      claimed: rc.claimed_confidence ?? null,
      verified: rc.confidence,
      cited: v?.cited ?? rc.evidence_refs.length,
      kept: rc.evidence_refs,
      dropped: (v?.dropped ?? []).map((d) => ({ claim: d.ref?.claim ?? null, reason: d.reason })),
    };
  }
  const claim = [...events].reverse().find((e) => e.node === "root_cause" && e.type === "output");
  if (!claim) return null;
  const check = [...events].reverse().find((e) => e.node === "verify_evidence" && e.type === "output");
  const p = claim.payload;
  return {
    statement: str(p.statement),
    service: str(p.service),
    category: str(p.category),
    partial: Boolean(p.partial),
    claimed: typeof p.confidence === "number" ? p.confidence : null,
    verified: check && typeof check.payload.confidence === "number" ? check.payload.confidence : null,
    cited: check && typeof check.payload.cited === "number" ? check.payload.cited : null,
    kept: check ? [] : (arr(p.evidence_refs) as EvidenceRef[]),
    dropped: arr(check?.payload.dropped).map((r) => ({ claim: null, reason: String(r) })),
  };
}

export function policyReason(events: RunEvent[]): string | null {
  const req = [...events].reverse().find((e) => e.node === "approval" && e.type === "approval_requested");
  const policy = req?.payload.policy as { reason?: string } | undefined;
  if (policy?.reason) return policy.reason;
  const rem = [...events].reverse().find((e) => e.node === "remediate" && e.type === "output");
  return typeof rem?.payload.policy === "string" ? rem.payload.policy : null;
}

export function humanAction(action: string): string {
  return (
    {
      toggle_flag: "Revert a feature flag",
      restart_service: "Restart the service",
      scale_service: "Scale the service",
      rollback_deployment: "Roll back the deployment",
    }[action] ?? action.replaceAll("_", " ")
  );
}

/** The run is finished but its post-fix check (about 90 s later) has not reported yet. */
export function awaitingVerification(run: Run | null): boolean {
  const rem = run?.remediation;
  if (!run || !rem?.executed_at || run.status === "running") return false;
  const outcome = rem.outcome ?? {};
  return outcome.ok === true && !("verified_at" in outcome);
}
