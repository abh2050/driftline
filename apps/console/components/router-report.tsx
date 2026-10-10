"use client";

import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ops, pct } from "@/lib/ops";
import type { RouterReport } from "@/lib/types";

const COLORS = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)"];

export function RouterReportView() {
  const [report, setReport] = useState<RouterReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [type, setType] = useState("D3");

  useEffect(() => {
    ops<RouterReport>("/router/report")
      .then(setReport)
      .catch((e: Error) => setError(e.message));
  }, []);

  if (!report) return <p className="text-muted-foreground">{error ?? "Loading…"}</p>;
  const clients = Object.keys(report.clients);
  const types = Array.from(new Set(clients.flatMap((c) => Object.keys(report.clients[c])))).sort();
  // one row per confidence bin, one column per client (bins with no decisions are left out)
  const bins = Array.from({ length: 10 }, (_, i) => {
    const row: Record<string, number | null> = { bin: (i + 0.5) / 10, ideal: (i + 0.5) / 10 };
    for (const c of clients) {
      const b = report.clients[c][type]?.reliability.find((r) => r.bin === i);
      row[c] = b && b.count > 0 ? b.accuracy : null;
    }
    return row;
  });

  return (
    <div className="space-y-6">
      <p className="text-sm text-muted-foreground">
        {report.report} · escalation threshold {report.escalation_threshold}
      </p>
      <div role="tablist" aria-label="Decision type" className="flex gap-2">
        {types.map((t) => (
          <Button
            key={t}
            role="tab"
            aria-selected={t === type}
            variant={t === type ? "default" : "outline"}
            size="sm"
            onClick={() => setType(t)}
          >
            {t}
          </Button>
        ))}
      </div>
      <Table aria-label="Router metrics">
        <TableHeader>
          <TableRow>
            <TableHead>Client</TableHead>
            <TableHead>n</TableHead>
            <TableHead>Accuracy</TableHead>
            <TableHead>Lenient</TableHead>
            <TableHead>Macro F1</TableHead>
            <TableHead>ECE</TableHead>
            <TableHead>Flip rate</TableHead>
            <TableHead>p50 / p95 / p99 ms</TableHead>
            <TableHead>$ / 1k</TableHead>
            <TableHead>Escalated</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {clients
            .filter((c) => report.clients[c][type])
            .map((c) => {
              const m = report.clients[c][type];
              return (
                <TableRow key={c}>
                  <TableCell className="font-medium">{c}</TableCell>
                  <TableCell>{m.n}</TableCell>
                  <TableCell>{pct(m.accuracy)}</TableCell>
                  <TableCell>{pct(m.accuracy_lenient)}</TableCell>
                  <TableCell>{m.macro_f1.toFixed(3)}</TableCell>
                  <TableCell>{m.ece.toFixed(3)}</TableCell>
                  <TableCell>{pct(m.flip_rate)}</TableCell>
                  <TableCell>
                    {m.latency_model_ms.p50.toFixed(0)} / {m.latency_model_ms.p95.toFixed(0)} /{" "}
                    {m.latency_model_ms.p99.toFixed(0)}
                  </TableCell>
                  <TableCell>${m.cost_per_1k_usd.toFixed(3)}</TableCell>
                  <TableCell>{pct(m.escalation_rate)}</TableCell>
                </TableRow>
              );
            })}
        </TableBody>
      </Table>
      <figure aria-label="Reliability diagram" className="rounded-lg border p-3">
        <figcaption className="mb-2 text-sm font-medium">
          Reliability: accuracy by confidence ({type}); the diagonal is perfect calibration
        </figcaption>
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={bins}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="bin" type="number" domain={[0, 1]} tickCount={6} />
              <YAxis domain={[0, 1]} tickCount={6} />
              <Tooltip formatter={(v) => (v === null ? "–" : Number(v).toFixed(3))} />
              <Legend />
              <Line dataKey="ideal" stroke="var(--muted-foreground)" strokeDasharray="4 4" dot={false} />
              {clients.map((c, i) => (
                <Line key={c} dataKey={c} stroke={COLORS[i % COLORS.length]} connectNulls isAnimationActive={false} />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </figure>
    </div>
  );
}
