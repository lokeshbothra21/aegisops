"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, ApiError, type Incident, type Run } from "@/lib/api";
import { absoluteTime, relativeTime } from "@/lib/format";
import { useOperator } from "@/lib/operator";
import { awaitingVerification, buildSteps, evidenceView, hypotheses, policyReason } from "@/lib/timeline";
import { useRunEvents } from "@/lib/useRunEvents";

import { ErrorNote } from "./ErrorNote";
import { EvidencePanel } from "./EvidencePanel";
import { Hypotheses } from "./Hypotheses";
import { Outcome } from "./Outcome";
import { RemediationCard } from "./RemediationCard";
import { RunStats } from "./RunStats";
import { StatusBadge } from "./StatusBadge";
import { Timeline } from "./Timeline";
import { Button, Card, Notice, Spinner } from "./ui";

/** Events after which the run row has changed (root cause, proposal, decision, outcome). */
const REFRESH_ON = new Set(["verify_evidence", "remediate", "approval", "execute", "verification", "run"]);
const isActive = (r: Run) => r.status === "running" || r.status === "awaiting_approval";

export function IncidentDetail({ id }: { id: number }) {
  const router = useRouter();
  const search = useSearchParams();
  const operator = useOperator();
  const [incident, setIncident] = useState<Incident | null>(null);
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<unknown>(null);

  const served = search.get("served");
  const runParam = Number(search.get("run")) || null;
  const selectedId = runParam ?? runs?.[0]?.id ?? null;

  const loadIncident = useCallback(
    () =>
      Promise.all([api.incident(id), api.incidentRuns(id)]).then(
        ([inc, list]) => {
          setIncident(inc);
          setRuns(list);
          setError(null);
        },
        (err: unknown) => setError(err),
      ),
    [id],
  );

  useEffect(() => {
    void loadIncident();
  }, [loadIncident]);

  const refreshIncident = useCallback(() => api.incident(id).then(setIncident, () => {}), [id]);

  const refreshRun = useCallback(
    (): Promise<Run | null> =>
      selectedId === null
        ? Promise.resolve(null)
        : api.run(selectedId).then(
            (r) => {
              setRun(r);
              setRuns((list) => (list ? list.map((x) => (x.id === r.id ? r : x)) : list));
              return r;
            },
            () => null,
          ),
    [selectedId],
  );

  useEffect(() => {
    void refreshRun();
  }, [refreshRun]);

  // the stream closed: if the run is still going (a dropped connection), resume it
  const reconnectRef = useRef<() => void>(() => {});
  const { events, open, reconnect } = useRunEvents(selectedId, {
    // pick up the run row's new fields as the steps that change it report
    onEvent: (ev) => {
      if (REFRESH_ON.has(ev.node)) void refreshRun();
    },
    onClosed: () => {
      void refreshIncident();
      void refreshRun().then((r) => {
        if (r?.status === "running") window.setTimeout(() => reconnectRef.current(), 1500);
      });
    },
  });
  useEffect(() => {
    reconnectRef.current = reconnect;
  }, [reconnect]);

  // the run shown is always the selected one, never the previous selection's
  const shown = run && run.id === selectedId ? run : null;

  // after a fix, the post-fix check lands about 90 s later: poll, then fetch its event
  const verifying = awaitingVerification(shown);
  const wasVerifying = useRef(false);
  useEffect(() => {
    if (verifying) {
      wasVerifying.current = true;
      const t = window.setInterval(() => void refreshRun(), 10_000);
      return () => window.clearInterval(t);
    }
    if (wasVerifying.current) {
      wasVerifying.current = false;
      reconnect();
      void refreshIncident();
    }
  }, [verifying, refreshRun, refreshIncident, reconnect]);

  const steps = useMemo(() => buildSteps(events, shown?.status ?? null), [events, shown?.status]);
  const hyps = useMemo(() => hypotheses(events), [events]);
  const evidence = useMemo(() => evidenceView(shown, events), [shown, events]);
  const policy = useMemo(() => policyReason(events), [events]);

  const select = (runId: number) => router.replace(`/incidents/${id}?run=${runId}`, { scroll: false });

  async function start() {
    setStarting(true);
    setStartError(null);
    try {
      const r = await api.startRun(id, operator?.token);
      setRuns((list) => [r, ...(list ?? []).filter((x) => x.id !== r.id)]);
      const q = new URLSearchParams({ run: String(r.id) });
      if (r.served !== "new") q.set("served", r.served);
      router.replace(`/incidents/${id}?${q}`, { scroll: false });
    } catch (err) {
      setStartError(err);
      if (err instanceof ApiError && err.status === 409) void loadIncident();
    } finally {
      setStarting(false);
    }
  }

  if (error) {
    return (
      <div className="space-y-4">
        <BackLink />
        <ErrorNote error={error} />
      </div>
    );
  }
  if (!incident || !runs) return <Spinner label="Loading incident" />;

  const active = runs.some(isActive);
  const live = shown?.status === "running" && open;

  return (
    <div>
      <BackLink />
      <div className="mt-3 mb-6">
        <div className="mb-1 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">
            <span className="font-mono text-muted">#{incident.id}</span> {incident.service}
          </h1>
          <StatusBadge status={incident.status} />
        </div>
        {incident.summary ? <p className="mb-1 text-sm">{incident.summary}</p> : null}
        <p className="text-xs text-muted">
          Opened <time dateTime={incident.opened_at} title={absoluteTime(incident.opened_at)}>{relativeTime(incident.opened_at)}</time>
          {incident.scenario_id ? ` · scenario ${incident.scenario_id}` : ""} · autonomy level {incident.autonomy_level}
        </p>
      </div>

      {served === "cached" ? (
        <div className="mb-4">
          <Notice tone="warn">
            Today&apos;s demo limit is reached, so this is the most recent finished investigation of
            this incident rather than a new one.
          </Notice>
        </div>
      ) : null}
      {served === "joined" ? (
        <div className="mb-4">
          <Notice>Someone started this replay moments ago: you are watching the same live run.</Notice>
        </div>
      ) : null}

      <div className="mb-4 flex flex-wrap items-center gap-3">
        {runs.length > 1 ? (
          <label className="flex items-center gap-2 text-sm">
            <span className="text-muted">Run</span>
            <select
              value={selectedId ?? ""}
              onChange={(e) => select(Number(e.target.value))}
              className="rounded-md border border-border bg-surface px-2 py-1.5 text-sm"
            >
              {runs.map((r) => (
                <option key={r.id} value={r.id}>
                  #{r.id} · {r.status.replaceAll("_", " ")} · {relativeTime(r.started_at)}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {live ? (
          <span className="inline-flex items-center gap-1.5 text-xs text-info">
            <span aria-hidden className="size-2 animate-pulse rounded-full bg-info" /> Live
          </span>
        ) : null}
        {!active ? (
          <Button className="ml-auto" onClick={start} disabled={starting}>
            {starting ? "Starting…" : runs.length ? "Investigate again" : "Start investigation"}
          </Button>
        ) : null}
      </div>
      {startError ? (
        <div className="mb-4">
          <ErrorNote error={startError} />
        </div>
      ) : null}

      {selectedId === null ? (
        <p className="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted">
          No investigation yet. Start one to watch the agent work through this incident step by step.
        </p>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
          <div className="space-y-4">
            <Card title="Investigation" aside={shown ? <StatusBadge status={shown.status} /> : null}>
              <Timeline steps={steps} />
            </Card>
            <Hypotheses items={hyps} />
          </div>
          <div className="space-y-4">
            <EvidencePanel view={evidence} />
            {shown ? (
              <>
                <RemediationCard
                  run={shown}
                  policy={policy}
                  onDecided={(r) => {
                    setRun(r);
                    reconnect();
                    void refreshIncident();
                  }}
                />
                <Outcome run={shown} />
                <RunStats run={shown} />
              </>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}

function BackLink() {
  return (
    <Link href="/" className="text-sm text-muted hover:text-text">
      ← All incidents
    </Link>
  );
}
