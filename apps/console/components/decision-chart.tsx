"use client";

import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Decision } from "@/lib/types";

const TITLES: Record<string, string> = {
  D1: "Is it real?",
  D2: "Severity",
  D3: "Routing",
  D4: "Action",
  D5: "Accept fix?",
  D6: "Write allowed?",
};

export function DecisionChart({ type, decision }: { type: string; decision: Decision }) {
  const data = Object.entries(decision.scores)
    .map(([option, score]) => ({ option, score }))
    .sort((a, b) => b.score - a.score);
  return (
    <figure aria-label={`${type} scores`} className="rounded-lg border p-3">
      <figcaption className="mb-2 flex items-baseline justify-between text-sm">
        <span className="font-medium">
          {type} · {TITLES[type] ?? ""}
        </span>
        <span className="text-muted-foreground">
          chose <b>{decision.chosen}</b>
          {decision.margin !== null && <> · margin {decision.margin.toFixed(2)}</>}
          {decision.escalated && <> · below threshold</>}
        </span>
      </figcaption>
      <div className="h-32">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ left: 16, right: 16 }}>
            <CartesianGrid horizontal={false} strokeDasharray="3 3" />
            <XAxis type="number" domain={[0, 1]} tickCount={6} />
            <YAxis type="category" dataKey="option" width={90} />
            <Tooltip formatter={(v) => Number(v).toFixed(3)} />
            <Bar dataKey="score" isAnimationActive={false}>
              {data.map((d) => (
                <Cell key={d.option} fill={d.option === decision.chosen ? "var(--chart-1)" : "var(--chart-3)"} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      {decision.rationale && <p className="mt-2 text-xs text-muted-foreground">{decision.rationale}</p>}
    </figure>
  );
}
