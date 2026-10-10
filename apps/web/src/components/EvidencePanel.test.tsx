import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { evidenceView } from "@/lib/timeline";
import { S1_EVENTS, S1_RUN } from "@/test/fixtures";

import { EvidencePanel } from "./EvidencePanel";

afterEach(cleanup);

describe("EvidencePanel", () => {
  it("shows claimed vs verified confidence, what held up and what was dropped and why", () => {
    render(<EvidencePanel view={evidenceView(S1_RUN, S1_EVENTS)} />);
    expect(screen.getByText("Claimed by the model").nextSibling?.textContent).toBe("75%");
    expect(screen.getByText("After evidence check").nextSibling?.textContent).toBe("38%");
    expect(screen.getByText(/1 of 2 citations verified/)).toBeTruthy();
    expect(screen.getByText("Verified evidence")).toBeTruthy();
    expect(screen.getByText(/paymentFailure flag changed/)).toBeTruthy();
    expect(screen.getByText("error_rate exceeded threshold").className).toMatch(/line-through/);
    expect(screen.getByText(/has no data for service 'payment'/)).toBeTruthy();
  });

  it("says the check is running while only the claim exists", () => {
    render(<EvidencePanel view={evidenceView(null, S1_EVENTS.slice(0, 5))} />);
    expect(screen.getByText(/Checking each citation/)).toBeTruthy();
    expect(screen.getByText("Cited evidence")).toBeTruthy();
    expect(screen.queryByText("Dropped")).toBeNull();
  });

  it("has an empty state before any root cause", () => {
    render(<EvidencePanel view={null} />);
    expect(screen.getByText(/once the agent states a root cause/)).toBeTruthy();
  });
});
