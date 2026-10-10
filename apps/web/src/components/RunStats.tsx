import type { Run } from "@/lib/api";
import { duration, usd } from "@/lib/format";

import { StatusBadge } from "./StatusBadge";
import { Card } from "./ui";

export function RunStats({ run }: { run: Run }) {
  const rows: [string, React.ReactNode][] = [
    ["Status", <StatusBadge key="s" status={run.status} />],
    ["Model", run.model || "–"],
    ["Duration", duration(run.duration_ms)],
    ["Tool calls", run.tool_calls],
    ["Tokens", `${run.tokens_in.toLocaleString()} in / ${run.tokens_out.toLocaleString()} out`],
    ["Cost", usd(run.cost_usd)],
  ];
  return (
    <Card title={`Run #${run.id}`}>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted">{k}</dt>
            <dd className="min-w-0 truncate text-right font-mono text-xs leading-5">{v}</dd>
          </div>
        ))}
      </dl>
      {run.error ? <p className="mt-3 text-xs text-bad">{run.error}</p> : null}
    </Card>
  );
}
