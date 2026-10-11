"""Start a replay job execution for a plan (operator/eval helper).

uv run python -m scripts.replay fault:F3:1 --run-id p7f3 [--speed 1440]

`az containerapp job start --env-vars` replaces the container's environment, so this reads
the job's own settings and passes them back with the plan, run id and speed overridden.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time

from scripts.smoke import azd_env


def az(*args: str, attempts: int = 3) -> str:
    """az without a terminal, retried: long eval runs see occasional transient failures."""
    for i in range(attempts):
        out = subprocess.run(
            ["az", *args, "--only-show-errors"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        if out.returncode == 0:
            return out.stdout
        if i == attempts - 1:
            raise RuntimeError(f"az {' '.join(args[:4])} failed: {out.stderr.strip()[:500]}")
        time.sleep(10 * (i + 1))
    raise AssertionError("unreachable")


def start(plan: str, run_id: str, speed: str = "1440", resource_group: str | None = None) -> str:
    """Start one replay execution; returns the stream it publishes to.

    Uses the ARM start API directly: `az containerapp job start` can block indefinitely when
    run without a terminal."""
    rg = resource_group or azd_env()["AZURE_RESOURCE_GROUP"]
    job = json.loads(az("containerapp", "job", "show", "-g", rg, "-n", "replay", "-o", "json"))
    container = job["properties"]["template"]["containers"][0]
    env = [e for e in container.get("env", []) if e["name"] not in OVERRIDES]
    env += [
        {"name": "REPLAY_PLAN", "value": plan},
        {"name": "REPLAY_RUN_ID", "value": run_id},
        {"name": "REPLAY_SPEED", "value": speed},
    ]
    body = {"containers": [{**container, "env": env}]}
    az(
        "rest",
        "--method",
        "post",
        "--uri",
        f"https://management.azure.com{job['id']}/start?api-version=2025-01-01",
        "--body",
        json.dumps(body),
    )
    return f"replay-{run_id}"


OVERRIDES = {"REPLAY_PLAN", "REPLAY_RUN_ID", "REPLAY_SPEED"}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("plan")
    p.add_argument("--run-id", required=True)
    p.add_argument("--speed", default="1440")
    a = p.parse_args()
    print(json.dumps({"stream": start(a.plan, a.run_id, a.speed)}))


if __name__ == "__main__":
    main()
