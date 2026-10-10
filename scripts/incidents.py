"""Inspect detector state and incidents in Cosmos (operator/eval helper).

uv run python -m scripts.incidents --stream replay-f3t1 [--wait 900]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from typing import Any

from azure.cosmos.aio import CosmosClient
from azure.identity.aio import AzureCliCredential

from scripts.smoke import azd_env


async def snapshot(stream: str) -> dict[str, Any]:
    cred = AzureCliCredential()
    async with CosmosClient(azd_env()["COSMOS_ENDPOINT"], credential=cred) as c:
        db = c.get_database_client("driftline")
        params: list[dict[str, Any]] = [{"name": "@s", "value": stream}]
        windows = [
            x
            async for x in db.get_container_client("windows").query_items(
                "SELECT VALUE COUNT(1) FROM c WHERE c.stream = @s", parameters=params
            )
        ]
        base = [
            x
            async for x in db.get_container_client("baselines").query_items(
                "SELECT c.observations, c.last_window, c.open_incident FROM c WHERE c.stream = @s",
                parameters=params,
            )
        ]
        incs = [
            x
            async for x in db.get_container_client("incidents").query_items(
                "SELECT c.id, c.opened_at, c.detected_at, c.status, c.breached_metrics, "
                "c.fault_label, c.config_diff, c.d1.chosen AS d1, c.d1.scores AS d1_scores "
                "FROM c WHERE c.stream = @s",
                parameters=params,
            )
        ]
    await cred.close()
    return {"windows": windows[0] if windows else 0, "baseline": base, "incidents": incs}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stream", required=True)
    ap.add_argument("--wait", type=int, default=0, help="seconds to wait for an incident")
    args = ap.parse_args()
    deadline = time.monotonic() + args.wait
    while True:
        snap = asyncio.run(snapshot(args.stream))
        if snap["incidents"] or time.monotonic() >= deadline:
            break
        time.sleep(30)
    print(json.dumps(snap, indent=1, default=str))


if __name__ == "__main__":
    main()
