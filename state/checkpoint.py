from __future__ import annotations

import copy
from dataclasses import dataclass

from .models import AgentState


@dataclass
class Checkpoint:
    checkpoint_id: str
    state: AgentState
    reason: str


class CheckpointStore:
    def __init__(self) -> None:
        self._checkpoints: dict[str, Checkpoint] = {}
        self._counter = 0

    def create(self, state: AgentState, reason: str) -> str:
        self._counter += 1
        checkpoint_id = f"cp-{self._counter:04d}"
        snapshot = copy.deepcopy(state)
        # Events are append-only and maintained by the live runner.
        snapshot.events = []
        self._checkpoints[checkpoint_id] = Checkpoint(checkpoint_id, snapshot, reason)
        return checkpoint_id

    def restore(self, checkpoint_id: str, current_events: list) -> AgentState:
        if checkpoint_id not in self._checkpoints:
            raise KeyError(f"unknown checkpoint: {checkpoint_id}")
        restored = copy.deepcopy(self._checkpoints[checkpoint_id].state)
        restored.events = current_events
        return restored

    def has(self, checkpoint_id: str | None) -> bool:
        return bool(checkpoint_id and checkpoint_id in self._checkpoints)

