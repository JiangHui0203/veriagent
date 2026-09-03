from __future__ import annotations

import time
import uuid

from ..retrieval import LocalRetriever
from ..schemas import RetrievalRequest, ToolResult


class RetrievalTool:
    name = "retrieve_evidence"

    def __init__(self, retriever: LocalRetriever) -> None:
        self.retriever = retriever

    def execute(self, request: RetrievalRequest) -> ToolResult:
        started = time.perf_counter()
        try:
            response = self.retriever.search(request)
            return ToolResult(
                call_id=str(uuid.uuid4()),
                tool=self.name,
                ok=True,
                items=response.items,
                metadata={
                    "request_key": request.key,
                    "total_matches": response.total_matches,
                    "scores": response.scores,
                    "returned": len(response.items),
                },
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception as exc:
            return ToolResult(
                call_id=str(uuid.uuid4()),
                tool=self.name,
                ok=False,
                error_type=type(exc).__name__,
                error_message=str(exc),
                latency_ms=(time.perf_counter() - started) * 1000,
                retryable=True,
            )

