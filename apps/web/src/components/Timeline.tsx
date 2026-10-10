import { clockTime } from "@/lib/format";
import type { Step, StepState } from "@/lib/timeline";

const DOT: Record<StepState, string> = {
  pending: "border-2 border-border bg-surface",
  active: "border-2 border-info bg-info-bg animate-pulse",
  done: "bg-ok",
  waiting: "bg-warn",
  warn: "bg-warn",
  failed: "bg-bad",
};

const STATE_LABEL: Record<StepState, string> = {
  pending: "not started",
  active: "in progress",
  done: "done",
  waiting: "waiting",
  warn: "warning",
  failed: "failed",
};

/** The graph's steps in order, each with what it concluded. Details show the raw events. */
export function Timeline({ steps }: { steps: Step[] }) {
  return (
    <ol className="relative">
      {steps.map((step, i) => (
        <li key={step.node} className="relative flex gap-3 pb-5 last:pb-0">
          {i < steps.length - 1 ? (
            <span aria-hidden className="absolute top-4 left-[7px] h-full w-px bg-border" />
          ) : null}
          <span aria-hidden className={`relative mt-1 size-[15px] shrink-0 rounded-full ${DOT[step.state]}`} />
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline gap-2">
              <h3 className={`text-sm font-medium ${step.state === "pending" ? "text-muted" : ""}`}>{step.label}</h3>
              <span className="sr-only">({STATE_LABEL[step.state]})</span>
              {step.ts ? (
                <time dateTime={step.ts} className="ml-auto font-mono text-xs text-muted">
                  {clockTime(step.ts)}
                </time>
              ) : null}
            </div>
            {step.state === "active" ? <p className="text-sm text-info">Working…</p> : null}
            {step.summary ? <p className="text-sm break-words text-muted">{step.summary}</p> : null}
            {step.events.length > 0 ? (
              <details className="mt-1">
                <summary className="cursor-pointer text-xs text-muted hover:text-text">Raw event</summary>
                <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-surface-2 p-3 font-mono text-xs leading-relaxed">
                  {JSON.stringify(step.events.map((e) => ({ type: e.type, ...e.payload })), null, 2)}
                </pre>
              </details>
            ) : null}
          </div>
        </li>
      ))}
    </ol>
  );
}
