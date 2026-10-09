# Driftline

Driftline watches a production RAG application, detects silent quality degradation, routes each incident to a specialist agent through an LLM router, finds the root cause, proves a fix with evals in a sandbox, and opens a pull request that a human merges. Engineers work with it through a web console. Everything runs on Azure and deploys with one `azd up`.

> Work in progress. Spec: [docs/SPEC.md](docs/SPEC.md). Decisions: [docs/adr/](docs/adr/). Steps that need a human: [docs/HUMAN_STEPS.md](docs/HUMAN_STEPS.md).

## Development

```sh
uv sync                                    # Python 3.12 workspace
uv run ruff check . && uv run mypy && uv run pytest
uv run pre-commit install

cd apps/console && npm ci                  # Node 22
npm run lint && npm run typecheck && npm run test:e2e
```
