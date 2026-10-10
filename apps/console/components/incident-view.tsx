"use client";

import { useEffect, useState } from "react";
import { SeverityBadge, StatusBadge } from "@/components/badges";
import { DecisionChart } from "@/components/decision-chart";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ops, usd } from "@/lib/ops";
import type { IncidentDetail } from "@/lib/types";

const EVAL_METRICS = [
  "recall_at_k",
  "mrr",
  "groundedness",
  "relevance",
  "response_completeness",
  "citation_rate",
  "answer_accuracy",
  "latency_p95_ms",
  "cost_per_query_usd",
];
const ROUTING = ["D2", "D3", "D4"];
const GUARDS = ["D5", "D6"];

export function IncidentView({ id }: { id: string }) {
  const [inc, setInc] = useState<IncidentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let stop = false;
    const load = () =>
      ops<IncidentDetail>(`/incidents/${encodeURIComponent(id)}`)
        .then((d) => !stop && setInc(d))
        .catch((e: Error) => !stop && setError(e.message));
    load();
    const timer = setInterval(load, 5000); // the workflow keeps updating the record
    return () => {
      stop = true;
      clearInterval(timer);
    };
  }, [id]);

  if (error && !inc) return <p className="text-destructive">{error}</p>;
  if (!inc) return <p className="text-muted-foreground">Loading…</p>;
  const s = inc.summary;
  const v = inc.validation;
  const totalCost = (inc.costs.tokens_usd ?? 0) + (inc.costs.decisions_usd ?? 0);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <SeverityBadge severity={s.severity} />
        <StatusBadge status={s.status} />
        <span>metrics: {s.breached_metrics.join(", ")}</span>
        <span>specialist: {inc.specialist ?? "–"}</span>
        <span>action: {inc.action ?? "–"}</span>
        {inc.pr_url && (
          <a className="font-medium underline" href={inc.pr_url} target="_blank" rel="noreferrer">
            Pull request #{inc.pr_url.split("/").pop()}
          </a>
        )}
        {inc.trace_url && (
          <a className="underline" href={inc.trace_url} target="_blank" rel="noreferrer">
            Trace in Application Insights
          </a>
        )}
        <span>cost: {usd(totalCost)}</span>
      </div>

      <section aria-label="Routing" className="grid gap-4 md:grid-cols-3">
        {ROUTING.filter((t) => inc.decisions[t]).map((t) => (
          <DecisionChart key={t} type={t} decision={inc.decisions[t]} />
        ))}
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Root cause</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            {inc.rca ? (
              <>
                <p>{inc.rca.root_cause}</p>
                <p className="text-muted-foreground">confidence {inc.rca.confidence.toFixed(2)}</p>
                <ul className="list-disc space-y-1 pl-5">
                  {inc.rca.evidence.map((e, i) => (
                    <li key={i}>
                      <span className="font-mono text-xs">{e.source}</span>: {e.detail}
                    </li>
                  ))}
                </ul>
                {inc.rca.config_changes.length > 0 && (
                  <p>
                    Proposed:{" "}
                    {inc.rca.config_changes.map((c) => (
                      <code key={c.key} className="mr-2">
                        {c.key}: {c.value}
                      </code>
                    ))}
                  </p>
                )}
                {inc.rca.runbook.length > 0 && (
                  <ol className="list-decimal pl-5">
                    {inc.rca.runbook.map((step, i) => (
                      <li key={i}>{step}</li>
                    ))}
                  </ol>
                )}
              </>
            ) : (
              <p className="text-muted-foreground">Investigation in progress.</p>
            )}
            {inc.escalations.length > 0 && (
              <div>
                <p className="font-medium">Escalated because</p>
                <ul className="list-disc pl-5">
                  {inc.escalations.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Sandbox evaluation</CardTitle>
          </CardHeader>
          <CardContent className="text-sm">
            {v ? (
              <>
                <p className="mb-2">
                  {v.questions ?? "?"} golden questions · target <code>{v.target_metric}</code> · gate{" "}
                  <b>{v.gate?.passed ? "passed" : "failed"}</b>
                  {v.error && <span className="text-destructive"> · {v.error}</span>}
                </p>
                <Table aria-label="Eval comparison">
                  <TableHeader>
                    <TableRow>
                      <TableHead>Metric</TableHead>
                      <TableHead>Baseline</TableHead>
                      <TableHead>Candidate</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {EVAL_METRICS.filter((m) => v.baseline?.[m] !== undefined).map((m) => (
                      <TableRow key={m}>
                        <TableCell>{m}</TableCell>
                        <TableCell>{v.baseline?.[m]}</TableCell>
                        <TableCell>{v.candidate?.[m] ?? "–"}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </>
            ) : (
              <p className="text-muted-foreground">No sandbox validation for this incident.</p>
            )}
          </CardContent>
        </Card>
      </div>

      <section aria-label="Guards" className="grid gap-4 md:grid-cols-2">
        {GUARDS.filter((t) => inc.decisions[t]).map((t) => (
          <DecisionChart key={t} type={t} decision={inc.decisions[t]} />
        ))}
      </section>

      {inc.resolution && (
        <p className="text-sm">
          {inc.resolution.kind} {inc.resolution.approved ? "approved" : "rejected"} by {inc.resolution.by}
          {inc.resolution.note && <> — {inc.resolution.note}</>}
        </p>
      )}
    </div>
  );
}
