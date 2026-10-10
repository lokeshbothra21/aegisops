// Typed client for the AegisOps API (docs/PROJECT.md §7). The browser calls the API directly,
// never through a proxy, so per-visitor limits see the visitor's own address (ADR-021).

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export type IncidentStatus =
  | "open"
  | "investigating"
  | "awaiting_approval"
  | "remediating"
  | "resolved"
  | "failed";

export type RunStatus = "running" | "awaiting_approval" | "succeeded" | "failed" | "budget_exceeded";

export interface Incident {
  id: number;
  opened_at: string;
  closed_at: string | null;
  service: string;
  alert_rule_id: number | null;
  status: IncidentStatus;
  autonomy_level: number;
  scenario_id: string | null;
  summary: string | null;
}

export interface IncidentPage {
  items: Incident[];
  next_cursor: number | null;
}

export interface EvidenceRef {
  kind: "trace" | "log" | "metric" | "change" | string;
  claim: string;
  ref_id: string;
  claimed_value: number | null;
}

export interface RootCause {
  service: string;
  category: string;
  statement: string;
  confidence: number;
  claimed_confidence?: number;
  partial?: boolean;
  evidence_refs: EvidenceRef[];
  verification?: {
    cited: number;
    verified: number;
    dropped: { ref: EvidenceRef; reason: string }[];
  };
}

export interface Remediation {
  id: number;
  action: string;
  params: Record<string, unknown>;
  risk: string;
  confidence: number;
  rationale: string | null;
  decision: "pending" | "approved" | "rejected" | "auto" | "not_offered" | string;
  decided_by: string | null;
  decided_at: string | null;
  executed_at: string | null;
  outcome: Record<string, unknown> | null;
}

export interface Run {
  id: number;
  incident_id: number;
  thread_id: string;
  status: RunStatus;
  model: string;
  variant: string;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  tool_calls: number;
  duration_ms: number | null;
  started_at: string;
  finished_at: string | null;
  root_cause: RootCause | null;
  error: string | null;
  langfuse_trace_id: string | null;
  remediation: Remediation | null;
  served: "new" | "joined" | "cached";
}

export interface Scenario {
  key: string;
  title: string;
  fault_type: string;
  expected_service: string | null;
  expected_category: string | null;
  expected_actions: string[];
  window_start: string;
  window_end: string;
  notes: string | null;
}

export interface RunEvent {
  seq: number;
  node: string;
  type: string;
  payload: Record<string, unknown>;
  ts: string;
}

/** An RFC 7807 problem from the API, or a network failure (status 0). */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly title: string,
    readonly detail: string | null = null,
    readonly retryAfterS: number | null = null,
  ) {
    super(detail ? `${title}: ${detail}` : title);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { Accept: "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "Cannot reach the API", API_URL);
  }
  if (!res.ok) {
    let title = res.statusText || `HTTP ${res.status}`;
    let detail: string | null = null;
    try {
      const body = (await res.json()) as { title?: string; detail?: string };
      title = body.title ?? title;
      detail = body.detail ?? null;
    } catch {
      // not problem+json; keep the status text
    }
    const retry = Number(res.headers.get("Retry-After"));
    throw new ApiError(res.status, title, detail, Number.isFinite(retry) && retry > 0 ? retry : null);
  }
  return (await res.json()) as T;
}

function post<T>(path: string, body: unknown, token?: string): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "X-Admin-Token": token } : {}),
    },
    body: JSON.stringify(body),
  });
}

export const api = {
  incidents(params: { status?: IncidentStatus; cursor?: number; limit?: number } = {}) {
    const q = new URLSearchParams();
    if (params.status) q.set("status", params.status);
    if (params.cursor) q.set("cursor", String(params.cursor));
    q.set("limit", String(params.limit ?? 25));
    return request<IncidentPage>(`/api/v1/incidents?${q}`);
  },
  incident: (id: number) => request<Incident>(`/api/v1/incidents/${id}`),
  incidentRuns: (id: number) => request<Run[]>(`/api/v1/incidents/${id}/runs`),
  run: (id: number) => request<Run>(`/api/v1/runs/${id}`),
  scenarios: () => request<Scenario[]>("/api/v1/scenarios"),
  startRun: (incidentId: number, token?: string) =>
    post<Run>(`/api/v1/incidents/${incidentId}/runs`, {}, token),
  replay: (key: string) => post<Run>(`/api/v1/scenarios/${encodeURIComponent(key)}/replay`, {}),
  decide: (runId: number, decision: "approve" | "reject", token: string, by: string) =>
    post<Run>(`/api/v1/runs/${runId}/${decision}`, { by }, token),
  eventsUrl: (runId: number, after: number) => `${API_URL}/api/v1/runs/${runId}/events?after=${after}`,
};
