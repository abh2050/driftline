"""Run every generator in dependency order: uv run python -m synth [--seed N].

With SYNTH_OFFLINE=1 everything is served from synth/cache (used by CI to prove that
the checked-in outputs are reproducible).
"""

from __future__ import annotations

import argparse
import asyncio

from synth import corpus, golden, redteam, routing, telemetry, traffic
from synth.common import CachedLLM, aclose, load_synth_config


async def run_all(seed: int) -> None:
    cfg = load_synth_config()
    llm = CachedLLM(cfg.deployment, cfg.concurrency)
    try:
        print("corpus:", await corpus.generate(seed, llm))
        print("golden:", await golden.generate(seed, llm))
        print("traffic:", await traffic.generate(seed, llm))
        print("telemetry:", telemetry.generate(seed))
        print("routing:", await routing.generate(seed, llm))
        print("redteam:", await redteam.generate(seed, llm))
        print(f"llm calls {llm.calls}, cache hits {llm.hits}")
    finally:
        await aclose(llm)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=load_synth_config().seed)
    asyncio.run(run_all(ap.parse_args().seed))


if __name__ == "__main__":
    main()
