# ADR 0007: Full incident workflow, sandbox validation and the human gate (Phase 7)

- Status: accepted
- Date: 2026-10-10
- Details or deviates from: SPEC 4.2, 5.1.7, 5.1.8, 5.1.12, fault F7

## 1. Workflow shape

The orchestrator runs one Agent Framework workflow per incident, named `driftline-<incident id>` and checkpointed in Cosmos (`apps/orchestrator/src/orchestrator/workflow.py`):

```
severity (D2) -> route (D3) -> investigate -> decide_action (D4)
   pr       -> validate (sandbox-runner, then D5) -> write_pr (D6 before every write)
   runbook  -> human_gate
   escalate -> human_gate
```

- Any decision whose top score is below `escalation_threshold` adds an escalation reason, and D4's choice is then overridden to `escalate`. D4 choosing `pr` when the specialist proposed no config change is also an escalation.
- Dual routing: when D3's margin is below `dual_route_margin`, the top two specialists investigate concurrently, and the RCA with the higher confidence is used. Both RCAs are kept on the incident.
- A fix is accepted only if D5 says `accept` **and** the layer 1 gate passed. Either one alone is not enough. A rejected or timed-out validation, or a write that D6 refuses, goes to the human gate as an escalation.

## 2. The human gate pauses the workflow

`HumanGate` calls `request_info(HumanRequest, HumanResponse)`. The run stops, and its state is in the last Cosmos checkpoint. The orchestrator stores the pending request id on the incident (`status: awaiting_human`). An answer, either an `approvals` event (from ops-api in Phase 8) or `POST /incidents/{id}/respond`, rebuilds the workflow and calls `run(responses=..., checkpoint_id=<latest>)`. An approved runbook runs only the automated actions listed in `RUNBOOK_ACTIONS` through patient-rag's admin API: for the index specialist, re-enable the indexer and then reindex. For that, the orchestrator identity also holds `patientrag.admin`. Every other runbook is recorded as approved for an operator to carry out.

## 3. sandbox-runner validates the running config plus the fix

- It is a Container Apps job with an event trigger: a KEDA `azure-servicebus` rule on topic `sandbox`, subscription `sandbox-runner`, authenticated with the job's managed identity. Each execution handles one request. Jobs cannot use Dapr, so the job receives from Service Bus and publishes to `validations` with the SDK (as replay does). The orchestrator holds an in-memory future per validation run and gives up after 40 minutes. If the orchestrator restarts mid-validation, the result is lost and the incident escalates (known limitation).
- **Candidate config = the config patient-rag was running when the incident opened + the specialist's changes.** The orchestrator looks up the incident's `config_hash` in patient-rag's `/configs` registry (the same lookup the detector uses) and sends the settings that differ from baseline as `running`. Applying the changes to the repository config instead would make almost any change pass, because the repository holds the healthy values (ADR 0006 section 5).
- The run uses a fixed, stratified 40-question subset of the golden set (30 standard, 10 fresh, seeded). It is compared with the baseline fixture on the same questions, using the paired, noise-aware gate from layer 1: a regression must exceed both its threshold and two standard errors of the per-question difference.
- When the candidate's index settings differ from the baseline index, the job builds `sbx-<run id>` and deletes it afterwards. That is the only index name the code will create. The identity needs Search Service Contributor, because index management is a control-plane operation.
- Full results, with per-question rows, go to the `eval-results` container. The summary without rows goes on the `validations` topic and into the PR body.

## 4. Prompt shields on tool results

Every specialist agent runs with function middleware (`@function_middleware`) that sends each tool result to Content Safety prompt shields. If an attack is detected, or the service is unavailable (fail closed), the agent sees a placeholder instead of the result, and the event is audited. Shields detected only 2 of 20 injections embedded in the red-team set. The real controls are D6 before every write and the APIM policy (ADR 0006); the shield is defence in depth. Layer 3 evals measure all three.

## 5. F7 uses a real rate-limited gateway, as runtime state

APIM API `chat-gateway` passes requests through to Foundry with `rate-limit calls=2 renewal-period=60` (`infra/apim/policies/chat-gateway.xml`). patient-rag keeps its own Entra token for Foundry, which APIM checks for this tenant and audience `https://ai.azure.com`, then forwards unchanged.

At first, the route was a key in `rag.yaml` (`chat_route`), and F7 overrode it. The first F7 run showed why that was wrong: the incident carried a config diff `chat_route: direct -> gateway`, so the performance specialist reasonably proposed reverting it, and D4 chose a fix rather than escalation. Real throttling has no config diff, and the synthetic routing set already models `chat_throttled` with none. The route is now runtime state in patient-rag, which F7 sets through `state: {chat_route: gateway}` and `/admin/reset` clears, in the same way F5 sets the indexer state. It is no longer part of the config or its hash, so the synthetic telemetry was regenerated. The evidence is now 429s, higher error rate and higher latency, and the expected action is to escalate (raise the limit or the deployment capacity, which is outside the repository).

