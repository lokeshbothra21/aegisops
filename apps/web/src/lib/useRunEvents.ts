"use client";

// A run's live event stream over SSE (GET /runs/{id}/events).
//
// The server replays stored events, then streams live ones while the run is running, and
// closes the stream itself when there is nothing more (run ended, or a finished run fully
// replayed). Events can follow the run's `end` (the post-fix check lands ~90 s later), so we
// never close on `end`; we wait for the server. EventSource treats every close as an error and
// would reconnect forever, so on error we close it and let the page decide (from the run's
// status) whether to reconnect. Reconnects resume after the last seq we hold, and duplicates
// are dropped by seq anyway.

import { useCallback, useEffect, useRef, useState } from "react";

import { api, type RunEvent } from "./api";
import { ALL_NODES, lastSeq, mergeEvents } from "./timeline";

interface Stream {
  runId: number | null;
  events: RunEvent[];
  open: boolean;
}

export function useRunEvents(
  runId: number | null,
  handlers: { onEvent?: (ev: RunEvent) => void; onClosed?: () => void } = {},
) {
  const [stream, setStream] = useState<Stream>({ runId: null, events: [], open: false });
  const [generation, setGeneration] = useState(0);
  const held = useRef<{ runId: number | null; events: RunEvent[] }>({ runId: null, events: [] });
  const handlersRef = useRef(handlers);

  useEffect(() => {
    handlersRef.current = handlers;
  });

  useEffect(() => {
    if (runId === null) return;
    if (held.current.runId !== runId) held.current = { runId, events: [] }; // a new run starts empty
    const source = new EventSource(api.eventsUrl(runId, lastSeq(held.current.events)));
    let closed = false;

    const close = () => {
      if (closed) return;
      closed = true;
      source.close();
      setStream((s) => ({ ...s, open: false }));
      handlersRef.current.onClosed?.();
    };
    const onMessage = (msg: MessageEvent<string>) => {
      let ev: RunEvent;
      try {
        ev = JSON.parse(msg.data) as RunEvent;
      } catch {
        return;
      }
      held.current = { runId, events: mergeEvents(held.current.events, [ev]) };
      setStream({ runId, events: held.current.events, open: true });
      handlersRef.current.onEvent?.(ev);
    };

    source.onopen = () => setStream({ runId, events: held.current.events, open: true });
    source.onerror = close;
    for (const name of [...ALL_NODES, "message"]) source.addEventListener(name, onMessage);
    return () => {
      closed = true;
      source.close();
    };
  }, [runId, generation]);

  /** Open the stream again (after an approval resumes the run, or a dropped connection). */
  const reconnect = useCallback(() => setGeneration((g) => g + 1), []);

  // state from a previous run is never shown for this one
  const current = stream.runId === runId;
  return { events: current ? stream.events : [], open: current && stream.open, reconnect };
}
