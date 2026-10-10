import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { setOperator } from "@/lib/operator";
import { S1_RUN } from "@/test/fixtures";

import { RemediationCard } from "./RemediationCard";

afterEach(() => {
  cleanup();
  act(() => setOperator(null));
  vi.restoreAllMocks();
});

const POLICY = "autonomy level 1 waits for a human on low risk";

describe("RemediationCard", () => {
  it("never offers approval to a visitor", () => {
    render(<RemediationCard run={S1_RUN} policy={POLICY} onDecided={() => {}} />);
    expect(screen.getByText("Revert a feature flag")).toBeTruthy();
    expect(screen.getByText(/Policy: autonomy level 1/)).toBeTruthy();
    expect(screen.getByText(/Visitors can.t approve/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("lets a signed-in operator approve, signed with their name", async () => {
    act(() => setOperator({ token: "tok", name: "lokesh" }));
    const approved = { ...S1_RUN, status: "running" as const };
    const decide = vi.spyOn(api, "decide").mockResolvedValue(approved);
    const onDecided = vi.fn();
    render(<RemediationCard run={S1_RUN} policy={POLICY} onDecided={onDecided} />);

    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Approve" })));
    expect(decide).toHaveBeenCalledWith(73, "approve", "tok", "lokesh");
    expect(onDecided).toHaveBeenCalledWith(approved);
  });

  it("explains a public proposal that is never executed", () => {
    const run = { ...S1_RUN, status: "succeeded" as const, remediation: { ...S1_RUN.remediation!, decision: "not_offered" } };
    render(<RemediationCard run={run} policy={null} onDecided={() => {}} />);
    expect(screen.getByText(/proposed, never executed/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });
});
