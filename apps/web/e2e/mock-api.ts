import type { Page, Route } from "@playwright/test";

import type { Incident, Run, Scenario } from "../src/lib/api";
import { S1_EVENTS, S1_RUN } from "../src/test/fixtures";

export const API = "http://api.test";

export const INCIDENT: Incident = {
  id: 640,
  opened_at: "2026-09-25T16:57:00Z",
  closed_at: null,
  service: "payment",
  alert_rule_id: 2,
  status: "awaiting_approval",
  autonomy_level: 1,
  scenario_id: "S1",
  summary: "high-error-rate: error_rate for payment = 0.6 > 0.05 over 120s for 2 evaluations",
};

const SCENARIO: Scenario = {
  key: "S1",
  title: "Payment charges fail (paymentFailure=100%)",
  fault_type: "flag",
  expected_service: "payment",
  expected_category: "dependency_errors",
  expected_actions: ["toggle_flag"],
  window_start: "2026-09-25T16:50:00Z",
  window_end: "2026-09-25T17:05:00Z",
  notes: null,
};

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

/** Answers every API call the UI makes, like the real API would for one paused S1 run. */
export async function mockApi(page: Page, overrides: { run?: Run } = {}) {
  const run = overrides.run ?? S1_RUN;
  await page.route(`${API}/**`, async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === "/api/v1/incidents") {
      return json(route, {
        items: [INCIDENT, { ...INCIDENT, id: 639, service: "checkout", status: "resolved", summary: "5xx > 5%" }],
        next_cursor: null,
      });
    }
    if (path === "/api/v1/scenarios") return json(route, [SCENARIO]);
    if (path === "/api/v1/scenarios/S1/replay") return json(route, { ...run, served: "new" }, 202);
    if (path === `/api/v1/incidents/${INCIDENT.id}`) return json(route, INCIDENT);
    if (path === `/api/v1/incidents/${INCIDENT.id}/runs`) return json(route, [run]);
    if (path === `/api/v1/runs/${run.id}`) return json(route, run);
    if (path === `/api/v1/runs/${run.id}/events`) {
      const body = S1_EVENTS.map(
        (e) => `id: ${e.seq}\nevent: ${e.node}\ndata: ${JSON.stringify(e)}\n\n`,
      ).join("");
      return route.fulfill({ status: 200, contentType: "text/event-stream", body });
    }
    return json(route, { title: "Not mocked", detail: path }, 404);
  });
}
