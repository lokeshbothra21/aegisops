import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { S1_EVENTS } from "@/test/fixtures";

import { useRunEvents } from "./useRunEvents";

/** Minimal EventSource stand-in: records instances, lets the test push named events. */
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners = new Map<string, ((msg: MessageEvent<string>) => void)[]>();
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(name: string, fn: (msg: MessageEvent<string>) => void) {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), fn]);
  }
  close() {
    this.closed = true;
  }
  emit(event: { seq: number; node: string; type: string; payload?: object }) {
    const data = JSON.stringify({ payload: {}, ts: "t", ...event });
    for (const fn of this.listeners.get(event.node) ?? []) fn({ data } as MessageEvent<string>);
  }
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});
afterEach(() => vi.unstubAllGlobals());

const last = () => FakeEventSource.instances.at(-1)!;

describe("useRunEvents", () => {
  it("collects events in order, keeps events after the run's end, and stops when the server closes", () => {
    const onClosed = vi.fn();
    const onEvent = vi.fn();
    const { result } = renderHook(() => useRunEvents(7, { onEvent, onClosed }));
    expect(last().url).toMatch(/\/api\/v1\/runs\/7\/events\?after=0$/);

    act(() => {
      last().onopen?.();
      last().emit(S1_EVENTS[1]);
      last().emit(S1_EVENTS[0]);
      last().emit(S1_EVENTS[0]); // duplicate after a reconnect
    });
    expect(result.current.open).toBe(true);
    expect(result.current.events.map((e) => e.seq)).toEqual([1, 2]);
    expect(onEvent).toHaveBeenCalledTimes(3);

    // the post-fix check is stored after `run:end`; it must not be cut off
    act(() => {
      last().emit({ seq: 3, node: "run", type: "end" });
      last().emit({ seq: 4, node: "verification", type: "cleared" });
    });
    expect(last().closed).toBe(false);
    expect(result.current.events.map((e) => e.node).slice(-2)).toEqual(["run", "verification"]);

    act(() => last().onerror?.()); // the server ended the stream
    expect(last().closed).toBe(true);
    expect(result.current.open).toBe(false);
    expect(onClosed).toHaveBeenCalledTimes(1);
  });

  it("closes on error instead of letting the browser retry forever; reconnect resumes after the last seq", () => {
    const onClosed = vi.fn();
    const { result } = renderHook(() => useRunEvents(7, { onClosed }));
    act(() => {
      last().emit(S1_EVENTS[0]);
      last().emit(S1_EVENTS[1]);
      last().onerror?.();
    });
    expect(last().closed).toBe(true);
    expect(onClosed).toHaveBeenCalledTimes(1);

    act(() => result.current.reconnect());
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(last().url).toMatch(/after=2$/);
    expect(result.current.events).toHaveLength(2); // kept across the reconnect
  });

  it("starts empty for a different run and closes the old stream", () => {
    const { result, rerender } = renderHook(({ id }) => useRunEvents(id), { initialProps: { id: 7 } });
    act(() => last().emit(S1_EVENTS[0]));
    const first = last();
    rerender({ id: 8 });
    expect(first.closed).toBe(true);
    expect(last().url).toMatch(/runs\/8\/events\?after=0$/);
    expect(result.current.events).toEqual([]);
  });

  it("does nothing without a run", () => {
    renderHook(() => useRunEvents(null));
    expect(FakeEventSource.instances).toHaveLength(0);
  });
});
