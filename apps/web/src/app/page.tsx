import { IncidentList } from "@/components/IncidentList";
import { ScenarioReplay } from "@/components/ScenarioReplay";

export default function Home() {
  return (
    <>
      <div className="mb-8 max-w-3xl">
        <h1 className="mb-2 text-2xl font-semibold tracking-tight">Incidents</h1>
        <p className="text-sm text-muted">
          Opened by deterministic rules over OpenTelemetry data, then investigated by an agent whose
          every cited trace, log, metric and change is checked against the data before it proposes a
          fix. A human approves; on this public demo, nothing executes.
        </p>
      </div>
      <ScenarioReplay />
      <IncidentList />
    </>
  );
}
