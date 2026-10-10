"""Shared helpers: seeded RNG streams, a disk-cached LLM, deterministic writers."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import io
import json
import os
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = REPO_ROOT / "synth" / "cache"
DATA_DIR = REPO_ROOT / "data"
DATASETS_DIR = REPO_ROOT / "evals" / "datasets"
OFFLINE_ENV = "SYNTH_OFFLINE"

Message = Mapping[str, str]


@dataclass(frozen=True)
class SynthConfig:
    seed: int
    deployment: str
    concurrency: int


def load_synth_config() -> SynthConfig:
    raw = yaml.safe_load((REPO_ROOT / "config" / "synth.yaml").read_text())
    return SynthConfig(
        seed=int(raw["seed"]),
        deployment=str(raw["deployment"]),
        concurrency=int(raw["concurrency"]),
    )


def rng(seed: int, stream: str) -> random.Random:
    """Independent, reproducible RNG per (seed, stream)."""
    digest = hashlib.sha256(f"{seed}:{stream}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


class LLM(Protocol):
    deployment: str

    async def complete(
        self, messages: Sequence[Message], *, json_mode: bool = False, key: str = ""
    ) -> str: ...


class CacheMissError(RuntimeError):
    pass


def _cache_key(deployment: str, messages: Sequence[Message], json_mode: bool, key: str) -> str:
    payload = json.dumps(
        {"d": deployment, "m": [dict(m) for m in messages], "j": json_mode, "k": key},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class CachedLLM:
    """Chat completion with a content-addressed disk cache.

    Every response is stored in synth/cache, so reruns with the same seed are
    served from disk and produce byte identical outputs. With SYNTH_OFFLINE=1 a
    cache miss raises instead of calling the model (used by CI).
    """

    def __init__(self, deployment: str, concurrency: int, cache_dir: Path = CACHE_DIR) -> None:
        self.deployment = deployment
        self._cache_dir = cache_dir
        self._sem = asyncio.Semaphore(concurrency)
        self._client: Any = None
        self._credential: Any = None
        self.calls = 0
        self.hits = 0
        self.used: set[str] = set()

    def _path(self, digest: str) -> Path:
        return self._cache_dir / digest[:2] / f"{digest}.json"

    async def complete(
        self, messages: Sequence[Message], *, json_mode: bool = False, key: str = ""
    ) -> str:
        digest = _cache_key(self.deployment, messages, json_mode, key)
        self.used.add(digest)
        path = self._path(digest)
        if path.exists():
            self.hits += 1
            return str(json.loads(path.read_text())["content"])
        if os.environ.get(OFFLINE_ENV) == "1":
            raise CacheMissError(f"cache miss in offline mode: {digest}")
        async with self._sem:
            if self._client is None:
                from azure.identity.aio import DefaultAzureCredential

                from driftline_common.foundry import make_openai_client

                self._credential = DefaultAzureCredential()
                self._client = make_openai_client(credential=self._credential)
            kwargs: dict[str, Any] = {}
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            resp = await self._client.chat.completions.create(
                model=self.deployment,
                messages=[dict(m) for m in messages],
                reasoning_effort="low",
                **kwargs,
            )
        self.calls += 1
        content = resp.choices[0].message.content or ""
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "deployment": self.deployment,
            "messages": [dict(m) for m in messages],
            "json_mode": json_mode,
            "key": key,
            "content": content,
        }
        path.write_text(json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        return content


def prune_cache(llm: CachedLLM, cache_dir: Path = CACHE_DIR) -> int:
    """Delete cached responses the last full run did not use. Returns the number removed."""
    removed = 0
    for path in cache_dir.glob("*/*.json"):
        if path.stem not in llm.used:
            path.unlink()
            removed += 1
    return removed


async def aclose(llm: LLM) -> None:
    """Close network resources held by a CachedLLM (no-op for fakes)."""
    if isinstance(llm, CachedLLM):
        if llm._client is not None:
            await llm._client.close()
        if llm._credential is not None:
            await llm._credential.close()


async def complete_json(
    llm: LLM, messages: Sequence[Message], *, key: str = "", attempts: int = 3
) -> Any:
    """Ask for JSON, retrying with a new cache key when the output does not parse."""
    for attempt in range(attempts):
        text = await llm.complete(messages, json_mode=True, key=f"{key}#{attempt}")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    raise ValueError(f"model did not return JSON after {attempts} attempts ({key})")


def dumps(record: Any) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def jsonl_bytes(records: Iterable[Any]) -> bytes:
    return "".join(dumps(r) + "\n" for r in records).encode()


def write_jsonl(path: Path, records: Iterable[Any]) -> int:
    data = jsonl_bytes(records)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data.count(b"\n")


def write_jsonl_gz(path: Path, records: Iterable[Any]) -> tuple[int, str]:
    """Write gzipped JSONL with a fixed header; return (rows, sha256 of uncompressed bytes).

    gzip bytes can differ between zlib builds, so determinism is checked on the
    uncompressed content hash recorded in the manifest.
    """
    data = jsonl_bytes(records)
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0, compresslevel=9) as gz:
        gz.write(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.getvalue())
    return data.count(b"\n"), hashlib.sha256(data).hexdigest()


def read_jsonl(path: Path) -> list[Any]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_manifest(
    directory: Path,
    *,
    generator: str,
    version: str,
    seed: int,
    deployment: str | None,
    counts: Mapping[str, int],
    outputs: Mapping[str, str],
) -> Path:
    """Write manifest.json (no timestamps, so it is deterministic too)."""
    manifest = {
        "generator": generator,
        "generator_version": version,
        "seed": seed,
        "model_deployment": deployment,
        "record_counts": dict(sorted(counts.items())),
        "outputs_sha256": dict(sorted(outputs.items())),
    }
    path = directory / f"manifest.{generator}.json"
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return path
