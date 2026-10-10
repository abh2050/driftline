export type IncidentSummary = {
  id: string;
  status: string;
  severity: string | null;
  stream: string;
  breached_metrics: string[];
  opened_at: string | null;
  detected_at: string | null;
  closed_at: string | null;
  specialist: string | null;
  action: string | null;
  pr_url: string | null;
  awaiting: string | null;
  fault_label: string | null;
  updated: number | null;
};

export type Decision = {
  chosen: string;
  scores: Record<string, number>;
  margin: number | null;
  escalated: boolean;
  rationale: string | null;
  client: string | null;
  cost_usd: number | null;
  latency_ms: number | null;
};

export type Evidence = { source: string; detail: string };

export type Rca = {
  root_cause: string;
  evidence: Evidence[];
  config_changes: { key: string; value: string }[];
  runbook: string[];
  confidence: number;
};

export type Validation = {
  run_id?: string;
  questions?: number;
  target_metric?: string;
  baseline?: Record<string, number>;
  candidate?: Record<string, number>;
  gate?: { passed?: boolean; recovered?: boolean; regressions?: Record<string, number> };
  error?: string;
};

export type IncidentDetail = {
  id: string;
  summary: IncidentSummary;
  metric_deltas: Record<string, { baseline: number; current: number; delta_pct: number }>;
  config_diff: { key: string; from: unknown; to: unknown }[];
  error_status_counts?: Record<string, number>;
  decisions: Record<string, Decision>;
  specialists: string[];
  specialist: string | null;
  rca: Rca | null;
  action: string | null;
  escalations: string[];
  validation: Validation | null;
  costs: { tokens_usd?: number; decisions_usd?: number };
  resolution: { approved: boolean; by: string; note: string; kind: string } | null;
  pr_url: string | null;
  trace_url: string | null;
};

export type Approval = {
  incident_id: string;
  kind: "runbook" | "escalation";
  summary: string;
  runbook: string[];
  reasons: string[];
  severity: string | null;
  specialist: string | null;
  detected_at: string | null;
};

export type ClientMetrics = {
  n: number;
  accuracy: number;
  accuracy_lenient: number | null;
  macro_f1: number;
  ece: number;
  flip_rate: number | null;
  cost_per_1k_usd: number;
  escalation_rate: number;
  latency_model_ms: { p50: number; p95: number; p99: number };
  reliability: { bin: number; lo: number; hi: number; confidence: number; accuracy: number; count: number }[];
};

export type RouterReport = {
  report: string;
  generated: string;
  escalation_threshold: number;
  clients: Record<string, Record<string, ClientMetrics>>;
};

export type Me = { name: string; roles: string[]; admin: boolean };
