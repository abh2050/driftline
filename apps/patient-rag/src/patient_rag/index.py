"""Search index schema and (re)indexing of the Cascade Forge Works corpus.

Used by scripts/seed-index.py, by the F5 runbook (reindex from a source version) and by
sandbox-runner for temporary sbx-* indexes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from azure.core.exceptions import ResourceNotFoundError
from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.aio import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)

from patient_rag.chunking import chunk_text
from patient_rag.clients import Clients
from patient_rag.config import RagConfig

REPO_ROOT = Path(__file__).resolve().parents[4]
CORPUS_DIR = Path(os.environ.get("CORPUS_DIR", REPO_ROOT / "data" / "corpus"))


def index_schema(name: str, dimensions: int) -> SearchIndex:
    return SearchIndex(
        name=name,
        fields=[
            SimpleField(name="id", type=SearchFieldDataType.STRING, key=True),
            SimpleField(name="doc_id", type=SearchFieldDataType.STRING, filterable=True),
            SimpleField(name="doc_type", type=SearchFieldDataType.STRING, filterable=True),
            SimpleField(name="revision", type=SearchFieldDataType.STRING),
            SimpleField(name="source_version", type=SearchFieldDataType.INT32, filterable=True),
            SimpleField(name="chunk", type=SearchFieldDataType.INT32),
            SearchableField(name="title", type=SearchFieldDataType.STRING),
            SearchableField(name="content", type=SearchFieldDataType.STRING),
            SearchField(
                name="embedding",
                type="Collection(Edm.Single)",
                searchable=True,
                vector_search_dimensions=dimensions,
                vector_search_profile_name="hnsw",
            ),
        ],
        vector_search=VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="hnsw-alg")],
            profiles=[VectorSearchProfile(name="hnsw", algorithm_configuration_name="hnsw-alg")],
        ),
    )


def load_source(version: int) -> list[dict[str, Any]]:
    """Corpus at a source version: v1 docs, or v1 with the 30 revised docs replaced (v2)."""
    docs = {d["id"]: d for d in _read(CORPUS_DIR / "docs.jsonl")}
    if version >= 2:
        for d in _read(CORPUS_DIR / "docs_v2.jsonl"):
            docs[d["id"]] = d
    return sorted(docs.values(), key=lambda d: d["id"])


def _read(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def chunk_documents(
    docs: Sequence[dict[str, Any]], cfg: RagConfig, version: int
) -> list[dict[str, Any]]:
    out = []
    for d in docs:
        for i, text in enumerate(chunk_text(d["text"], cfg.chunk_size, cfg.chunk_overlap)):
            out.append(
                {
                    "id": f"{d['id']}--{i}".replace(".", "_"),
                    "doc_id": d["id"],
                    "doc_type": d["type"],
                    "revision": d["revision"],
                    "source_version": version,
                    "chunk": i,
                    "title": d["title"],
                    "content": text,
                }
            )
    return out


async def build_index(
    clients: Clients,
    cfg: RagConfig,
    *,
    version: int = 1,
    name: str | None = None,
    batch: int = 64,
) -> dict[str, int]:
    """Create or replace an index for `cfg` from source `version` and upload all chunks."""
    index_name = name or cfg.index_name()
    async with SearchIndexClient(clients.search_endpoint, clients.credential) as ic:
        with contextlib.suppress(ResourceNotFoundError):
            await ic.delete_index(index_name)
        await ic.create_or_update_index(index_schema(index_name, cfg.embedding_dimensions))
    chunks = chunk_documents(load_source(version), cfg, version)
    for start in range(0, len(chunks), batch):
        part = chunks[start : start + batch]
        vectors = await clients.embed(
            [f"{c['title']}\n{c['content']}" for c in part],
            cfg.embedding_deployment,
            cfg.embedding_dimensions,
        )
        for c, v in zip(part, vectors, strict=True):
            c["embedding"] = v
        async with SearchClient(clients.search_endpoint, index_name, clients.credential) as sc:
            await sc.upload_documents(part)
        await asyncio.sleep(0)
    return {"documents": len({c["doc_id"] for c in chunks}), "chunks": len(chunks)}


async def delete_index(clients: Clients, name: str) -> None:
    async with SearchIndexClient(clients.search_endpoint, clients.credential) as ic:
        await ic.delete_index(name)
