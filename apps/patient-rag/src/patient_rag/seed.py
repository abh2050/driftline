"""Seed (or rebuild) search indexes from the synthetic corpus.

Usage:
  uv run python -m patient_rag.seed                     # baseline config, source v1
  uv run python -m patient_rag.seed --fault F1          # index for a fault's chunk settings
  uv run python -m patient_rag.seed --version 2         # reindex from source v2 (F5 runbook)
"""

from __future__ import annotations

import argparse
import asyncio

from patient_rag.clients import Clients
from patient_rag.config import apply, load_base_config, load_fault
from patient_rag.index import build_index


async def run(fault: str | None, version: int) -> None:
    cfg = load_base_config()
    if fault:
        cfg = apply(cfg, load_fault(fault).overrides)
    clients = Clients()
    try:
        stats = await build_index(clients, cfg, version=version)
        print(f"index {cfg.index_name()} (source v{version}): {stats}")
    finally:
        await clients.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fault", default=None)
    ap.add_argument("--version", type=int, default=1)
    args = ap.parse_args()
    asyncio.run(run(args.fault, args.version))


if __name__ == "__main__":
    main()
