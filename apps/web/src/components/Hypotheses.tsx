import type { Hypothesis } from "@/lib/timeline";

import { Badge, Card } from "./ui";

export function Hypotheses({ items }: { items: Hypothesis[] }) {
  if (items.length === 0) return null;
  return (
    <Card title="Hypotheses">
      <ol className="space-y-2">
        {items.map((h) => (
          <li key={h.id} className="flex gap-2 text-sm">
            <span className="font-mono text-xs text-muted">{h.id}</span>
            <span className="min-w-0 flex-1">{h.text}</span>
            {h.supported ? <Badge tone="ok">supported</Badge> : null}
          </li>
        ))}
      </ol>
    </Card>
  );
}
