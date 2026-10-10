"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { api, type Scenario } from "@/lib/api";
import { words } from "@/lib/format";

import { ErrorNote } from "./ErrorNote";
import { Badge, Button } from "./ui";

/** The public demo's entry point: replay the agent on a captured real fault. */
export function ScenarioReplay() {
  const router = useRouter();
  const [scenarios, setScenarios] = useState<Scenario[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api.scenarios().then(setScenarios, () => setScenarios([]));
  }, []);

  if (!scenarios || scenarios.length === 0) return null;

  async function replay(key: string) {
    setBusy(key);
    setError(null);
    try {
      const run = await api.replay(key);
      const q = new URLSearchParams({ run: String(run.id) });
      if (run.served !== "new") q.set("served", run.served);
      router.push(`/incidents/${run.incident_id}?${q}`);
    } catch (err) {
      setError(err);
      setBusy(null);
    }
  }

  return (
    <section className="mb-8">
      <h2 className="mb-1 text-base font-semibold">Watch it investigate a real fault</h2>
      <p className="mb-4 text-sm text-muted">
        Each scenario is a fault injected into a 15-service system and captured. Replaying runs the
        agent on that data, live: you see each step, what it claims, and what survives the evidence check.
      </p>
      {error ? <div className="mb-3"><ErrorNote error={error} /></div> : null}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {scenarios.map((s) => (
          <article key={s.key} className="flex flex-col rounded-xl border border-border bg-surface p-4">
            <div className="mb-2 flex items-center gap-2">
              <Badge tone="info">{s.key}</Badge>
              {s.expected_service ? <span className="text-xs text-muted">{s.expected_service}</span> : null}
            </div>
            <h3 className="mb-1 text-sm font-medium">{s.title}</h3>
            {s.expected_category ? (
              <p className="mb-4 text-xs text-muted">Expected: {words(s.expected_category)}</p>
            ) : null}
            <Button className="mt-auto" disabled={busy !== null} onClick={() => replay(s.key)}>
              {busy === s.key ? "Starting…" : "Replay investigation"}
            </Button>
          </article>
        ))}
      </div>
    </section>
  );
}
