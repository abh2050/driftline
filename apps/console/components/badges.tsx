import { Badge } from "@/components/ui/badge";

const SEV: Record<string, "destructive" | "default" | "secondary" | "outline"> = {
  sev1: "destructive",
  sev2: "default",
  sev3: "secondary",
  sev4: "outline",
};

export function SeverityBadge({ severity }: { severity: string | null }) {
  return <Badge variant={severity ? (SEV[severity] ?? "outline") : "outline"}>{severity ?? "–"}</Badge>;
}

export function StatusBadge({ status }: { status: string }) {
  const waiting = status === "awaiting_human";
  return (
    <Badge variant={waiting ? "destructive" : status === "pr_open" ? "default" : "secondary"}>
      {status.replaceAll("_", " ")}
    </Badge>
  );
}
