"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api, type Incident, type IncidentStatus } from "@/lib/api";
import { absoluteTime, relativeTime } from "@/lib/format";

import { ErrorNote } from "./ErrorNote";
import { StatusBadge } from "./StatusBadge";
import { Button, Spinner } from "./ui";

const FILTERS: { label: string; status?: IncidentStatus }[] = [
  { label: "All" },
  { label: "Open", status: "open" },
  { label: "Investigating", status: "investigating" },
  { label: "Awaiting approval", status: "awaiting_approval" },
  { label: "Resolved", status: "resolved" },
  { label: "Failed", status: "failed" },
];

export function IncidentList() {
  const [filter, setFilter] = useState<IncidentStatus | undefined>();
  const [items, setItems] = useState<Incident[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  // first page for the current filter; later pages append (from the button)
  useEffect(() => {
    let cancelled = false;
    api.incidents({ status: filter }).then(
      (page) => {
        if (cancelled) return;
        setItems(page.items);
        setCursor(page.next_cursor);
        setError(null);
        setLoading(false);
      },
      (err: unknown) => {
        if (cancelled) return;
        setError(err);
        setLoading(false);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [filter]);

  function choose(status: IncidentStatus | undefined) {
    if (status === filter) return;
    setLoading(true);
    setItems([]);
    setFilter(status);
  }

  async function loadMore() {
    if (!cursor) return;
    setLoading(true);
    try {
      const page = await api.incidents({ status: filter, cursor });
      setItems((prev) => [...prev, ...page.items]);
      setCursor(page.next_cursor);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section>
      <div className="mb-3 flex flex-wrap items-center gap-2" role="group" aria-label="Filter by status">
        {FILTERS.map((f) => (
          <button
            key={f.label}
            type="button"
            aria-pressed={filter === f.status}
            onClick={() => choose(f.status)}
            className={`rounded-full border px-3 py-1 text-xs ${
              filter === f.status ? "border-accent bg-accent text-accent-text" : "border-border bg-surface text-muted hover:text-text"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {error ? <ErrorNote error={error} /> : null}

      {!error && !loading && items.length === 0 ? (
        <p className="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted">
          No incidents{filter ? " with this status" : " yet"}. Incidents open when an alert rule
          breaches on incoming telemetry.
        </p>
      ) : null}

      {items.length > 0 ? (
        <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-surface">
          {items.map((inc) => (
            <li key={inc.id}>
              <Link
                href={`/incidents/${inc.id}`}
                className="grid grid-cols-[auto_1fr_auto] items-center gap-x-3 gap-y-1 px-4 py-3 hover:bg-surface-2 sm:grid-cols-[4rem_10rem_1fr_9rem_7rem]"
              >
                <span className="font-mono text-xs text-muted">#{inc.id}</span>
                <span className="truncate text-sm font-medium">{inc.service}</span>
                <span className="col-span-3 row-start-2 truncate text-sm text-muted sm:col-span-1 sm:row-start-auto">
                  {inc.summary ?? "–"}
                </span>
                <span className="col-start-3 row-start-1 justify-self-end sm:col-start-auto sm:row-start-auto sm:justify-self-start">
                  <StatusBadge status={inc.status} />
                </span>
                <time
                  dateTime={inc.opened_at}
                  title={absoluteTime(inc.opened_at)}
                  className="hidden text-right text-xs text-muted sm:block"
                >
                  {relativeTime(inc.opened_at)}
                </time>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}

      <div className="mt-4 flex justify-center">
        {loading ? (
          <Spinner label="Loading incidents" />
        ) : cursor ? (
          <Button variant="secondary" onClick={loadMore}>
            Load more
          </Button>
        ) : null}
      </div>
    </section>
  );
}
