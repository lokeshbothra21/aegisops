// Real payloads from a recorded S1 run (local database, 25 Sep), trimmed.
import type { Run, RunEvent } from "@/lib/api";

let seq = 0;
const ev = (node: string, type: string, payload: Record<string, unknown>): RunEvent => ({
  seq: ++seq,
  node,
  type,
  payload,
  ts: `2026-09-25T16:58:${String(seq).padStart(2, "0")}Z`,
});

export const S1_EVENTS: RunEvent[] = [
  ev("triage", "output", {
    model: "gemini-3.5-flash-lite",
    service: "payment",
    summary: "The payment service experienced an elevated error rate of 0.6 exceeding the 0.05 threshold.",
    symptom: "error_rate",
  }),
  ev("plan", "output", {
    hypotheses: [
      "A recent flag change (paymentFailure set to 100%) caused payment errors.",
      "The restart of the payment container introduced a transient failure.",
      "Increased load from the load generator saturated payment.",
    ],
  }),
  ev("investigate", "output", { supported: ["H1"], follow_up_rounds: 0 }),
  ev("correlate_changes", "output", { top: ["paymentFailure: off -> 100%", "replicas 0 -> 1"], temporal_score: 0.819 }),
  ev("root_cause", "output", {
    service: "payment",
    category: "config_regression",
    statement: "The paymentFailure flag was set to 100%, causing payment to return errors.",
    confidence: 0.75,
    evidence_refs: [
      { kind: "change", claim: "paymentFailure flag changed from off to 100%", ref_id: "2026-09-25T16:56:15+00:00", claimed_value: null },
      { kind: "metric", claim: "error_rate exceeded threshold", ref_id: "payment_error_rate", claimed_value: 0.2 },
    ],
  }),
  ev("verify_evidence", "output", {
    cited: 2,
    verified: 1,
    dropped: ["metric 'payment_error_rate' has no data for service 'payment' in the last hour"],
    confidence: 0.375,
    claimed_confidence: 0.75,
  }),
  ev("remediate", "output", {
    action: "toggle_flag",
    risk: "low",
    params: { flag: "paymentFailure", service: "payment", variant: "off" },
    policy: "autonomy level 1 waits for a human on low risk",
    confidence: 0.375,
  }),
  ev("approval", "approval_requested", {
    policy: { auto: false, reason: "autonomy level 1 waits for a human on low risk" },
  }),
];

export const S1_RUN: Run = {
  id: 73,
  incident_id: 640,
  thread_id: "inc-640-abc",
  status: "awaiting_approval",
  model: "openai/gpt-oss-120b",
  variant: "D",
  tokens_in: 4210,
  tokens_out: 980,
  cost_usd: 0.0041,
  tool_calls: 10,
  duration_ms: null,
  started_at: "2026-09-25T16:57:00Z",
  finished_at: null,
  error: null,
  langfuse_trace_id: null,
  served: "new",
  root_cause: {
    service: "payment",
    category: "config_regression",
    statement: "The paymentFailure flag was set to 100%, causing payment to return errors.",
    confidence: 0.375,
    claimed_confidence: 0.75,
    partial: false,
    evidence_refs: [
      { kind: "change", claim: "paymentFailure flag changed from off to 100%", ref_id: "2026-09-25T16:56:15+00:00", claimed_value: null },
    ],
    verification: {
      cited: 2,
      verified: 1,
      dropped: [
        {
          ref: { kind: "metric", claim: "error_rate exceeded threshold", ref_id: "payment_error_rate", claimed_value: 0.2 },
          reason: "metric 'payment_error_rate' has no data for service 'payment' in the last hour",
        },
      ],
    },
  },
  remediation: {
    id: 9,
    action: "toggle_flag",
    params: { flag: "paymentFailure", service: "payment", variant: "off" },
    risk: "low",
    confidence: 0.375,
    rationale: "Revert the flag change that preceded the alert.",
    decision: "pending",
    decided_by: null,
    decided_at: null,
    executed_at: null,
    outcome: null,
  },
};
