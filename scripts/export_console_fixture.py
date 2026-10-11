"""Export real Driftline data for the console screenshots (docs/site): incidents, one
incident's detail, the approvals queue, the latest router report and an explainer answer,
shaped exactly as ops-api returns them. The console then runs against the mocked ops-api with
this file (MOCK_DATA), because the deployed console needs an interactive Entra sign-in.

uv run python -m scripts.export_console_fixture inc-20261011015035-924ae2
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from azure.cosmos.aio import CosmosClient
from azure.identity.aio import AzureCliCredential
from ops_api import views

from scripts.smoke import azd_env

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "apps" / "console" / "tests" / "fixtures" / "real.json"
EXPLAINER = (ROOT / "apps" / "orchestrator" / "prompts" / "explainer.md").read_text()
QUESTION = "Why was this incident routed to the retrieval specialist, and is the fix safe?"


async def explain(detail: dict[str, Any]) -> str:
    """The explainer's instructions and the incident record, answered by its model."""
    from driftline_common.config import load_agents_config
    from driftline_common.foundry import make_openai_client

    cred = AzureCliCredential()
    client = make_openai_client(credential=cred)
    try:
        resp = await client.chat.completions.create(
            model=load_agents_config().deployment_for("explainer"),
            messages=[
                {"role": "system", "content": EXPLAINER},
                {
                    "role": "user",
                    "content": f"Incident record:\n{json.dumps(detail, default=str)[:15000]}"
                    f"\n\nQuestion: {QUESTION}",
                },
            ],
        )
        return resp.choices[0].message.content or ""
    finally:
        await client.close()
        await cred.close()


async def main(incident_id: str) -> None:
    env = azd_env()
    cred = AzureCliCredential()
    async with CosmosClient(env["COSMOS_ENDPOINT"], credential=cred) as cosmos:
        c = cosmos.get_database_client("driftline").get_container_client("incidents")
        docs = [
            d
            async for d in c.query_items(
                "SELECT * FROM c ORDER BY c.detected_at DESC OFFSET 0 LIMIT 40",
                partition_key="incident",
            )
        ]
        doc = await c.read_item(incident_id, partition_key="incident")
    await cred.close()
    detail = views.detail(doc)
    reports = sorted((ROOT / "evals" / "reports").glob("*-router.json"))
    router = json.loads(reports[-1].read_text())
    router["report"] = reports[-1].name
    data = {
        "incidents": [views.summary(d) for d in docs],
        "detail": detail,
        "approvals": [a for d in docs if (a := views.approval(d))],
        "router": router,
        "chat": {"question": QUESTION, "answer": await explain(detail)},
        "incident_id": incident_id,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1, default=str))
    print(
        f"wrote {OUT.relative_to(ROOT)}: {len(data['incidents'])} incidents, "
        f"{len(data['approvals'])} approvals"
    )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
