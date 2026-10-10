import { Suspense } from "react";
import { IncidentView } from "@/components/incident-view";
import { PageHeader } from "@/components/page-header";

async function Incident({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <>
      <PageHeader title={`Incident ${id}`} description="Routing, root cause, evaluation and fix." />
      <IncidentView id={id} />
    </>
  );
}

export default function IncidentPage({ params }: PageProps<"/incidents/[id]">) {
  return (
    <Suspense fallback={<PageHeader title="Incident" description="Loading…" />}>
      <Incident params={params} />
    </Suspense>
  );
}
