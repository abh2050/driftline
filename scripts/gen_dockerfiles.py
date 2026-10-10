"""Generate the Python service Dockerfiles from one template.

uv's frozen sync needs every workspace member's pyproject.toml, so all images copy the same
list; regenerate after adding a member: uv run python -m scripts.gen_dockerfiles
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEMBERS = [
    "patient-rag",
    "traffic-gen",
    "router",
    "ingest",
    "detector",
    "replay",
    "orchestrator",
    "github-mcp",
    "kql-tool",
    "sandbox-runner",
    "ops-api",
]
# workspace members whose source a service needs besides its own
SOURCE_DEPS = {"detector": ["ingest"], "sandbox-runner": ["patient-rag"]}

# service -> (package, extra runtime COPY lines, env lines, CMD)
SERVICES: dict[str, tuple[str, list[str], list[str], str]] = {
    "patient-rag": (
        "patient-rag",
        [
            "COPY apps/patient-rag/config apps/patient-rag/config",
            "COPY apps/patient-rag/prompts apps/patient-rag/prompts",
            "COPY apps/patient-rag/faults apps/patient-rag/faults",
            "COPY data/corpus data/corpus",
        ],
        [
            "PATIENT_RAG_HOME=/src/apps/patient-rag",
            "CORPUS_DIR=/src/data/corpus",
            "GIT_COMMIT=${GIT_COMMIT}",
        ],
        '["uvicorn", "patient_rag.app:app", "--host", "0.0.0.0", "--port", "8000", '
        '"--no-access-log"]',
    ),
    "traffic-gen": (
        "traffic-gen",
        [
            "COPY evals/datasets/traffic.jsonl evals/datasets/golden.jsonl "
            "evals/datasets/seasonality.json evals/datasets/",
        ],
        ["DATASETS_DIR=/src/evals/datasets"],
        '["traffic-gen"]',
    ),
    "router": (
        "router",
        [],
        [],
        '["uvicorn", "router.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]',
    ),
    "ingest": (
        "ingest",
        [],
        [],
        '["uvicorn", "ingest.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]',
    ),
    "detector": (
        "detector",
        [],
        [],
        '["uvicorn", "detector.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]',
    ),
    "github-mcp": (
        "github-mcp-proxy",
        [],
        [],
        '["uvicorn", "github_mcp_proxy.app:app", "--host", "0.0.0.0", "--port", "8000", '
        '"--no-access-log"]',
    ),
    "kql-tool": (
        "kql-tool",
        ["COPY apps/kql-tool/queries apps/kql-tool/queries"],
        ["KQL_QUERIES_DIR=/src/apps/kql-tool/queries"],
        '["uvicorn", "kql_tool.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]',
    ),
    "orchestrator": (
        "orchestrator",
        [
            "COPY apps/orchestrator/prompts apps/orchestrator/prompts",
            "COPY apps/patient-rag/config apps/patient-rag/config",
            "COPY evals/gates.yaml evals/gates.yaml",
        ],
        ["ORCHESTRATOR_HOME=/src/apps/orchestrator"],
        '["uvicorn", "orchestrator.app:app", "--host", "0.0.0.0", "--port", "8000", '
        '"--no-access-log"]',
    ),
    "sandbox-runner": (
        "sandbox-runner",
        [
            "COPY apps/patient-rag/config apps/patient-rag/config",
            "COPY apps/patient-rag/prompts apps/patient-rag/prompts",
            "COPY apps/patient-rag/faults apps/patient-rag/faults",
            "COPY data/corpus data/corpus",
            "COPY evals/__init__.py evals/gates.yaml evals/",
            "COPY evals/layer1 evals/layer1",
            "COPY evals/datasets/golden.jsonl evals/datasets/golden.jsonl",
            "COPY evals/fixtures/layer1/baseline.jsonl evals/fixtures/layer1/baseline.jsonl",
        ],
        [
            "PATIENT_RAG_HOME=/src/apps/patient-rag",
            "CORPUS_DIR=/src/data/corpus",
            "EVALS_DIR=/src/evals",
            "PYTHONPATH=/src",
            "HOME=/home/app",
        ],
        '["sandbox-runner"]',
    ),
    "ops-api": (
        "ops-api",
        ["COPY evals/reports evals/reports"],
        ["REPORTS_DIR=/src/evals/reports"],
        '["uvicorn", "ops_api.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]',
    ),
    "replay": (
        "replay",
        ["COPY data/telemetry data/telemetry"],
        ["TELEMETRY_DIR=/src/data/telemetry"],
        '["python", "-m", "replay.run"]',
    ),
}


def render(service: str) -> str:
    package, copies, env, cmd = SERVICES[service]
    members = "\n".join(f"COPY apps/{m}/pyproject.toml apps/{m}/pyproject.toml" for m in MEMBERS)
    arg = "ARG GIT_COMMIT=unknown\n" if any("GIT_COMMIT" in e for e in env) else ""
    envs = " \\\n    ".join(
        [
            "PATH=/src/.venv/bin:$PATH",
            "DRIFTLINE_CONFIG_DIR=/src/config",
            "PYTHONUNBUFFERED=1",
            *env,
        ]
    )
    extra = "\n".join(copies)
    sources = "\n".join(
        f"COPY apps/{m}/src apps/{m}/src" for m in [service, *SOURCE_DEPS.get(service, [])]
    )
    return f"""# Generated by scripts/gen_dockerfiles.py. Built from the repo root (uv workspace).
FROM ghcr.io/astral-sh/uv:0.6.14 AS uv

FROM python:3.12-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY packages packages
{members}
{sources}
RUN uv sync --frozen --no-dev --no-editable --package {package}

FROM python:3.12-slim
{arg}RUN useradd --create-home --uid 10001 app
WORKDIR /src
COPY --from=build /src/.venv /src/.venv
COPY config config
{extra}
ENV {envs}
USER app
EXPOSE 8000
CMD {cmd}
"""


def main() -> None:
    for service in SERVICES:
        (ROOT / "apps" / service / "Dockerfile").write_text(render(service))
        print(f"wrote apps/{service}/Dockerfile")


if __name__ == "__main__":
    main()