## 6. Fixes found in the live acceptance runs

- **D3 confused agent health with service health.** The D3 context carried `agent_health`, the latency of Driftline's own specialists (the retrieval agent's p95 is about 30 s, because it calls tools). The router read it as a patient-rag symptom and sent F5 and F7 to the retrieval specialist. The key is now `investigator_agent_health`. The D3 rubric says it describes the investigators, and it states the signal for each specialist (canary drop without a config change goes to index; 429s, errors and latency go to performance). The layer 2a eval renames the dataset key in the same way, so the benchmark and production send the same context.
- **D6 needs the files a PR changes.** Asked about `create_pull_request` with only the branch and title, D6 answered `needs_human` ("the evidence lacks file paths"). Every D6 check now includes `files_changed`, computed by the patch code, not the model.
- **The detector lost the config diff for gradual faults.** Every block that was not yet breaching moved the stream's baseline config hash to the config then running. F1 and F6 take a few blocks to push CUSUM over its threshold, and a D1 "noise" verdict resets CUSUM. Either way, by the time of the incident the faulted config had become the "baseline", the diff was empty, and the specialists guessed. The detector now keeps a log of config changes per stream. An incident's baseline is the config in effect `config_lookback_minutes` (180) before its breach run began. A test checks every config fault and trial against the pre-fault hash. With the old logic, the F6 trials fail it.
- **Incidents carry the evidence a person would look at first.** Windows now keep non-200 status counts and the p95 latency of each stage (retrieval, LLM). Incidents carry `error_status_counts` over the breach run, and `stage_latency_p95_ms` before and during it. Specialists and D3/D4 receive both. Before this, F7's evidence was only "error rate up", so its RCA hedged between throttling and cold starts. Its 429s and its slower LLM stage now point straight at throttling. F6's slower retrieval stage points at the reranker.
- **Agents could read the answers.** An F1 specialist cited `apps/patient-rag/faults/F1.yaml` as evidence. The fault definitions, eval answer keys (`evals/`), synthetic generators (`synth/`, `data/`), design docs (`docs/`) and scripts were all readable through `get_file_contents`, and `search_code` could find them. The RCA results from Phase 6 and earlier Phase 7 runs are therefore not clean evidence. The APIM policy now refuses to let any agent read those paths, and `search_code` is no longer offered. Probe `read-faults` (agent-retrieval) gets HTTP 403 from APIM and is audited. Remaining exposure: `get_commit` can show a diff that touches those paths. Specialists use commits to correlate deploys, so it stays allowed. The layer 3 evals will measure it.
- **D3 and D4 rubrics.** D4 sent a live bad value that differs from the repository to `runbook`, and sent provider throttling to `runbook`. D3 sent a slow reranker to retrieval. The rubrics now state that a deviation from the repository is fixed with `pr` (pin and redeploy), that throttling, quotas and capacity are `escalate` even when mitigations exist, and that "latency broke, quality held" belongs to performance. On the same 928 benchmark cases (first 400 real cases, gpt-5-mini), D3 accuracy went from 0.739 to 0.903 and D4 from 0.884 to 0.927. D4 also states that restoring chunking settings is a PR, because the service queries the index that matches its chunk settings; without that, F1 went to a runbook "to reindex". The full benchmark was re-run with these rubrics on 2026-10-11 (`evals/reports/2026-10-11-router.md`): over all 1,200 cases, gpt-5-mini routes at 89.3% (from 73.1%), now ahead of the rules floor at 87.8%, and D4 is at 91.0% (from 85.0%).
- **File-qualified config keys.** An F4 specialist returned the key `apps/patient-rag/config/rag.yaml:prompt_version`. The sandbox's config validation rejected it and the gate refused the fix, which is safe but wrong. `ConfigChange` now reduces keys to the bare name. The patch code still rejects any key that is not in `rag.yaml`.
- **Service Bus lock expiry.** A validation takes 5 to 8 minutes, longer than the 5-minute message lock. The completion failed, the execution was marked Failed after its result had been published, and the request was delivered again. The job now renews the lock (`AutoLockRenewer`) for the whole validation.
- **Agent Framework middleware classification.** A middleware function whose annotations are strings (`from __future__ import annotations`) cannot be classified and fails at agent construction. The shield uses `@function_middleware`, and a test covers it.
- The detector reopened an incident on the F5 trial 2 stream after an event-time recovery. It also raised one unlabelled latency incident during warm-up, which D2 rated sev4. Both are for the layer 3 system evals (detection precision); they do not affect the workflow.
