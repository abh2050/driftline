import { PageHeader } from "@/components/page-header";
import { RouterReportView } from "@/components/router-report";

export default function RouterPage() {
  return (
    <>
      <PageHeader title="Router benchmark" description="Latest layer 2a router report." />
      <RouterReportView />
    </>
  );
}
