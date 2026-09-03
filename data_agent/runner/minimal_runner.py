"""Minimal, dependency-injected Data Agent orchestration with replay traces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..llm import (
    AnalysisSelection,
    FinalAnswer,
    SQLGeneration,
    analysis_messages,
    final_answer_messages,
    sql_generation_messages,
)
from ..tools.metric_search import METRICS
from ..tools.metric_search import requests_unsupported_metric
from ..trace import TraceWriter


@dataclass(frozen=True)
class AgentRunResult:
    run_id: str
    task_id: str
    model: str
    status: str
    final_answer: dict[str, Any]
    trace_path: Path
    summary_path: Path


class DataAgentRunner:
    def __init__(
        self,
        *,
        llm_client: Any,
        tool_registry: Any,
        trace_root: Path,
        execution_backend: str = "local",
    ) -> None:
        self.llm_client = llm_client
        self.tool_registry = tool_registry
        self.trace_root = Path(trace_root)
        self.execution_backend = execution_backend

    @property
    def model(self) -> str:
        return str(getattr(self.llm_client, "model", "unknown"))

    def _llm_generate(
        self,
        writer: TraceWriter,
        *,
        step: int,
        stage: str,
        messages: list[dict[str, str]],
        response_type: Any,
    ) -> Any:
        writer.emit(
            event_type="llm_request_started",
            step=step,
            status="info",
            summary=f"Requested structured {stage} output.",
            data={"stage": stage, "model": self.model},
            role="assistant",
        )
        response = self.llm_client.generate_json(messages, response_type=response_type)
        writer.emit(
            event_type="llm_request_completed",
            step=step,
            status="success" if response.ok else "error",
            summary=(
                f"Received structured {stage} output."
                if response.ok
                else f"Structured {stage} request failed."
            ),
            data={
                "stage": stage,
                "ok": response.ok,
                "attempts": response.attempts,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "latency_ms": response.latency_ms,
                "error_type": response.error_type,
                "error": response.error,
            },
            role="assistant",
        )
        return response

    def _failed(
        self,
        writer: TraceWriter,
        *,
        step: int,
        error_type: str,
        error: str,
    ) -> AgentRunResult:
        final_answer = {
            "answer": "The run failed before a verified answer was produced.",
            "key_values": {},
            "limitations": [error],
        }
        writer.emit(
            event_type="run_failed",
            step=step,
            status="error",
            summary="Run failed before verification.",
            data={"error_type": error_type, "error": error},
        )
        writer.finalize(status="FAILED", final_answer=final_answer)
        return AgentRunResult(
            run_id=writer.run_id,
            task_id=writer.task_id,
            model=writer.model,
            status="FAILED",
            final_answer=final_answer,
            trace_path=writer.trace_path,
            summary_path=writer.summary_path,
        )

    def _safe_stop(
        self,
        writer: TraceWriter,
        *,
        step: int,
        metric_query: str,
        reason: str,
    ) -> AgentRunResult:
        final_answer = {
            "answer": "当前六表没有完整退款台账，无法可靠计算净收入。",
            "key_values": {},
            "limitations": [
                reason,
                "Customer Payment Value and Item Sales Value are not net revenue.",
            ],
        }
        writer.emit(
            event_type="data_gap_detected",
            step=step,
            status="warning",
            summary="Unsupported metric requires a deterministic safe stop.",
            data={
                "metric_query": metric_query,
                "answerability": "UNANSWERABLE_WITH_CURRENT_DATA",
                "expected_action": "STOP_WITH_DATA_GAP",
                "reason": reason,
            },
        )
        writer.emit(
            event_type="verification_completed",
            step=step,
            status="success",
            summary="Verified that no numeric answer is allowed with current data.",
            data={"verified": True, "outcome": "DATA_GAP_CONFIRMED"},
        )
        writer.emit(
            event_type="run_finished",
            step=step,
            status="stopped",
            summary="Run stopped safely because the requested metric is unsupported.",
            data={"status": "STOP_WITH_DATA_GAP"},
        )
        writer.finalize(status="STOP_WITH_DATA_GAP", final_answer=final_answer)
        return AgentRunResult(
            run_id=writer.run_id,
            task_id=writer.task_id,
            model=writer.model,
            status="STOP_WITH_DATA_GAP",
            final_answer=final_answer,
            trace_path=writer.trace_path,
            summary_path=writer.summary_path,
        )

    def run(
        self,
        *,
        run_id: str,
        task_id: str,
        question: str,
        cross_check_sql: Optional[str] = None,
    ) -> AgentRunResult:
        writer = TraceWriter(
            output_root=self.trace_root,
            run_id=run_id,
            task_id=task_id,
            model=self.model,
            execution_backend=self.execution_backend,
        )
        writer.emit(
            event_type="run_started",
            step=0,
            status="info",
            summary=f"Started analysis for {task_id}.",
            data={
                "run_id": run_id,
                "task_id": task_id,
                "model": self.model,
                "execution_backend": self.execution_backend,
                "status": "RUNNING",
            },
        )
        writer.emit(
            event_type="question_received",
            step=0,
            status="info",
            summary="Received the runtime question.",
            data={"question": question},
            role="user",
        )

        available_metrics = [
            {"metric_id": metric_id, "display_name": definition["display_name"]}
            for metric_id, definition in METRICS.items()
        ]
        analysis_response = self._llm_generate(
            writer,
            step=1,
            stage="analysis",
            messages=analysis_messages(question, available_metrics),
            response_type=AnalysisSelection,
        )
        if not analysis_response.ok:
            return self._failed(
                writer,
                step=1,
                error_type=analysis_response.error_type or "LLM_ERROR",
                error=analysis_response.error or "analysis generation failed",
            )
        analysis = analysis_response.data
        writer.emit(
            event_type="analysis_generated",
            step=1,
            status="success",
            summary="Selected metrics and candidate tables.",
            data=analysis,
            role="assistant",
        )

        metric_queries = list(analysis["metrics"])
        if requests_unsupported_metric(question):
            # The model still performs metric selection, but it cannot silently
            # substitute a supported amount for an unsupported business metric.
            metric_queries = [question]

        resolved_metrics = []
        for metric_query in metric_queries:
            writer.emit(
                event_type="metric_search_started",
                step=2,
                status="info",
                summary=f"Resolving metric {metric_query}.",
                data={"query": metric_query},
                tool="search_metric_definition",
            )
            writer.emit(
                event_type="tool_call_started",
                step=2,
                status="info",
                summary="Calling metric definition search.",
                data={"arguments": {"query": metric_query}},
                tool="search_metric_definition",
            )
            metric_result = self.tool_registry.call(
                "search_metric_definition", {"query": metric_query}
            )
            writer.emit(
                event_type="tool_call_completed",
                step=2,
                status="success" if metric_result.ok else "warning",
                summary="Metric definition search returned.",
                data={"tool_result": metric_result.to_dict()},
                tool="search_metric_definition",
            )
            writer.emit(
                event_type="metric_search_completed",
                step=2,
                status="success" if metric_result.ok else "warning",
                summary=(
                    f"Resolved {metric_query}."
                    if metric_result.ok
                    else f"Could not resolve {metric_query} as a supported metric."
                ),
                data={"tool_result": metric_result.to_dict()},
                tool="search_metric_definition",
            )
            if not metric_result.ok:
                if metric_result.error_type == "UNSUPPORTED_METRIC":
                    return self._safe_stop(
                        writer,
                        step=3,
                        metric_query=metric_query,
                        reason=metric_result.error or "unsupported metric",
                    )
                return self._failed(
                    writer,
                    step=3,
                    error_type=metric_result.error_type or "METRIC_ERROR",
                    error=metric_result.error or "metric resolution failed",
                )
            resolved_metrics.append(metric_result.result)

        needed_tables = []
        for table_name in [
            *analysis["needed_tables"],
            *[
                table
                for metric in resolved_metrics
                for table in metric["required_tables"]
            ],
        ]:
            if table_name not in needed_tables:
                needed_tables.append(table_name)

        schemas = []
        for table_name in needed_tables:
            writer.emit(
                event_type="schema_inspection_started",
                step=3,
                status="info",
                summary=f"Inspecting schema for {table_name}.",
                data={"table": table_name},
                tool="inspect_schema",
            )
            writer.emit(
                event_type="tool_call_started",
                step=3,
                status="info",
                summary="Calling schema inspection.",
                data={"arguments": {"table_name": table_name}},
                tool="inspect_schema",
            )
            schema_result = self.tool_registry.call(
                "inspect_schema", {"table_name": table_name}
            )
            writer.emit(
                event_type="tool_call_completed",
                step=3,
                status="success" if schema_result.ok else "error",
                summary="Schema inspection returned.",
                data={"tool_result": schema_result.to_dict()},
                tool="inspect_schema",
            )
            writer.emit(
                event_type="schema_inspection_completed",
                step=3,
                status="success" if schema_result.ok else "error",
                summary=f"Schema inspection completed for {table_name}.",
                data={"tool_result": schema_result.to_dict()},
                tool="inspect_schema",
            )
            if not schema_result.ok:
                return self._failed(
                    writer,
                    step=3,
                    error_type=schema_result.error_type or "SCHEMA_ERROR",
                    error=schema_result.error or "schema inspection failed",
                )
            schemas.append(schema_result.result)

        sql_response = self._llm_generate(
            writer,
            step=4,
            stage="sql_generation",
            messages=sql_generation_messages(question, resolved_metrics, schemas),
            response_type=SQLGeneration,
        )
        if not sql_response.ok:
            return self._failed(
                writer,
                step=4,
                error_type=sql_response.error_type or "LLM_ERROR",
                error=sql_response.error or "SQL generation failed",
            )
        generated = sql_response.data
        writer.emit(
            event_type="sql_generated",
            step=4,
            status="success",
            summary="Generated one read-only analysis query.",
            data={"sql": generated["sql"], "reason": generated["reason"]},
            role="assistant",
        )

        writer.emit(
            event_type="tool_call_started",
            step=5,
            status="info",
            summary="Executing generated SQL.",
            data={"arguments": {"sql": generated["sql"], "max_rows": 200}},
            tool="execute_sql",
        )
        sql_result = self.tool_registry.call(
            "execute_sql", {"sql": generated["sql"], "max_rows": 200}
        )
        writer.emit(
            event_type="tool_call_completed",
            step=5,
            status="success" if sql_result.ok else "error",
            summary="SQL execution tool returned.",
            data={"tool_result": sql_result.to_dict()},
            tool="execute_sql",
        )
        writer.emit(
            event_type="sql_execution_completed",
            step=5,
            status="success" if sql_result.ok else "error",
            summary="Generated SQL executed successfully." if sql_result.ok else "Generated SQL failed.",
            data={"tool_result": sql_result.to_dict()},
            tool="execute_sql",
        )
        if not sql_result.ok:
            return self._failed(
                writer,
                step=5,
                error_type=sql_result.error_type or "SQL_ERROR",
                error=sql_result.error or "SQL execution failed",
            )

        cross_result = None
        if cross_check_sql is not None:
            writer.emit(
                event_type="cross_check_started",
                step=6,
                status="info",
                summary="Starting independent SQL cross-check.",
                data={"absolute_tolerance": 0.01, "relative_tolerance": 0.000001},
                tool="cross_check",
            )
            writer.emit(
                event_type="tool_call_started",
                step=6,
                status="info",
                summary="Calling numeric cross-check.",
                data={
                    "arguments": {
                        "sql_a": generated["sql"],
                        "sql_b": cross_check_sql,
                        "absolute_tolerance": 0.01,
                        "relative_tolerance": 0.000001,
                    }
                },
                tool="cross_check",
            )
            cross_result = self.tool_registry.call(
                "cross_check",
                {
                    "sql_a": generated["sql"],
                    "sql_b": cross_check_sql,
                    "absolute_tolerance": 0.01,
                    "relative_tolerance": 0.000001,
                },
            )
            writer.emit(
                event_type="tool_call_completed",
                step=6,
                status="success" if cross_result.ok else "error",
                summary="Cross-check tool returned.",
                data={"tool_result": cross_result.to_dict()},
                tool="cross_check",
            )
            cross_matched = cross_result.ok and cross_result.result["matched"]
            writer.emit(
                event_type="cross_check_completed",
                step=6,
                status="success" if cross_matched else "error",
                summary="Independent SQL results matched." if cross_matched else "Independent SQL results did not match.",
                data={"tool_result": cross_result.to_dict()},
                tool="cross_check",
            )
            if not cross_matched:
                return self._failed(
                    writer,
                    step=6,
                    error_type=cross_result.error_type or "CROSS_CHECK_FAILED",
                    error=cross_result.error or "independent SQL results did not match",
                )

        final_step = 7 if cross_check_sql is not None else 6
        answer_response = self._llm_generate(
            writer,
            step=final_step,
            stage="final_answer",
            messages=final_answer_messages(
                question,
                resolved_metrics,
                sql_result.result,
                cross_result.result if cross_result is not None else None,
            ),
            response_type=FinalAnswer,
        )
        if not answer_response.ok:
            return self._failed(
                writer,
                step=final_step,
                error_type=answer_response.error_type or "LLM_ERROR",
                error=answer_response.error or "final answer generation failed",
            )
        final_answer = answer_response.data
        writer.emit(
            event_type="answer_generated",
            step=final_step,
            status="success",
            summary="Generated a concise answer from verified tool results.",
            data=final_answer,
            role="assistant",
        )
        writer.emit(
            event_type="verification_completed",
            step=final_step + 1,
            status="success",
            summary="Verified SQL execution and available cross-check evidence.",
            data={
                "verified": True,
                "sql_execution_ok": True,
                "cross_check_matched": (
                    cross_result.result["matched"] if cross_result is not None else None
                ),
            },
        )
        writer.emit(
            event_type="run_finished",
            step=final_step + 1,
            status="success",
            summary="Run completed successfully.",
            data={"status": "SUCCESS"},
        )
        writer.finalize(status="SUCCESS", final_answer=final_answer)
        return AgentRunResult(
            run_id=run_id,
            task_id=task_id,
            model=self.model,
            status="SUCCESS",
            final_answer=final_answer,
            trace_path=writer.trace_path,
            summary_path=writer.summary_path,
        )
