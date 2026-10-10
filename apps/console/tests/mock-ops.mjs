// Mocked ops-api for Playwright (spec 10.2): fixed fixtures, an SSE feed and a streamed chat.
import http from "node:http";

const port = Number(process.env.MOCK_OPS_PORT ?? 3199);
const now = "2026-10-10T20:00:00Z";
const summary = (id, extra = {}) => ({
  id,
  status: "pr_open",
  severity: "sev1",
  stream: "replay-demo",
  breached_metrics: ["hit_count"],
  opened_at: now,
  detected_at: now,
  closed_at: null,
  specialist: "retrieval",
  action: "pr",
  pr_url: "https://github.com/abh2050/driftline/pull/7",
  awaiting: null,
  fault_label: "F3",
  updated: 1,
  ...extra,
});
const decision = (chosen, scores) => ({ chosen, scores, margin: 0.8, escalated: false, rationale: `chose ${chosen}`, client: "gpt-5-mini", cost_usd: 0.0005, latency_ms: 2500 });
const detail = {
  id: "inc-123",
  summary: summary("inc-123"),
  metric_deltas: { hit_count: { baseline: 8, current: 2, delta_pct: -75 } },
  config_diff: [{ key: "top_k", from: 8, to: 2 }],
  decisions: {
    D2: decision("sev1", { sev1: 0.9, sev2: 0.07, sev3: 0.02, sev4: 0.01 }),
    D3: decision("retrieval", { retrieval: 0.94, prompt: 0.02, index: 0.02, performance: 0.02 }),
    D4: decision("pr", { pr: 0.95, runbook: 0.04, escalate: 0.01 }),
    D5: decision("accept", { accept: 0.95, reject: 0.05 }),
    D6: decision("allow", { allow: 0.95, needs_human: 0.04, deny: 0.01 }),
  },
  specialists: ["retrieval"],
  specialist: "retrieval",
  rca: {
    root_cause: "top_k was lowered from 8 to 2",
    evidence: [{ source: "config_diff", detail: "top_k 8 -> 2" }],
    config_changes: [{ key: "top_k", value: "8" }],
    runbook: [],
    confidence: 0.9,
  },
  action: "pr",
  escalations: [],
  validation: {
    questions: 40,
    target_metric: "recall_at_k",
    baseline: { recall_at_k: 0.95, groundedness: 3.95 },
    candidate: { recall_at_k: 0.95, groundedness: 4.02 },
    gate: { passed: true },
  },
  costs: { tokens_usd: 0.012, decisions_usd: 0.003 },
  resolution: null,
  pr_url: "https://github.com/abh2050/driftline/pull/7",
  trace_url: "https://portal.azure.com/#trace",
};
const approvals = [
  { incident_id: "inc-f5", kind: "runbook", summary: "The indexer is stopped", runbook: ["re-enable the indexer", "reindex"], reasons: [], severity: "sev1", specialist: "index", detected_at: now },
];
const bins = Array.from({ length: 10 }, (_, i) => ({ bin: i, lo: i / 10, hi: (i + 1) / 10, confidence: (i + 0.5) / 10, accuracy: Math.min(1, (i + 0.5) / 10 + 0.05), count: i > 4 ? 10 : 0 }));
const metrics = { n: 1200, accuracy: 0.9, accuracy_lenient: 0.95, macro_f1: 0.88, ece: 0.05, flip_rate: 0.1, cost_per_1k_usd: 0.7, escalation_rate: 0.01, latency_model_ms: { p50: 3000, p95: 4200, p99: 5100 }, reliability: bins };
const report = { report: "2026-10-10-router.json", generated: now, escalation_threshold: 0.6, clients: { "gpt-5-mini": { D3: metrics, D4: metrics }, rules: { D3: metrics, D4: metrics } } };

const json = (res, body, status = 200) => {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
};

http
  .createServer((req, res) => {
    const url = new URL(req.url, "http://x");
    const p = url.pathname;
    if (p === "/healthz") return json(res, { status: "ok" });
    if (p === "/me") return json(res, { name: "admin@example.com", roles: ["admin"], admin: true });
    if (p === "/incidents") return json(res, [summary("inc-123"), summary("inc-older", { severity: "sev3", status: "closed", detected_at: "2026-10-09T10:00:00Z" })]);
    if (p === "/incidents/inc-123") return json(res, detail);
    if (p.startsWith("/incidents/") && p.endsWith("/respond")) return json(res, { approved: true, by: "admin@example.com" });
    if (p === "/approvals") return json(res, approvals);
    if (p === "/router/report") return json(res, report);
    if (p === "/demo/replay") return json(res, { stream: "replay-demo-f3-abc123", execution: "replay-xyz" });
    if (p === "/demo/fault") return json(res, { fault_id: "F3" });
    if (p === "/demo/reset") return json(res, { fault_id: null });
    if (p === "/v1/traces") return json(res, {});
    if (p === "/stream") {
      res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache" });
      res.write("retry: 3000\n\n");
      const t = setTimeout(() => {
        const inc = summary("inc-live", { status: "routing", action: null, pr_url: null, detected_at: "2026-10-10T21:00:00Z" });
        res.write(`event: incident\ndata: ${JSON.stringify({ type: "incident", incident: inc })}\n\n`);
      }, 500);
      req.on("close", () => clearTimeout(t));
      return;
    }
    if (p === "/chat") {
      res.writeHead(200, { "content-type": "text/plain; charset=utf-8" });
      const parts = ["The incident was routed ", "to retrieval because ", "top_k changed."];
      let i = 0;
      const t = setInterval(() => {
        if (i < parts.length) res.write(parts[i++]);
        else {
          clearInterval(t);
          res.end();
        }
      }, 100);
      return;
    }
    json(res, { detail: "not found" }, 404);
  })
  .listen(port, () => console.log(`mock ops-api on ${port}`));
