"""Seed the baseline index and the F1 (chunk_size 2048) index; skip any that already hold
documents. Runs in the azd postprovision hook, so a fresh environment comes up searchable.

uv run python -m scripts.seed_index [--force]
"""

from __future__ import annotations

import asyncio
import os
import sys

from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient

from patient_rag.config import apply, load_base_config, load_fault
from patient_rag.seed import run
from scripts.smoke import azd_env


async def has_documents(endpoint: str, index: str) -> bool:
    cred = DefaultAzureCredential()
    try:
        async with SearchClient(endpoint, index, cred) as client:
            return int(await client.get_document_count()) > 0
    except Exception:  # missing index
        return False
    finally:
        await cred.close()


async def main(force: bool) -> None:
    env = azd_env()
    for key in ("SEARCH_ENDPOINT", "FOUNDRY_ENDPOINT"):
        os.environ.setdefault(key, env.get(key, ""))
    base = load_base_config()
    for fault in (None, "F1"):
        cfg = apply(base, load_fault(fault).overrides) if fault else base
        if not force and await has_documents(os.environ["SEARCH_ENDPOINT"], cfg.index_name()):
            print(f"index {cfg.index_name()}: already seeded")
            continue
        await run(fault, 1)


if __name__ == "__main__":
    asyncio.run(main("--force" in sys.argv))
