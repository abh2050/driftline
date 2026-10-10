"""The RAG pipeline: embed -> vector search -> optional rerank -> answer with citations.

Importable as a library so evals (layer 1) and sandbox-runner can build patient-rag in
process with any config, exactly as the service runs it.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizedQuery

from patient_rag.clients import Clients
from patient_rag.config import RagConfig

CITATION = re.compile(r"\[doc:([A-Za-z0-9_.\-]+)\]")


@dataclass
class Hit:
    doc_id: str
    chunk_id: str
    title: str
    content: str
    score: float
    rerank_score: float | None = None


@dataclass
class RagResult:
    answer: str
    hits: list[Hit]
    citations: list[str]
    latency_ms: float
    retrieval_latency_ms: float
    llm_latency_ms: float
    tokens_in: int
    tokens_out: int
    embedding_tokens: int
    reranked: bool
    config_hash: str
    prompt_version: str
    error: str | None = None
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def retrieved_doc_ids(self) -> list[str]:
        seen: list[str] = []
        for h in self.hits:
            if h.doc_id not in seen:
                seen.append(h.doc_id)
        return seen

    @property
    def context(self) -> str:
        return format_context(self.hits)


def format_context(hits: list[Hit]) -> str:
    return "\n\n".join(f"[doc:{h.doc_id}] {h.title}\n{h.content}" for h in hits)


class RagPipeline:
    def __init__(
        self,
        clients: Clients,
        cfg: RagConfig,
        *,
        index_name: str | None = None,
        chat_route: str = "direct",
    ) -> None:
        self.clients = clients
        self.cfg = cfg
        # "gateway" sends chat through the rate-limited APIM API (fault F7); runtime state
        self.chat_route = chat_route
        self.index_name = index_name or cfg.index_name()
        self._search = SearchClient(clients.search_endpoint, self.index_name, clients.credential)

    async def retrieve(self, query: str) -> tuple[list[Hit], int, bool]:
        cfg = self.cfg
        vector = (
            await self.clients.embed([query], cfg.embedding_deployment, cfg.embedding_dimensions)
        )[0]
        k = cfg.rerank_candidates if cfg.reranker_enabled else cfg.top_k
        results = await self._search.search(
            search_text=None,
            vector_queries=[
                VectorizedQuery(vector=vector, k_nearest_neighbors=k, fields="embedding")
            ],
            top=k,
            select=["id", "doc_id", "title", "content"],
        )
        hits = [
            Hit(
                doc_id=r["doc_id"],
                chunk_id=r["id"],
                title=r["title"],
                content=r["content"],
                score=float(r["@search.score"]),
            )
            async for r in results
        ]
        reranked = False
        if cfg.reranker_enabled and hits:
            order = await self.clients.rerank(
                cfg.reranker_deployment, query, [f"{h.title}\n{h.content}" for h in hits], cfg.top_k
            )
            ranked = []
            for idx, score in order:
                h = hits[idx]
                h.rerank_score = score
                ranked.append(h)
            hits = ranked
            reranked = True
        return hits[: cfg.top_k], len(query.split()), reranked

    async def ask(self, query: str) -> RagResult:
        cfg = self.cfg
        t0 = time.perf_counter()
        hits: list[Hit] = []
        reranked = False
        try:
            hits, emb_tokens, reranked = await self.retrieve(query)
            t1 = time.perf_counter()
            user = f"Document excerpts:\n\n{format_context(hits)}\n\nQuestion: {query}"
            chat = await self.clients.chat(
                self.chat_route, cfg.chat_deployment, cfg.prompt(), user, cfg.max_answer_tokens
            )
            t2 = time.perf_counter()
        except Exception as e:
            t_err = time.perf_counter()
            return RagResult(
                answer="",
                hits=hits,
                citations=[],
                latency_ms=(t_err - t0) * 1000,
                retrieval_latency_ms=(t_err - t0) * 1000,
                llm_latency_ms=0.0,
                tokens_in=0,
                tokens_out=0,
                embedding_tokens=0,
                reranked=reranked,
                config_hash=cfg.config_hash(),
                prompt_version=cfg.prompt_version,
                error=f"{type(e).__name__}: {e}",
            )
        retrieved = {h.doc_id for h in hits}
        cited = [c for c in dict.fromkeys(CITATION.findall(chat.text)) if c in retrieved]
        return RagResult(
            answer=chat.text,
            hits=hits,
            citations=cited,
            latency_ms=(t2 - t0) * 1000,
            retrieval_latency_ms=(t1 - t0) * 1000,
            llm_latency_ms=(t2 - t1) * 1000,
            tokens_in=chat.tokens_in,
            tokens_out=chat.tokens_out,
            embedding_tokens=emb_tokens,
            reranked=reranked,
            config_hash=cfg.config_hash(),
            prompt_version=cfg.prompt_version,
        )

    async def close(self) -> None:
        await self._search.close()
