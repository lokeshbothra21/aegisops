"use client";

import { useState } from "react";

import { setOperator, useOperator } from "@/lib/operator";

import { Button } from "./ui";

/** Sign in as the operator of your own deployment to approve fixes. Visitors never need this. */
export function OperatorControl() {
  const operator = useOperator();
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState("");
  const [name, setName] = useState("");

  return (
    <div className="relative">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className={`rounded-md px-2 py-1 hover:text-text ${operator ? "bg-ok-bg text-ok" : ""}`}
      >
        {operator ? `Operator: ${operator.name}` : "Operator"}
      </button>
      {open ? (
        <div className="absolute right-0 z-10 mt-2 w-72 rounded-xl border border-border bg-surface p-4 text-text shadow-lg">
          {operator ? (
            <div className="space-y-3 text-sm">
              <p>
                Approvals are signed as <strong>{operator.name}</strong>. The token stays in this tab only.
              </p>
              <Button
                variant="secondary"
                className="w-full"
                onClick={() => {
                  setOperator(null);
                  setOpen(false);
                }}
              >
                Sign out
              </Button>
            </div>
          ) : (
            <form
              className="space-y-3 text-sm"
              onSubmit={(e) => {
                e.preventDefault();
                if (!token.trim()) return;
                setOperator({ token: token.trim(), name: name.trim() || "operator" });
                setToken("");
                setOpen(false);
              }}
            >
              <p className="text-muted">
                Only for approving fixes on your own deployment. The public demo never executes
                actions, so visitors don&apos;t need this.
              </p>
              <label className="block">
                <span className="mb-1 block text-xs text-muted">Your name (recorded in the audit log)</span>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  maxLength={64}
                  className="w-full rounded-md border border-border bg-bg px-2 py-1.5"
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs text-muted">Admin token</span>
                <input
                  type="password"
                  autoComplete="off"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  className="w-full rounded-md border border-border bg-bg px-2 py-1.5"
                />
              </label>
              <Button type="submit" className="w-full" disabled={!token.trim()}>
                Sign in for this tab
              </Button>
            </form>
          )}
        </div>
      ) : null}
    </div>
  );
}
