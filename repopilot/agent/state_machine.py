"""Structured task state machine for RepoPilot agent.

Defines all valid states, transitions, timestamps, and termination reasons.
Used by both run_agent() and ReplSession.run_turn() via AgentLoopCore.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TransitionError(RuntimeError):
    """Raised when an invalid state transition is attempted."""


class TaskState(Enum):
    IDLE = "idle"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_TOOL = "waiting_tool"
    VERIFYING = "verifying"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


# ── Transition table ──────────────────────────────────────────────────────
# Maps (from_state, action) -> to_state
# Actions: plan, run, wait_approval, wait_tool, verify, cancel, cancelled,
#          complete, fail, block

_TRANSITIONS: dict[tuple[TaskState, str], TaskState] = {
    # IDLE -> PLANNING
    (TaskState.IDLE, "plan"): TaskState.PLANNING,

    # PLANNING -> RUNNING / FAILED
    (TaskState.PLANNING, "run"): TaskState.RUNNING,
    (TaskState.PLANNING, "fail"): TaskState.FAILED,

    # RUNNING -> WAITING_APPROVAL / WAITING_TOOL / VERIFYING / FAILED / COMPLETED
    (TaskState.RUNNING, "wait_approval"): TaskState.WAITING_APPROVAL,
    (TaskState.RUNNING, "wait_tool"): TaskState.WAITING_TOOL,
    (TaskState.RUNNING, "verify"): TaskState.VERIFYING,
    (TaskState.RUNNING, "fail"): TaskState.FAILED,
    (TaskState.RUNNING, "complete"): TaskState.COMPLETED,

    # WAITING_APPROVAL -> RUNNING / BLOCKED
    (TaskState.WAITING_APPROVAL, "run"): TaskState.RUNNING,
    (TaskState.WAITING_APPROVAL, "block"): TaskState.BLOCKED,

    # WAITING_TOOL -> RUNNING / FAILED
    (TaskState.WAITING_TOOL, "run"): TaskState.RUNNING,
    (TaskState.WAITING_TOOL, "fail"): TaskState.FAILED,

    # VERIFYING -> RUNNING / FAILED / COMPLETED
    (TaskState.VERIFYING, "run"): TaskState.RUNNING,
    (TaskState.VERIFYING, "fail"): TaskState.FAILED,
    (TaskState.VERIFYING, "complete"): TaskState.COMPLETED,

    # CANCELLING -> CANCELLED / FAILED
    (TaskState.CANCELLING, "cancelled"): TaskState.CANCELLED,
    (TaskState.CANCELLING, "fail"): TaskState.FAILED,
}

# Cancel is allowed from any non-terminal, non-IDLE state
_CANCEL_ORIGINS = {
    TaskState.PLANNING,
    TaskState.RUNNING,
    TaskState.WAITING_APPROVAL,
    TaskState.WAITING_TOOL,
    TaskState.VERIFYING,
}

# Terminal states (no further transitions allowed)
_TERMINAL_STATES = {
    TaskState.CANCELLED,
    TaskState.COMPLETED,
    TaskState.FAILED,
    TaskState.BLOCKED,
}


@dataclass
class TaskStateMachine:
    """State machine for agent task lifecycle.

    Usage:
        sm = TaskStateMachine()
        sm.transition("plan")
        sm.transition("run")
        sm.transition("verify")
        sm.transition("complete", reason="Task finished")
        assert sm.state == TaskState.COMPLETED
    """

    _state: TaskState = field(default=TaskState.IDLE)
    started_at: float = field(default_factory=time.time)
    last_transition_at: float = field(default_factory=time.time)
    termination_reason: str = ""

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def state(self) -> TaskState:
        return self._state

    @property
    def duration_ms(self) -> int:
        """Total wall-clock duration in milliseconds."""
        return int((time.time() - self.started_at) * 1000)

    @property
    def is_terminal(self) -> bool:
        return self._state in _TERMINAL_STATES

    # ── Transitions ───────────────────────────────────────────────────────

    def transition(self, action: str, reason: str = "") -> None:
        """Attempt a state transition identified by *action*.

        Args:
            action: One of plan, run, wait_approval, wait_tool, verify,
                    cancel, cancelled, complete, fail, block.
            reason: Optional human-readable explanation for the transition
                    (required for terminal states, recommended for all).

        Raises:
            TransitionError: If the transition is invalid from the current state.
        """
        if self.is_terminal:
            raise TransitionError(
                f"Cannot transition from terminal state {self._state.name}"
            )

        if action == "cancel":
            if self._state not in _CANCEL_ORIGINS:
                raise TransitionError(
                    f"Cannot cancel from {self._state.name}"
                )
            self._state = TaskState.CANCELLING
        else:
            key = (self._state, action)
            if key not in _TRANSITIONS:
                raise TransitionError(
                    f"Invalid transition: {self._state.name} -> {action}"
                )
            self._state = _TRANSITIONS[key]

        self.last_transition_at = time.time()
        if reason:
            self.termination_reason = reason

    # ── Serialization ─────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self._state.name,
            "started_at": self.started_at,
            "last_transition_at": self.last_transition_at,
            "termination_reason": self.termination_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskStateMachine:
        try:
            state = TaskState[data["state"]]
        except KeyError:
            raise ValueError(f"invalid state: {data['state']}")

        sm = cls(
            _state=state,
            started_at=data.get("started_at", time.time()),
            last_transition_at=data.get("last_transition_at", time.time()),
            termination_reason=data.get("termination_reason", ""),
        )
        return sm

    # ── Integration helpers ───────────────────────────────────────────────

    def to_run_result_status(self) -> str:
        """Map terminal state to RunResult.status string.

        Returns:
            One of "completed", "error", "cancelled", "blocked".

        Raises:
            ValueError: If the current state is not terminal.
        """
        if not self.is_terminal:
            raise ValueError(
                f"State {self._state.name} is not terminal; "
                "cannot convert to RunResult status"
            )
        mapping = {
            TaskState.COMPLETED: "completed",
            TaskState.FAILED: "error",
            TaskState.CANCELLED: "cancelled",
            TaskState.BLOCKED: "blocked",
        }
        return mapping.get(self._state, "error")

    def reset(self) -> None:
        """Reset the state machine to IDLE."""
        self._state = TaskState.IDLE
        self.started_at = time.time()
        self.last_transition_at = time.time()
        self.termination_reason = ""
