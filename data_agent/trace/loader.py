"""Validated trace loading for future replay clients."""

from __future__ import annotations

import json
from pathlib import Path

from .events import TraceEvent


def load_trace(path: Path) -> list[TraceEvent]:
    events = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid trace JSON on line {line_number}") from error
            if not isinstance(payload, dict):
                raise ValueError(f"trace line {line_number} must be an object")
            events.append(TraceEvent.from_dict(payload))

    event_ids = [event.event_id for event in events]
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("event_id values must be unique")
    events.sort(key=lambda event: event.event_id)
    if [event.event_id for event in events] != list(range(1, len(events) + 1)):
        raise ValueError("event_id values must be consecutive and start at 1")
    if any(current.step < previous.step for previous, current in zip(events, events[1:])):
        raise ValueError("trace steps must be non-decreasing")
    return events
