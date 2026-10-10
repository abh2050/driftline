import { IncidentFeed } from "@/components/incident-feed";
import { PageHeader } from "@/components/page-header";

export default function IncidentsPage() {
  return (
    <>
      <PageHeader title="Incidents" description="Live feed of detected incidents." />
      <IncidentFeed />
    </>
  );
}
