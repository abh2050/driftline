import { ApprovalsList } from "@/components/approvals-list";
import { PageHeader } from "@/components/page-header";

export default function ApprovalsPage() {
  return (
    <>
      <PageHeader title="Approvals" description="Runbooks and escalations waiting for a person." />
      <ApprovalsList />
    </>
  );
}
