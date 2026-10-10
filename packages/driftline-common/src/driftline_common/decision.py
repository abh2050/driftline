"""The DecisionClient interface (spec 4.1) and the D1-D6 decision catalog (spec 4.2).

Every choice between fixed options goes through a DecisionClient, served by the router
service. Clients return calibrated-as-possible probabilities over the options; the router
measures latency and cost in one place and escalates when the top score is low.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, Field, model_validator


class Decision(BaseModel):
    question_id: str
    options: list[str]
    scores: dict[str, float]  # probabilities that sum to 1
    chosen: str
    margin: float  # top score minus second score
    latency_ms: float
    model: str
    input_tokens: int
    cost_usd: float
    # Router additions (not part of the spec's minimum): decision type, client name,
    # escalation flag and a short rationale for the console and PR bodies.
    decision_type: str = ""
    client: str = ""
    escalated: bool = False
    rationale: str = ""
    output_tokens: int = 0

    @model_validator(mode="after")
    def _consistent(self) -> Decision:
        if set(self.scores) != set(self.options):
            raise ValueError("scores must cover exactly the options")
        if self.chosen not in self.options:
            raise ValueError("chosen must be one of the options")
        return self


class DecisionClient(Protocol):
    name: str

    async def decide(
        self,
        question: str,
        options: list[str],
        context: dict[str, Any],
        rubric: str | None = None,
        *,
        question_id: str = "",
        decision_type: str = "",
    ) -> Decision: ...


def normalize(raw: dict[str, Any], options: list[str], floor: float = 1e-4) -> dict[str, float]:
    """Map arbitrary model scores onto the options and normalize to sum to 1.

    Unknown keys are dropped, missing options get a small floor, negatives clip to 0.
    If nothing usable remains, the distribution is uniform.
    """
    lower = {str(k).strip().lower(): v for k, v in raw.items()}
    vals: dict[str, float] = {}
    for o in options:
        try:
            v = float(lower.get(o.lower(), 0.0))
        except (TypeError, ValueError):
            v = 0.0
        vals[o] = max(v, 0.0)
    total = sum(vals.values())
    if total <= 0:
        return {o: round(1 / len(options), 6) for o in options}
    vals = {o: max(v / total, floor) for o, v in vals.items()}
    total = sum(vals.values())
    return {o: round(v / total, 6) for o, v in vals.items()}


def top_two(scores: dict[str, float]) -> tuple[str, float]:
    """(chosen, margin). Ties break by option order (dict order)."""
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    return ranked[0][0], round(ranked[0][1] - second, 6)


@dataclass(frozen=True)
class DecisionSpec:
    type: str
    question: str
    options: tuple[str, ...]
    rubric: str | None = None


CATALOG: dict[str, DecisionSpec] = {
    "D1": DecisionSpec(
        "D1",
        "Is this metric breach a real incident in the RAG application, or noise?",
        ("real", "noise"),
        "Real incidents are sustained (several consecutive windows), move quality, latency "
        "or error metrics materially, and usually coincide with a config change or deploy. "
        "Noise is short, small, or explained by routine events.",
    ),
    "D2": DecisionSpec(
        "D2",
        "What severity is this incident?",
        ("sev1", "sev2", "sev3", "sev4"),
        "sev1: error rate >= 10%, groundedness down >= 40% or retrieval score down >= 50%. "
        "sev2: error rate >= 3%, groundedness down >= 15%, citation rate down >= 50% or p95 "
        "latency doubled. sev3: any quality metric down >= 5% or p95 latency up >= 40%. "
        "sev4: anything smaller.",
    ),
    "D3": DecisionSpec(
        "D3",
        "Which specialist agent should investigate this incident?",
        ("retrieval", "prompt", "index", "performance"),
        "retrieval: chunking, top_k, embeddings, reranking quality. prompt: prompt version, "
        "citation behavior, instruction changes. index: stale content, indexer state, index "
        "schema. performance: latency, throttling, capacity, errors from dependencies. "
        "Agent health is context: prefer the right specialist even if it is slow.",
    ),
    "D4": DecisionSpec(
        "D4",
        "Given the root cause analysis, what action should Driftline take?",
        ("pr", "runbook", "escalate"),
        "pr: the cause is a setting or prompt in the repository and a patch can fix it. "
        "runbook: the cause is runtime state (indexer, data) that an operator must change. "
        "escalate: the cause is outside Driftline's control (capacity, quotas, provider) or "
        "the evidence is too unclear to act.",
    ),
    "D5": DecisionSpec(
        "D5",
        "Should this candidate fix be accepted?",
        ("accept", "reject"),
        "Accept only if the degraded metric recovers to within 2% of baseline, no other "
        "metric regresses beyond its threshold, and the patch only touches "
        "apps/patient-rag/config or apps/patient-rag/prompts and changes the key that "
        "caused the incident.",
    ),
    "D6": DecisionSpec(
        "D6",
        "May the agent perform this write action?",
        ("allow", "needs_human", "deny"),
        "deny: merges, pushes to main, branch protection changes, deletes, reading secrets, "
        "or writes outside apps/patient-rag/config and apps/patient-rag/prompts. "
        "needs_human: anything unusual or not covered. allow: opening a branch or pull "
        "request that writes only inside the allowlist.",
    ),
}


class DecideRequest(BaseModel):
    """Body of POST /decide on the router service."""

    decision_type: str = Field(pattern=r"^D[1-6]$")
    question_id: str
    context: dict[str, Any]
    question: str | None = None
    options: list[str] | None = None
    rubric: str | None = None
    client: str | None = None  # override the active client (benchmarks only)
    incident_id: str | None = None

    def resolved(self) -> tuple[str, list[str], str | None]:
        spec = CATALOG[self.decision_type]
        return (
            self.question or spec.question,
            self.options or list(spec.options),
            self.rubric if self.rubric is not None else spec.rubric,
        )
