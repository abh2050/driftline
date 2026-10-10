"""Workflow checkpoints in Cosmos `checkpoints` (spec 5.1.7), so a paused workflow (human in
the loop, restart, redeploy) resumes where it stopped."""

from __future__ import annotations

from typing import Any

from agent_framework import WorkflowCheckpoint
from agent_framework._workflows._checkpoint_encoding import (
    decode_checkpoint_value,
    encode_checkpoint_value,
)
from azure.cosmos.aio import ContainerProxy
from azure.cosmos.exceptions import CosmosResourceNotFoundError


class CosmosCheckpointStorage:
    def __init__(self, container: ContainerProxy) -> None:
        self.container = container

    async def save(self, checkpoint: WorkflowCheckpoint) -> str:
        doc = {
            "id": checkpoint.checkpoint_id,
            "pk": checkpoint.workflow_name,
            "workflow_name": checkpoint.workflow_name,
            "timestamp": checkpoint.timestamp,
            "payload": encode_checkpoint_value(checkpoint.to_dict()),
        }
        await self.container.upsert_item(doc)
        return checkpoint.checkpoint_id

    async def _load(self, doc: dict[str, Any]) -> WorkflowCheckpoint:
        return WorkflowCheckpoint.from_dict(decode_checkpoint_value(doc["payload"]))

    async def load(self, checkpoint_id: str) -> WorkflowCheckpoint:
        q = "SELECT * FROM c WHERE c.id = @id"
        async for doc in self.container.query_items(
            q, parameters=[{"name": "@id", "value": checkpoint_id}]
        ):
            return await self._load(doc)
        raise KeyError(checkpoint_id)

    async def list_checkpoints(self, *, workflow_name: str) -> list[WorkflowCheckpoint]:
        q = "SELECT * FROM c WHERE c.pk = @w ORDER BY c.timestamp"
        return [
            await self._load(d)
            async for d in self.container.query_items(
                q, parameters=[{"name": "@w", "value": workflow_name}], partition_key=workflow_name
            )
        ]

    async def list_checkpoint_ids(self, *, workflow_name: str) -> list[str]:
        q = "SELECT VALUE c.id FROM c WHERE c.pk = @w ORDER BY c.timestamp"
        return [
            str(i)
            async for i in self.container.query_items(
                q, parameters=[{"name": "@w", "value": workflow_name}], partition_key=workflow_name
            )
        ]

    async def get_latest(self, *, workflow_name: str) -> WorkflowCheckpoint | None:
        q = "SELECT TOP 1 * FROM c WHERE c.pk = @w ORDER BY c.timestamp DESC"
        async for d in self.container.query_items(
            q, parameters=[{"name": "@w", "value": workflow_name}], partition_key=workflow_name
        ):
            return await self._load(d)
        return None

    async def delete(self, checkpoint_id: str) -> bool:
        async for d in self.container.query_items(
            "SELECT c.id, c.pk FROM c WHERE c.id = @id",
            parameters=[{"name": "@id", "value": checkpoint_id}],
        ):
            try:
                await self.container.delete_item(d["id"], partition_key=d["pk"])
                return True
            except CosmosResourceNotFoundError:
                return False
        return False
