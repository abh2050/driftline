"""Seed the baseline index and the F1 (chunk_size 2048) index."""

import asyncio

from patient_rag.seed import run

asyncio.run(run(None, 1))
asyncio.run(run("F1", 1))
