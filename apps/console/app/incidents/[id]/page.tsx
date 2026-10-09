import { Suspense } from "react";
import { PageHeader } from "@/components/page-header";

async function IncidentTitle({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <PageHeader title={`Incident ${id}`} description="Routing, RCA, evals and PR." />;
}

export default function IncidentPage({ params }: PageProps<"/incidents/[id]">) {
  return (
    <Suspense fallback={<PageHeader title="Incident" description="Loading…" />}>
      <IncidentTitle params={params} />
    </Suspense>
  );
}
