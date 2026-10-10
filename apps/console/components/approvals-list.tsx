"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { SeverityBadge } from "@/components/badges";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ops } from "@/lib/ops";
import type { Approval, Me } from "@/lib/types";

export function ApprovalsList() {
  const [items, setItems] = useState<Approval[] | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [sent, setSent] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    ops<Approval[]>("/approvals")
      .then(setItems)
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
    ops<Me>("/me").then(setMe).catch(() => setMe(null));
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  async function answer(a: Approval, approved: boolean) {
    setError(null);
    try {
      await ops(`/incidents/${encodeURIComponent(a.incident_id)}/respond`, {
        method: "POST",
        body: JSON.stringify({ approved, note: notes[a.incident_id] ?? "" }),
      });
      setSent((s) => ({ ...s, [a.incident_id]: approved ? "approved" : "rejected" }));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (!items) return <p className="text-muted-foreground">{error ?? "Loading…"}</p>;
  return (
    <div className="space-y-4">
      {error && <p className="text-destructive">{error}</p>}
      {items.length === 0 && <p className="text-muted-foreground">Nothing is waiting for a person.</p>}
      {items.map((a) => (
        <Card key={a.incident_id} data-testid="approval">
          <CardHeader>
            <CardTitle className="flex items-center gap-3 text-base">
              <span className="capitalize">{a.kind}</span>
              <SeverityBadge severity={a.severity} />
              <Link className="font-mono text-xs underline" href={`/incidents/${a.incident_id}`}>
                {a.incident_id}
              </Link>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <p>{a.summary}</p>
            {a.runbook.length > 0 && (
              <ol className="list-decimal pl-5">
                {a.runbook.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ol>
            )}
            {a.reasons.length > 0 && (
              <ul className="list-disc pl-5 text-muted-foreground">
                {a.reasons.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            )}
            {sent[a.incident_id] ? (
              <p role="status">Sent: {sent[a.incident_id]}. The workflow resumes shortly.</p>
            ) : me?.admin ? (
              <div className="flex flex-wrap items-center gap-2">
                <input
                  aria-label={`Note for ${a.incident_id}`}
                  className="h-8 min-w-64 rounded-md border px-2"
                  placeholder="Note (optional)"
                  value={notes[a.incident_id] ?? ""}
                  onChange={(e) => setNotes((n) => ({ ...n, [a.incident_id]: e.target.value }))}
                />
                <Button onClick={() => answer(a, true)}>
                  {a.kind === "runbook" ? "Approve runbook" : "Acknowledge escalation"}
                </Button>
                <Button variant="outline" onClick={() => answer(a, false)}>
                  Reject
                </Button>
              </div>
            ) : (
              <p className="text-muted-foreground">Only admins can answer.</p>
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
