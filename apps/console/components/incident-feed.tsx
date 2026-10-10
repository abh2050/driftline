"use client";

import Link from "next/link";
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { SeverityBadge, StatusBadge } from "@/components/badges";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ops } from "@/lib/ops";
import type { IncidentSummary } from "@/lib/types";

const NOTIFY_SEVERITIES = new Set(["sev1", "sev2"]);

type Permission = NotificationPermission | "unsupported";
const permissionSnapshot = (): Permission =>
  typeof Notification === "undefined" ? "unsupported" : Notification.permission;
const subscribeFocus = (cb: () => void) => {
  window.addEventListener("focus", cb);
  return () => window.removeEventListener("focus", cb);
};

function shouldNotify(prev: IncidentSummary | undefined, next: IncidentSummary): string | null {
  if (!prev && next.severity && NOTIFY_SEVERITIES.has(next.severity)) {
    return `${next.severity.toUpperCase()} incident: ${next.breached_metrics.join(", ")}`;
  }
  if (next.awaiting === "escalation" && prev?.awaiting !== "escalation") {
    return `Escalation needs a person: ${next.id}`;
  }
  if (prev && !prev.severity && next.severity && NOTIFY_SEVERITIES.has(next.severity)) {
    return `${next.severity.toUpperCase()} incident: ${next.breached_metrics.join(", ")}`;
  }
  return null;
}

export function IncidentFeed() {
  const [items, setItems] = useState<Record<string, IncidentSummary>>({});
  const [live, setLive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [, setAsked] = useState(0);
  const permission = useSyncExternalStore(subscribeFocus, permissionSnapshot, () => "unsupported" as Permission);

  useEffect(() => {
    ops<IncidentSummary[]>("/incidents?limit=100")
      .then((rows) => setItems(Object.fromEntries(rows.map((r) => [r.id, r]))))
      .catch((e: Error) => setError(e.message));
    const source = new EventSource("/api/ops/stream");
    source.onopen = () => setLive(true);
    source.onerror = () => setLive(false);
    source.addEventListener("incident", (ev) => {
      const next = (JSON.parse((ev as MessageEvent<string>).data) as { incident: IncidentSummary }).incident;
      setItems((cur) => {
        const message = shouldNotify(cur[next.id], next);
        if (message && typeof Notification !== "undefined" && Notification.permission === "granted") {
          new Notification("Driftline", { body: message, tag: `${next.id}-${next.status}` });
        }
        return { ...cur, [next.id]: { ...cur[next.id], ...next } };
      });
    });
    return () => source.close();
  }, []);

  const rows = useMemo(
    () => Object.values(items).sort((a, b) => (b.detected_at ?? "").localeCompare(a.detected_at ?? "")),
    [items],
  );

  return (
    <section aria-label="Incident feed" className="space-y-4">
      <div className="flex items-center gap-3 text-sm">
        <span
          aria-label={live ? "live" : "offline"}
          className={`inline-block size-2 rounded-full ${live ? "bg-green-500" : "bg-muted-foreground"}`}
        />
        <span>{live ? "Live" : "Connecting…"}</span>
        {permission === "default" && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => Notification.requestPermission().then(() => setAsked((n) => n + 1))}
          >
            Enable notifications
          </Button>
        )}
        {error && <span className="text-destructive">{error}</span>}
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Incident</TableHead>
            <TableHead>Severity</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Metrics</TableHead>
            <TableHead>Specialist</TableHead>
            <TableHead>Action</TableHead>
            <TableHead>Detected</TableHead>
            <TableHead>PR</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.id} data-testid="incident-row">
              <TableCell>
                <Link className="font-mono text-xs underline" href={`/incidents/${r.id}`}>
                  {r.id}
                </Link>
              </TableCell>
              <TableCell>
                <SeverityBadge severity={r.severity} />
              </TableCell>
              <TableCell>
                <StatusBadge status={r.status} />
              </TableCell>
              <TableCell className="text-xs">{r.breached_metrics.join(", ")}</TableCell>
              <TableCell>{r.specialist ?? "–"}</TableCell>
              <TableCell>{r.action ?? "–"}</TableCell>
              <TableCell className="text-xs">
                {r.detected_at ? new Date(r.detected_at).toLocaleString() : "–"}
              </TableCell>
              <TableCell>
                {r.pr_url ? (
                  <a className="underline" href={r.pr_url} target="_blank" rel="noreferrer">
                    #{r.pr_url.split("/").pop()}
                  </a>
                ) : (
                  "–"
                )}
              </TableCell>
            </TableRow>
          ))}
          {rows.length === 0 && (
            <TableRow>
              <TableCell colSpan={8} className="text-muted-foreground">
                No incidents yet.
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </section>
  );
}
