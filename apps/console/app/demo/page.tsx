import { Suspense } from "react";
import { DemoControls } from "@/components/demo-controls";
import { PageHeader } from "@/components/page-header";
import { currentRoles } from "@/lib/principal";

// The admin role is checked here and again by ops-api on every demo route (spec 5.2.3f).
async function AdminOnly() {
  const roles = await currentRoles();
  if (!roles.includes("admin")) {
    return (
      <p role="alert" className="text-destructive">
        Admin role required. Your roles: {roles.length ? roles.join(", ") : "none"}.
      </p>
    );
  }
  return <DemoControls />;
}

export default function DemoPage() {
  return (
    <>
      <PageHeader title="Demo controls" description="Inject faults, start replays, reset patient-rag. Admin only." />
      <Suspense fallback={<p className="text-muted-foreground">Checking your role…</p>}>
        <AdminOnly />
      </Suspense>
    </>
  );
}
