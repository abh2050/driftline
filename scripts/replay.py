"""Start a replay job execution for a plan (operator/eval helper).

uv run python -m scripts.replay fault:F3:1 --run-id p7f3 [--speed 1440]

`az containerapp job start --env-vars` replaces the container's environment, so this reads
the job's own settings and passes them back with the plan, run id and speed overridden.
"""

from __future__ import annotations

import argparse
import json
import subprocess

from scripts.smoke import azd_env


def az(*args: str) -> str:
    return subprocess.run(["az", *args], check=True, capture_output=True, text=True).stdout


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("plan")
    p.add_argument("--run-id", required=True)
    p.add_argument("--speed", default="1440")
    a = p.parse_args()
    rg = azd_env()["AZURE_RESOURCE_GROUP"]
    container = json.loads(
        az(
            "containerapp",
            "job",
            "show",
            "-g",
            rg,
            "-n",
            "replay",
            "--query",
            "properties.template.containers[0]",
            "-o",
            "json",
        )
    )
    env = {e["name"]: e["value"] for e in container["env"] if "value" in e}
    env.update(REPLAY_PLAN=a.plan, REPLAY_RUN_ID=a.run_id, REPLAY_SPEED=a.speed)
    out = az(
        "containerapp",
        "job",
        "start",
        "-g",
        rg,
        "-n",
        "replay",
        "--image",
        container["image"],
        "--env-vars",
        *[f"{k}={v}" for k, v in env.items()],
        "-o",
        "json",
    )
    print(json.dumps({"execution": json.loads(out)["name"], "stream": f"replay-{a.run_id}"}))


if __name__ == "__main__":
    main()
