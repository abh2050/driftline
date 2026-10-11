"""Generate the site's hero media with the Higgsfield API (docs/site/assets/).

Credentials come from HF_CREDENTIALS ("<key id>:<secret>") in the environment or .env, and are
only ever sent to api.higgsfield.ai. The generated files are static assets; the page never
calls the API.

uv run python -m scripts.higgsfield_assets [hero|loop|safety ...]
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "site" / "assets"
API = "https://api.higgsfield.ai"
LOG = ASSETS / "higgsfield.json"  # prompts, models and request ids, for provenance

HERO_PROMPT = (
    "Cinematic abstract visualization of a monitoring signal: a single thin luminous electric-"
    "blue line of light streams left to right across a deep navy-black void, runs level, then "
    "sags sharply downward glowing warm red where it breaks, then rises back to level in clean "
    "blue. Fine glowing particles and faint grid lines recede into depth, soft volumetric haze, "
    "shallow depth of field, premium technology launch aesthetic, minimal, high contrast, "
    "lots of empty dark space on the left for a headline, no text, no letters, no logos"
)
LOOP_PROMPT = (
    "Slow, elegant camera drift. The luminous line of light flows continuously left to right, "
    "particles drift gently, the red break pulses softly then heals into blue. Subtle, smooth, "
    "seamless ambient motion, no cuts, no text"
)
SAFETY_PROMPT = (
    "Abstract premium tech visual: four translucent layered glass panels in a row, each "
    "glowing faint blue, a single red spark trying to pass through and being stopped at the "
    "panels, deep navy-black background, soft volumetric light, minimal, no text, no letters"
)


def credentials() -> str:
    value = os.environ.get("HF_CREDENTIALS")
    if not value:
        env = ROOT / ".env"
        for line in env.read_text().splitlines() if env.exists() else []:
            if line.startswith("HF_CREDENTIALS="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not value or value.count(":") != 1:
        sys.exit("HF_CREDENTIALS (<key id>:<secret>) is not set")
    return value


def call(method: str, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    # the credential only ever goes to the Higgsfield API host, even for URLs from responses
    if not url.startswith(f"{API}/"):
        sys.exit(f"refusing to send credentials to {url}")
    headers = {"Authorization": f"Key {credentials()}", "Content-Type": "application/json"}
    if body is not None:
        headers["Idempotency-Key"] = str(uuid.uuid4())
    req = urllib.request.Request(  # noqa: S310 - https host checked above
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 - fixed https host
        data: dict[str, Any] = json.loads(resp.read())
        return data


def generate(model: str, body: dict[str, Any], timeout_s: int = 900) -> dict[str, Any]:
    submitted = call("POST", f"{API}/{model}", body)
    status_url = submitted["status_url"]
    print(f"{model}: request {submitted['request_id']} queued", flush=True)
    deadline, wait = time.time() + timeout_s, 3.0
    while time.time() < deadline:
        time.sleep(wait)
        wait = min(wait * 1.4, 15)
        res = call("GET", status_url)
        if res["status"] in ("completed", "failed", "nsfw", "canceled"):
            if res["status"] != "completed":
                sys.exit(f"{model}: {res['status']}: {json.dumps(res)[:300]}")
            res["request_id"] = submitted["request_id"]
            return res
    sys.exit(f"{model}: timed out")


def output_url(res: dict[str, Any]) -> str:
    for key in ("video", "images", "videos", "outputs"):
        item = res.get(key)
        if isinstance(item, list) and item:
            item = item[0]
        if isinstance(item, dict) and item.get("url"):
            return str(item["url"])
    sys.exit(f"no output url in {json.dumps(res)[:300]}")


def download(url: str, name: str) -> Path:
    path = ASSETS / name
    urllib.request.urlretrieve(url, path)  # noqa: S310 - Higgsfield CDN url from the API
    print(f"  saved {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KB)")
    return path


def record(entry: dict[str, Any]) -> None:
    log = json.loads(LOG.read_text()) if LOG.exists() else {}
    log[entry["asset"]] = entry
    LOG.write_text(json.dumps(log, indent=1) + "\n")


def main(which: list[str]) -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    hero_url = None
    if "hero" in which:
        model = "higgsfield-ai/soul/standard"
        size = {"aspect_ratio": "16:9", "resolution": "1080p"}
        res = generate(model, {"prompt": HERO_PROMPT, **size})
        hero_url = output_url(res)
        download(hero_url, "hero.jpg")
        record(
            {
                "asset": "hero.jpg",
                "model": model,
                "prompt": HERO_PROMPT,
                "request_id": res["request_id"],
                "source_url": hero_url,
            }
        )
    if "loop" in which:
        if hero_url is None:
            hero_url = json.loads(LOG.read_text())["hero.jpg"]["source_url"]
        model = "kling-video/v2.5-turbo/pro/image-to-video"
        res = generate(
            model,
            {
                "prompt": LOOP_PROMPT,
                "image_url": hero_url,
                "duration": 5,
                "negative_prompt": "text, letters, logos, watermark",
            },
        )
        download(output_url(res), "hero-loop.mp4")
        record(
            {
                "asset": "hero-loop.mp4",
                "model": model,
                "prompt": LOOP_PROMPT,
                "request_id": res["request_id"],
            }
        )
    if "safety" in which:
        model = "higgsfield-ai/soul/standard"
        size = {"aspect_ratio": "16:9", "resolution": "1080p"}
        res = generate(model, {"prompt": SAFETY_PROMPT, **size})
        download(output_url(res), "safety.jpg")
        record(
            {
                "asset": "safety.jpg",
                "model": model,
                "prompt": SAFETY_PROMPT,
                "request_id": res["request_id"],
            }
        )


if __name__ == "__main__":
    main(sys.argv[1:] or ["hero", "loop", "safety"])
