import { describe, expect, it } from "vitest";

import { S1_EVENTS, S1_RUN } from "@/test/fixtures";

import {
  awaitingVerification,
  buildSteps,
  evidenceView,
  hypotheses,
  lastSeq,
  mergeEvents,
  policyReason,
  summarize,
} from "./timeline";

describe("mergeEvents", () => {
  it("orders by seq and drops duplicates from a reconnect", () => {
    const [a, b, c] = S1_EVENTS;
    const merged = mergeEvents(mergeEvents([], [c, a]), [b, c, a]);
    expect(merged.map((e) => e.seq)).toEqual([1, 2, 3]);
    expect(lastSeq(merged)).toBe(3);
    expect(lastSeq([])).toBe(0);
  });
});

describe("buildSteps", () => {
  it("shows every node, marks the next one active while running", () => {
    const steps = buildSteps(S1_EVENTS.slice(0, 3), "running");
    expect(steps.map((s) => s.state)).toEqual([
      "done",
      "done",
      "done",
      "active",
      "pending",
      "pending",
      "pending",
      "pending",
    ]);
    expect(steps[3].label).toBe("Correlate changes");
  });

  it("marks the approval step waiting and adds post-approval steps only when they happen", () => {
    const steps = buildSteps(S1_EVENTS, "awaiting_approval");
    expect(steps.at(-1)).toMatchObject({ node: "approval", state: "waiting" });
    expect(steps.some((s) => s.node === "execute")).toBe(false);
    const after = buildSteps(
      [
        ...S1_EVENTS,
        { seq: 9, node: "approval", type: "approved", payload: { by: "shreyas" }, ts: "x" },
        { seq: 10, node: "execute", type: "output", payload: { ok: true, details: "paymentFailure: 100% -> off" }, ts: "x" },
        { seq: 11, node: "run", type: "end", payload: { status: "succeeded" }, ts: "x" },
        { seq: 12, node: "verification", type: "still_breaching", payload: { value_after: 0.29 }, ts: "x" },
      ],
      "succeeded",
    );
    expect(after.map((s) => s.node).slice(-3)).toEqual(["approval", "execute", "verification"]);
    expect(after.at(-1)).toMatchObject({ state: "warn", summary: "Still breaching after the fix (0.29)" });
    expect(after.find((s) => s.node === "approval")?.summary).toBe("Approved by shreyas");
  });

  it("never marks a step active when the run is not running", () => {
    expect(buildSteps([], "failed").every((s) => s.state === "pending")).toBe(true);
  });
});

describe("summaries", () => {
  it("says what the verifier did in one line", () => {
    expect(summarize(S1_EVENTS[5])).toBe("1 of 2 citations verified, 1 dropped: 75% → 38%");
    expect(summarize(S1_EVENTS[4])).toBe("config regression on payment, claimed 75%");
    expect(summarize(S1_EVENTS[6])).toBe("Revert a feature flag (low risk)");
  });

  it("handles model outages and unknown events without throwing", () => {
    expect(
      summarize({ seq: 1, node: "investigate", type: "llm_unavailable", payload: {}, ts: "x" }),
    ).toMatch(/partial/);
    expect(summarize({ seq: 1, node: "future_node", type: "some_new_thing", payload: {}, ts: "x" })).toBe(
      "some new thing",
    );
  });
});

describe("hypotheses", () => {
  it("numbers them like the agent does and marks the supported ones", () => {
    const hs = hypotheses(S1_EVENTS);
    expect(hs.map((h) => [h.id, h.supported])).toEqual([
      ["H1", true],
      ["H2", false],
      ["H3", false],
    ]);
  });
});

describe("evidenceView", () => {
  it("uses the stored root cause when there is one: exact kept and dropped refs", () => {
    const v = evidenceView(S1_RUN, S1_EVENTS)!;
    expect(v).toMatchObject({ claimed: 0.75, verified: 0.375, cited: 2 });
    expect(v.kept.map((r) => r.kind)).toEqual(["change"]);
    expect(v.dropped).toEqual([
      {
        claim: "error_rate exceeded threshold",
        reason: "metric 'payment_error_rate' has no data for service 'payment' in the last hour",
      },
    ]);
  });

  it("falls back to the live events: counts and reasons, before the run row has them", () => {
    const beforeCheck = evidenceView(null, S1_EVENTS.slice(0, 5))!;
    expect(beforeCheck.verified).toBeNull();
    expect(beforeCheck.kept).toHaveLength(2); // cited, not yet checked
    const afterCheck = evidenceView(null, S1_EVENTS.slice(0, 6))!;
    expect(afterCheck).toMatchObject({ verified: 0.375, cited: 2 });
    expect(afterCheck.dropped[0].reason).toMatch(/no data/);
    expect(evidenceView(null, S1_EVENTS.slice(0, 3))).toBeNull();
  });
});

describe("policy and verification", () => {
  it("reads the policy's reason from the approval request", () => {
    expect(policyReason(S1_EVENTS)).toBe("autonomy level 1 waits for a human on low risk");
  });

  it("knows when the post-fix check is still pending", () => {
    const executed = {
      ...S1_RUN,
      status: "succeeded" as const,
      remediation: { ...S1_RUN.remediation!, executed_at: "t", outcome: { ok: true, details: "done" } },
    };
    expect(awaitingVerification(executed)).toBe(true);
    const verified = {
      ...executed,
      remediation: { ...executed.remediation, outcome: { ok: true, verified_at: "t", alert_cleared: true } },
    };
    expect(awaitingVerification(verified)).toBe(false);
    expect(awaitingVerification(S1_RUN)).toBe(false);
  });
});
