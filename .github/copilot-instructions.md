# Copilot instructions for Driftline

Driftline is a multi agent system on Azure that detects quality degradation in a RAG application, routes incidents with an LLM router (see docs/adr/0001-scope-change-llm-router.md), and opens pull requests that humans merge. Engineers use it through a web console. The full spec is in docs/SPEC.md. Read it before you suggest structural changes.

Follow these conventions:

1. Use Python 3.12, FastAPI, pydantic v2, and async code for backend services. Code must pass ruff and mypy strict.
2. Use Next.js with the App Router, TypeScript strict, Tailwind, and shadcn/ui for the console. Code must pass ESLint and the TypeScript compiler.
3. Authenticate to Azure with DefaultAzureCredential and managed identity. Never put keys or connection strings in code or config.
4. Route every decision through the router service and the DecisionClient interface. Never call a model directly to choose between fixed options.
5. Send telemetry over OTLP to otel-collector. Start a span for every agent call, decision, tool call, Service Bus message, and console action, and put trace context into message properties.
6. Import schemas, the decision interface, telemetry setup, and the audit writer from packages/driftline-common.
7. Read thresholds, deployment names, and schedules from the config folder or environment variables.
8. Agents may write only to apps/patient-rag/config and apps/patient-rag/prompts. Never add code that merges PRs, pushes to main, or changes branch protection.
9. The console talks only to ops-api. Admin features check the admin role on both the console and ops-api.
10. Synthetic data generators must take a seed and produce identical output for the same seed.
11. Every feature needs a test: pytest for services and Playwright for console pages. Every patient-rag behavior change needs a layer 1 eval run.
12. Azure resources go in infra/modules as Bicep. Do not create Azure resources from application code, except the temporary sbx-* search indexes in sandbox-runner.
