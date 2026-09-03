"""Append-only JSONL trace writer with a compact run summary."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from .events import TraceEvent, json_safe


READABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class TraceWriter:
    def __init__(
        self,
        *,
        output_root: Path,
        run_id: str,
        task_id: str,
        model: str,
        execution_backend: str = "local",
    ) -> None:
        if not READABLE_ID.fullmatch(run_id):
            raise ValueError("run_id must be a simple readable identifier")
        self.run_id = run_id
        self.task_id = task_id
        self.model = model
        self.execution_backend = execution_backend
        self.run_dir = Path(output_root).resolve() / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.run_dir / "trace.jsonl"
        self.summary_path = self.run_dir / "run_summary.json"
        self.trace_path.write_text("", encoding="utf-8")
        self._next_event_id = 1
        self._last_step = 0

    @property
    def event_count(self) -> int:
        return self._next_event_id - 1

    def emit(
        self,
        *,
        event_type: str,
        status: str,
        summary: str,
        data: Optional[dict[str, Any]] = None,
        step: Optional[int] = None,
        role: Optional[str] = None,
        tool: Optional[str] = None,
    ) -> TraceEvent:
        event_step = self._last_step if step is None else step
        if event_step < self._last_step:
            raise ValueError("trace step cannot move backwards")
        event = TraceEvent.create(
            event_id=self._next_event_id,
            event_type=event_type,
            step=event_step,
            status=status,
            summary=summary,
            data=data,
            role=role,
            tool=tool,
        )
        with self.trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        self._next_event_id += 1
        self._last_step = event_step
        return event

    def finalize(self, *, status: str, final_answer: dict[str, Any]) -> Path:
        summary = {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "model": self.model,
            "execution_backend": self.execution_backend,
            "status": status,
            "final_answer": json_safe(final_answer),
            "event_count": self.event_count,
        }
        self.summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return self.summary_path
