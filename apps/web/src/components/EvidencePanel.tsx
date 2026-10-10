import { percent, words } from "@/lib/format";
import type { EvidenceView } from "@/lib/timeline";

import { Badge, Card } from "./ui";

function Bar({ label, value, tone }: { label: string; value: number | null; tone: "muted" | "accent" }) {
  return (
    <div>
      <div className="mb-1 flex justify-between text-xs">
        <span className="text-muted">{label}</span>
        <span className="font-mono">{percent(value)}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-surface-2">
        <div
          className={`h-full rounded-full ${tone === "accent" ? "bg-accent" : "bg-muted/50"}`}
          style={{ width: `${Math.round((value ?? 0) * 100)}%` }}
        />
      </div>
    </div>
  );
}

/** The root cause as verified: what the model claimed, and what survived the evidence check. */
export function EvidencePanel({ view }: { view: EvidenceView | null }) {
  if (!view) {
    return (
      <Card title="Root cause and evidence">
        <p className="text-sm text-muted">Appears once the agent states a root cause.</p>
      </Card>
    );
  }
  const checked = view.verified !== null;
  return (
    <Card
      title="Root cause and evidence"
      aside={view.partial ? <Badge tone="warn">partial</Badge> : null}
    >
      <div className="mb-2 flex flex-wrap gap-2">
        <Badge tone="info">{words(view.category)}</Badge>
        <Badge>{view.service}</Badge>
      </div>
      <p className="mb-4 text-sm">{view.statement}</p>

      <div className="mb-4 space-y-2">
        <Bar label="Claimed by the model" value={view.claimed} tone="muted" />
        <Bar label="After evidence check" value={view.verified} tone="accent" />
      </div>

      {checked ? (
        <p className="mb-3 text-xs text-muted">
          {view.cited !== null ? `${view.cited - view.dropped.length} of ${view.cited} citations verified against the data.` : null}{" "}
          Confidence is scaled by the share that held up.
        </p>
      ) : (
        <p className="mb-3 text-xs text-muted">Checking each citation against the data…</p>
      )}

      {view.kept.length > 0 ? (
        <>
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
            {checked ? "Verified evidence" : "Cited evidence"}
          </h3>
          <ul className="mb-3 space-y-1.5">
            {view.kept.map((ref, i) => (
              <li key={i} className="flex gap-2 text-sm">
                <span aria-hidden className={checked ? "text-ok" : "text-muted"}>
                  {checked ? "✓" : "•"}
                </span>
                <span className="min-w-0">
                  <Badge>{ref.kind}</Badge> {ref.claim}
                  <span className="block truncate font-mono text-xs text-muted">{ref.ref_id}</span>
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {view.dropped.length > 0 ? (
        <>
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Dropped</h3>
          <ul className="space-y-1.5">
            {view.dropped.map((d, i) => (
              <li key={i} className="flex gap-2 text-sm">
                <span aria-hidden className="text-bad">✗</span>
                <span className="min-w-0">
                  {d.claim ? <span className="line-through decoration-bad/60">{d.claim}</span> : null}
                  <span className="block text-xs text-muted">{d.reason}</span>
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </Card>
  );
}
