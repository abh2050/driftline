"use client";

import Link from "next/link";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ops } from "@/lib/ops";

const FAULTS: [string, string][] = [
  ["F1", "chunk_size 512 → 2048"],
  ["F2", "embedding deployment changed without reindex"],
  ["F3", "top_k 8 → 2"],
  ["F4", "prompt without citation instruction"],
  ["F5", "indexer disabled while documents change"],
  ["F6", "reranker on a slow tier"],
  ["F7", "chat throttled by an APIM rate limit"],
];

export function DemoControls() {
  const [log, setLog] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  async function run(label: string, path: string, body?: unknown) {
    setBusy(true);
    try {
      const out = await ops<Record<string, unknown>>(path, {
        method: "POST",
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const detail = out.stream ? ` → stream ${String(out.stream)}` : out.fault_id !== undefined ? ` → fault ${String(out.fault_id)}` : "";
      setLog((l) => [`${label}: ok${detail}`, ...l]);
    } catch (e) {
      setLog((l) => [`${label}: ${(e as Error).message}`, ...l]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        <b>Replay</b> pushes a recorded fault trial through ingest and the detector at 1440× speed on its own
        stream; the incident appears on <Link className="underline" href="/incidents">Incidents</Link> within a
        few minutes. <b>Inject live</b> applies the fault to the running patient-rag.
      </p>
      <Table aria-label="Faults">
        <TableHeader>
          <TableRow>
            <TableHead>Fault</TableHead>
            <TableHead>What changes</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {FAULTS.map(([id, text]) => (
            <TableRow key={id}>
              <TableCell className="font-medium">{id}</TableCell>
              <TableCell>{text}</TableCell>
              <TableCell className="flex gap-2">
                <Button size="sm" disabled={busy} onClick={() => run(`Replay ${id}`, "/demo/replay", { fault_id: id, trial: 1 })}>
                  Replay {id}
                </Button>
                <Button size="sm" variant="outline" disabled={busy} onClick={() => run(`Inject ${id}`, "/demo/fault", { fault_id: id })}>
                  Inject {id} live
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Button variant="destructive" disabled={busy} onClick={() => run("Reset", "/demo/reset")}>
        Reset patient-rag to baseline
      </Button>
      <ul aria-label="Demo log" className="space-y-1 font-mono text-xs">
        {log.map((l, i) => (
          <li key={i}>{l}</li>
        ))}
      </ul>
    </div>
  );
}
