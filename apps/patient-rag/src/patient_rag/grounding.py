"""Async groundedness for sampled requests, via azure-ai-evaluation (score 1-5)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from azure.ai.evaluation import GroundednessEvaluator
from azure.identity import DefaultAzureCredential

log = logging.getLogger("patient-rag.grounding")


class GroundednessJudge:
    def __init__(self, endpoint: str, deployment: str) -> None:
        # The credential goes to the evaluator, not into the model config dict.
        self._config: dict[str, Any] = {"azure_endpoint": endpoint, "azure_deployment": deployment}
        self._credential = DefaultAzureCredential()
        self._evaluator: GroundednessEvaluator | None = None

    def _get(self) -> GroundednessEvaluator:
        if self._evaluator is None:
            self._evaluator = GroundednessEvaluator(  # type: ignore[no-untyped-call]
                self._config, credential=self._credential, is_reasoning_model=True
            )
        return self._evaluator

    async def score(self, query: str, response: str, context: str) -> float | None:
        try:
            result = await asyncio.to_thread(
                self._get(), query=query, response=response, context=context
            )
            value = result.get("groundedness")
            return None if value is None else round(float(value), 2)
        except Exception:
            log.exception("groundedness evaluation failed")
            return None
