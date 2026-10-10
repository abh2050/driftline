"""Token-based chunking (cl100k), paragraph-aware, with overlap."""

from __future__ import annotations

from functools import lru_cache

import tiktoken


@lru_cache(maxsize=1)
def _enc() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    enc = _enc()
    tokens = enc.encode(text)
    if len(tokens) <= size:
        return [text.strip()]
    step = max(1, size - overlap)
    chunks = []
    for start in range(0, len(tokens), step):
        piece = tokens[start : start + size]
        chunks.append(enc.decode(piece).strip())
        if start + size >= len(tokens):
            break
    return [c for c in chunks if c]


def count_tokens(text: str) -> int:
    return len(_enc().encode(text))
