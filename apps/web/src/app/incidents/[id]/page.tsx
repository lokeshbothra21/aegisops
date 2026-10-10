import { notFound } from "next/navigation";
import { Suspense } from "react";

import { IncidentDetail } from "@/components/IncidentDetail";
import { Spinner } from "@/components/ui";

export default async function IncidentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const incidentId = Number(id);
  if (!Number.isInteger(incidentId) || incidentId <= 0) notFound();
  return (
    <Suspense fallback={<Spinner label="Loading incident" />}>
      <IncidentDetail id={incidentId} />
    </Suspense>
  );
}
