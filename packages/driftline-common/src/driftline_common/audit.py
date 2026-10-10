"""Immutable audit trail (spec 5.1.7): one JSON record per workflow step.

Records are write-once blobs in the `audit` container, which has a time-based immutability
policy (ADR 0003, ADR 0008). Each record carries the actor identity, the trace id and a hash
of the previous record for the same incident, so gaps or edits are detectable.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any

from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob.aio import BlobServiceClient
from opentelemetry import trace


class AuditWriter:
    def __init__(
        self, endpoint: str | None = None, credential: DefaultAzureCredential | None = None
    ) -> None:
        self._credential = credential or DefaultAzureCredential()
        self._service = BlobServiceClient(
            endpoint or os.environ["STORAGE_BLOB_ENDPOINT"], credential=self._credential
        )
        self._container = self._service.get_container_client("audit")
        self._last: dict[str, tuple[int, str]] = {}

    async def write(
        self, incident_id: str, step: str, actor: str, outcome: str, detail: dict[str, Any]
    ) -> str:
        seq, prev = self._last.get(incident_id, (0, ""))
        seq += 1
        span = trace.get_current_span().get_span_context()
        record = {
            "incident_id": incident_id,
            "seq": seq,
            "step": step,
            "actor": actor,
            "outcome": outcome,
            "at": datetime.now(UTC).isoformat(),
            "trace_id": format(span.trace_id, "032x") if span.is_valid else None,
            "prev_sha256": prev,
            "detail": detail,
        }
        body = json.dumps(record, sort_keys=True, default=str).encode()
        digest = hashlib.sha256(body).hexdigest()
        # timestamped so a restarted writer never collides with an existing write-once blob
        name = f"{incident_id}/{datetime.now(UTC):%Y%m%dT%H%M%S%f}-{seq:04d}-{step}.json"
        await self._container.upload_blob(name, body, overwrite=False)
        self._last[incident_id] = (seq, digest)
        return name

    async def list(self, incident_id: str) -> list[dict[str, Any]]:
        out = []
        async for b in self._container.list_blobs(name_starts_with=f"{incident_id}/"):
            data = await (await self._container.download_blob(b.name)).readall()
            out.append(json.loads(data))
        return sorted(out, key=lambda r: (r["at"], r["seq"]))

    async def close(self) -> None:
        await self._service.close()
        await self._credential.close()
