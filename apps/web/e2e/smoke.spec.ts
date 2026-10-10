import { expect, type Page, test } from "@playwright/test";

import { INCIDENT, mockApi } from "./mock-api";

/** The page must never scroll sideways, at any width (phones included). */
async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

test("incidents list and the scenario replay entry point", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Incidents" })).toBeVisible();
  await expect(page.getByText("Payment charges fail (paymentFailure=100%)")).toBeVisible();
  await expect(page.getByRole("link", { name: /#640.*payment/ })).toBeVisible();
  await expectNoHorizontalScroll(page);
});

test("replaying a scenario opens its live investigation", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Replay investigation" }).click();
  await expect(page).toHaveURL(new RegExp(`/incidents/${INCIDENT.id}\\?run=73`));
  await expect(page.getByRole("heading", { name: /#640\s+payment/ })).toBeVisible();
});

test("incident page: steps, evidence check, and a proposal a visitor cannot approve", async ({ page }, info) => {
  await mockApi(page);
  await page.goto(`/incidents/${INCIDENT.id}?run=73`);

  const timeline = page.locator("ol").first();
  await expect(timeline.getByText("Verify evidence")).toBeVisible();
  await expect(timeline.getByText("1 of 2 citations verified, 1 dropped: 75% → 38%")).toBeVisible();
  await expect(timeline.getByText("Waiting for a human to approve or reject")).toBeVisible();

  const evidence = page.locator("section", { hasText: "Root cause and evidence" });
  await expect(evidence.getByText("After evidence check")).toBeVisible();
  await expect(evidence.getByText(/has no data for service 'payment'/)).toBeVisible();
  await expect(page.getByText("Revert a feature flag", { exact: true })).toBeVisible();
  await expect(page.getByText(/Visitors can.t approve/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);

  await expectNoHorizontalScroll(page);
  await page.screenshot({ path: info.outputPath("incident.png"), fullPage: true });
});
