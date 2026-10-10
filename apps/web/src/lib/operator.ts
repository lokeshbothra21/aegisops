"use client";

// The operator's admin token, typed into the UI to approve or reject fixes on their own
// deployment. Kept in sessionStorage: this tab only, gone when it closes, never sent to
// Vercel. Visitors never need it (public runs stop at the proposal).

import { useSyncExternalStore } from "react";

const KEY = "aegisops.operator";
const listeners = new Set<() => void>();

export interface Operator {
  token: string;
  name: string;
}

function read(): string | null {
  try {
    return window.sessionStorage.getItem(KEY);
  } catch {
    return null; // storage blocked: the UI still works, read-only
  }
}

export function setOperator(op: Operator | null): void {
  try {
    if (op) window.sessionStorage.setItem(KEY, JSON.stringify(op));
    else window.sessionStorage.removeItem(KEY);
  } catch {
    // storage blocked; nothing to persist
  }
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The raw stored string is the snapshot (stable between renders); parsed by the caller. */
export function useOperator(): Operator | null {
  const raw = useSyncExternalStore(subscribe, read, () => null);
  if (!raw) return null;
  try {
    const op = JSON.parse(raw) as Operator;
    return op.token ? op : null;
  } catch {
    return null;
  }
}
