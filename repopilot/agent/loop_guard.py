"""Loop guard — abort the agent when it makes no progress.

Detects three classes of stuck behaviour:

1. **Same call repeated**: identical (tool_name, args) three times in a row
   (usually caused by the model ignoring an error and retrying blindly).
2. **Same error repeated**: three consecutive tool results with the same
   ``error_code`` (except a small allowlist that is retry-friendly).
3. **No progress**: N consecutive steps produced no successful tool result
   (all errors) — often means the model is exploring blindly.

The guard is *not* the state machine.  It only decides whether to break
out of ``run_turn`` early with a synthetic assistant summary and an
error hint the model can use in its next turn.

Design goals:
  * Zero cost for successful trajectories.
  * All limits configurable so tests can pin down thresholds.
  * No dependency on the LLM or IO — pure in-memory bookkeeping.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional

# Error codes for which repetition is *not* a red flag on its own —
# e.g. TIMEOUT may legitimately be retried after backoff.  We still cap
# them via max_same_error_code, but with a wider budget by design.
_RETRY_FRIENDLY = frozenset({"E_TIMEOUT", "E_SANDBOX"})


def _fingerprint(tool_name: str, args: dict[str, Any]) -> str:
    """Stable short hash of (tool_name, normalised args)."""
    try:
        payload = json.dumps(args, sort_keys=True, default=str, ensure_ascii=False)
    except Exception:
        payload = repr(args)
    return hashlib.sha1(f"{tool_name}|{payload}".encode("utf-8")).hexdigest()[:12]


@dataclass
class LoopGuardConfig:
    max_same_call_in_row: int = 3
    max_same_error_code: int = 3
    max_no_progress_steps: int = 6


@dataclass
class LoopGuardState:
    last_call_fp: Optional[str] = None
    same_call_count: int = 0
    last_error_code: Optional[str] = None
    same_error_count: int = 0
    no_progress_count: int = 0
    aborted_reason: Optional[str] = None
    step_count: int = 0


@dataclass
class LoopGuard:
    """Track successive tool calls and results; report abort conditions."""

    config: LoopGuardConfig = field(default_factory=LoopGuardConfig)
    state: LoopGuardState = field(default_factory=LoopGuardState)

    # ── API ─────────────────────────────────────────────────────────

    def record_call(self, tool_name: str, args: dict[str, Any]) -> Optional[str]:
        """Record a *pending* tool call.  Return abort reason if guard fires."""
        fp = _fingerprint(tool_name, args)
        if fp == self.state.last_call_fp:
            self.state.same_call_count += 1
        else:
            self.state.last_call_fp = fp
            self.state.same_call_count = 1
        if self.state.same_call_count >= self.config.max_same_call_in_row:
            reason = (
                f"Same call repeated {self.state.same_call_count} times "
                f"({tool_name}). Aborting to avoid an infinite loop; "
                f"ask the model to change strategy."
            )
            self.state.aborted_reason = reason
            return reason
        return None

    def record_result(self, error_code: Optional[str]) -> Optional[str]:
        """Record the outcome of the last call.  Return abort reason if fired."""
        self.state.step_count += 1
        if error_code is None:
            # success clears both error counters and no-progress
            self.state.last_error_code = None
            self.state.same_error_count = 0
            self.state.no_progress_count = 0
            return None

        # error path
        self.state.no_progress_count += 1
        if error_code == self.state.last_error_code:
            self.state.same_error_count += 1
        else:
            self.state.last_error_code = error_code
            self.state.same_error_count = 1

        # widen budget for retry-friendly codes
        limit = self.config.max_same_error_code
        if error_code in _RETRY_FRIENDLY:
            limit += 2

        if self.state.same_error_count >= limit:
            reason = (
                f"Same error {error_code} occurred {self.state.same_error_count} times "
                f"in a row. Aborting to avoid burning tokens; the model should "
                f"change approach or ask the user."
            )
            self.state.aborted_reason = reason
            return reason

        if self.state.no_progress_count >= self.config.max_no_progress_steps:
            reason = (
                f"{self.state.no_progress_count} consecutive tool errors with no "
                f"progress. Aborting so the user can intervene."
            )
            self.state.aborted_reason = reason
            return reason
        return None

    def reset(self) -> None:
        self.state = LoopGuardState()
