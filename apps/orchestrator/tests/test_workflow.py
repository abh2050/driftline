"""Workflow routing with fake dependencies: no Azure, no models, no MCP."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from agent_framework import InMemoryCheckpointStorage, WorkflowContext, executor

from orchestrator import workflow as wf
from orchestrator.models import ConfigChange, HumanResponse, IncidentRun, RcaResult

INCIDENT = {
    "id": "inc-test",
    "breached_metrics": ["groundedness"],
    "metric_deltas": {"groundedness": {"baseline": 4.5, "current": 3.1}},
    "window_count": 3,
    "config_diff": {"top_k": [5, 1]},
    "recent_deploys": [],
}


class FakeRouter:
    def __init__(self, choices: dict[str, str], low: set[str] | None = None) -> None:
        self.choices, self.low = choices, low or set()

    async def decide(self, dtype: str, context: dict[str, Any], incident_id: str) -> dict[str, Any]:
        top = 0.4 if dtype in self.low else 0.9
        chosen = self.choices[dtype]
        scores = {chosen: top, "other": 1 - top}
        return {
            "chosen": chosen,
            "scores": scores,
            "margin": abs(2 * top - 1),
            "escalated": dtype in self.low,
            "cost_usd": 0.001,
        }


class FakeStore:
    def __init__(self) -> None:
        self.docs = {"inc-test": {"id": "inc-test", "pk": "incident"}}

    async def read_item(self, item: str, partition_key: str) -> dict[str, Any]:
        return dict(self.docs[item])

    async def upsert_item(self, doc: dict[str, Any]) -> None:
        self.docs[doc["id"]] = doc


class FakeAudit:
    def __init__(self) -> None:
        self.steps: list[str] = []

    async def write(self, incident: str, step: str, actor: str, outcome: str, data: Any) -> None:
        self.steps.append(step)


class FakePublisher:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.sent: list[dict[str, Any]] = []

    async def publish(self, topic: str, data: dict[str, Any]) -> None:
        self.sent.append(data)
        fut = wf.deps.validations[data["run_id"]]
        asyncio.get_running_loop().call_soon(fut.set_result, {**self.result, "run_id": "x"})


class FakeFactory:
    def identity(self, name: str) -> str:
        return f"agent-{name}"


def rca(changes: dict[str, str], runbook: list[str] | None = None) -> RcaResult:
    return RcaResult(
        root_cause="top_k was lowered",
        evidence=[],
        config_changes=[ConfigChange(key=k, value=v) for k, v in changes.items()],
        runbook=runbook or [],
        confidence=0.8,
    )


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch) -> Any:
    def configure(
        choices: dict[str, str],
        result: RcaResult,
        validation: dict[str, Any] | None = None,
        low: set[str] | None = None,
    ) -> tuple[FakeStore, FakeAudit, FakePublisher]:
        store, audit = FakeStore(), FakeAudit()
        pub = FakePublisher(validation or {"gate": {"passed": True}})
        wf.deps.router = FakeRouter(choices, low)  # type: ignore[assignment]
        wf.deps.incidents = store  # type: ignore[assignment]
        wf.deps.audit = audit  # type: ignore[assignment]
        wf.deps.publisher = pub  # type: ignore[assignment]
        wf.deps.factory = FakeFactory()  # type: ignore[assignment]
        wf.deps.validations = {}

        async def specialist(run: IncidentRun, s: str) -> tuple[RcaResult, float]:
            return result, 0.01

        async def health() -> dict[str, Any]:
            return {}

        @executor(id="write_pr")
        async def fake_write_pr(
            run: IncidentRun, ctx: WorkflowContext[IncidentRun, IncidentRun]
        ) -> None:
            run.pr_url = "https://github.com/abh2050/driftline/pull/99"
            await wf.save_status(run, "pr_open", pr_url=run.pr_url)
            await ctx.yield_output(run)

        monkeypatch.setattr(wf, "run_specialist", specialist)

        async def running(incident: dict[str, Any]) -> dict[str, Any]:
            return {"top_k": 2}

        monkeypatch.setattr(wf, "agent_health", health)
        monkeypatch.setattr(wf, "running_overrides", running)
        monkeypatch.setattr(wf, "write_pr", fake_write_pr)
        return store, audit, pub

    return configure


async def run(storage: Any = None) -> Any:
    workflow = wf.build_workflow(storage or InMemoryCheckpointStorage(), "inc-test")
    return await workflow.run(IncidentRun(incident=dict(INCIDENT)))


async def test_failed_gate_escalates_to_human(setup: Any) -> None:
    store, _audit, pub = setup(
        {"D2": "high", "D3": "retrieval", "D4": "pr", "D5": "accept"},
        rca({"top_k": "5"}),
        validation={"gate": {"passed": False}},
    )
    result = await run()
    assert pub.sent[0]["changes"] == {"top_k": 5}
    assert pub.sent[0]["running"] == {"top_k": 2}
    assert result.get_request_info_events()
    doc = store.docs["inc-test"]
    assert doc["status"] == "awaiting_human"
    assert doc["pending"]["kind"] == "escalation"
    assert "gate passed=False" in doc["pending"]["reasons"][0]


async def test_low_confidence_escalates_without_validation(setup: Any) -> None:
    store, _, pub = setup(
        {"D2": "high", "D3": "retrieval", "D4": "pr"}, rca({"top_k": "5"}), low={"D3"}
    )
    result = await run()
    assert not pub.sent
    assert result.get_request_info_events()
    assert store.docs["inc-test"]["pending"]["kind"] == "escalation"


async def test_runbook_pauses_then_resumes_from_checkpoint(setup: Any) -> None:
    store, audit, _ = setup(
        {"D2": "medium", "D3": "prompt", "D4": "runbook"},
        rca({}, runbook=["re-enable the indexer"]),
    )
    storage = InMemoryCheckpointStorage()
    result = await run(storage)
    (req,) = result.get_request_info_events()
    assert store.docs["inc-test"]["pending"]["kind"] == "runbook"
    latest = await storage.get_latest(workflow_name="driftline-inc-test")
    assert latest is not None
    resumed = await wf.build_workflow(storage, "inc-test").run(
        responses={req.request_id: HumanResponse(True, "ops@example.com")},
        checkpoint_id=latest.checkpoint_id,
        checkpoint_storage=storage,
    )
    assert resumed.get_outputs()
    doc = store.docs["inc-test"]
    # the prompt specialist has no automated runbook actions: approval is recorded only
    assert doc["status"] == "runbook_approved"
    assert doc["resolution"]["approved"] is True
    assert "human-response" in audit.steps


async def test_validated_fix_opens_pr(setup: Any) -> None:
    store, audit, _pub = setup(
        {"D2": "high", "D3": "retrieval", "D4": "pr", "D5": "accept"}, rca({"top_k": "5"})
    )
    result = await run()
    assert not result.get_request_info_events()
    assert store.docs["inc-test"]["status"] == "pr_open"
    assert audit.steps.count("decision-D5") == 1
    assert "sandbox-request" in audit.steps
