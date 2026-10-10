"""Request bodies for ops-api."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Answer(BaseModel):
    approved: bool
    note: str = Field(default="", max_length=2000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    incident_id: str | None = None


class FaultRequest(BaseModel):
    fault_id: str


class ReplayRequest(BaseModel):
    fault_id: str
    trial: int = Field(default=1, ge=1, le=5)
