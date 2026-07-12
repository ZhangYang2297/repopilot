"""Context Manager: builds LLM message arrays with layered context and automatic compaction.

Message assembly order (top to bottom = first to last in messages array):
  1. System prompt (L0): fixed instructions ~800 tokens
  2. Project context (L0.5): REPOPILOT.md content (if exists), repo map, memory, injected skills
  3. Plan segment: current plan from planner
  4. Compacted summary (L2): result of micro/auto compact (when triggered)
  5. Recent steps (L1): verbatim recent messages, fit within remaining budget
  6. Pending tool result: last tool output (already tool-compacted)

Token budget:
  - system + project context ~5000 tokens
  - plan + summary ~1000-2000 tokens
  - recent steps fill the remainder up to budget_tokens
  - tool-compact always applied to tool outputs before storing

When token_usage_ratio() exceeds thresholds:
  - 0.75+: trigger micro_compact (summarize oldest 3-5 steps)
  - 0.90+: trigger auto_compact (summarize all but last K steps)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from repopilot.agent.compact import (
    estimate_tokens,
    tool_compact,
    micro_compact,
    auto_compact,
    CompactResult,
)

# Default allocation (tokens)
SYSTEM_RESERVE = 1000
PROJECT_RESERVE = 5000
PLAN_RESERVE = 1000
SUMMARY_RESERVE = 2000
RECENT_STEPS_KEEP = 10
MICRO_COMPACT_THRESHOLD = 0.75
AUTO_COMPACT_THRESHOLD = 0.90
TOOL_OUTPUT_MAX_CHARS = 12000
TOOL_OUTPUT_MAX_LINES = 200


@dataclass
class Step:
    """A single conversation turn or tool interaction."""
    role: str  # "user" | "assistant" | "tool" | "plan" | "observation"
    content: str
    tool_calls: Optional[list[dict]] = None
    tool_call_id: Optional[str] = None
    step_type: str = "message"  # "message" | "tool_result" | "plan" | "observation"
    tokens: int = 0

    def __post_init__(self):
        if self.tokens == 0:
            self.tokens = estimate_tokens(self.content) + (
                30 * (len(self.tool_calls) if self.tool_calls else 0)
            )

    def to_message(self) -> dict:
        """Convert to OpenAI-style message dict."""
        msg: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        return msg


@dataclass
class ContextManager:
    """Manages conversation context with layered assembly and automatic compaction."""

    budget_tokens: int = 120000
    system_prompt: str = ""
    repo_map_str: str = ""
    memory_str: str = ""
    injected_skills: str = ""
    plan: str = ""
    summary: str = ""
    steps: list[Step] = field(default_factory=list)
    pending_tool_result: Optional[Step] = None
    _actual_usage_calibration: float = 1.0

    def set_plan(self, plan: str) -> None:
        self.plan = plan

    def add_observation(self, text: str) -> None:
        self.steps.append(Step(role="system", content=text, step_type="observation"))

    def add_assistant(self, text: str, tool_calls: Optional[list[dict]] = None) -> None:
        content = text or ""
        self.steps.append(Step(role="assistant", content=content, tool_calls=tool_calls))

    def add_user(self, text: str) -> None:
        self.steps.append(Step(role="user", content=text))

    def add_tool_result(self, tool_call_id: str, content: str, is_error: bool = False) -> None:
        compacted = tool_compact(content, TOOL_OUTPUT_MAX_CHARS, TOOL_OUTPUT_MAX_LINES)
        prefix = "Error: " if is_error else ""
        self.steps.append(Step(
            role="tool",
            content=prefix + compacted,
            tool_call_id=tool_call_id,
            step_type="tool_result",
        ))

    def inject_skill_prompt(self, text: str) -> None:
        if text and text not in self.injected_skills:
            self.injected_skills += ("\n" + text if self.injected_skills else text)

    def recent_steps(self, n: int = 20) -> list[dict]:
        return [s.to_message() for s in self.steps[-n:]]

    def token_usage_ratio(self) -> float:
        used = self._current_total_tokens()
        return used / self.budget_tokens if self.budget_tokens > 0 else 1.0

    def _system_tokens(self) -> int:
        return estimate_tokens(self.system_prompt) + 200  # small overhead for message structure

    def _project_tokens(self) -> int:
        parts = [self.repo_map_str, self.memory_str, self.injected_skills]
        return sum(estimate_tokens(p) for p in parts)

    def _plan_tokens(self) -> int:
        return estimate_tokens(self.plan) if self.plan else 0

    def _summary_tokens(self) -> int:
        return estimate_tokens(self.summary) if self.summary else 0

    def _current_total_tokens(self) -> int:
        steps_tokens = sum(s.tokens for s in self.steps)
        return (self._system_tokens() + self._project_tokens() +
                self._plan_tokens() + self._summary_tokens() + steps_tokens)

    def build_messages(self, task: Optional[str] = None) -> list[dict]:
        """Assemble the full message array for the LLM.

        Order: system → project context → plan → summary → recent steps → pending tool → (optional task).
        """
        messages: list[dict] = []

        # L0: system prompt
        system_parts = []
        if self.system_prompt:
            system_parts.append(self.system_prompt)
        messages.append({"role": "system", "content": "\n\n".join(system_parts) if system_parts else "You are a helpful coding assistant."})

        # L0.5: project context
        project_parts = []
        if self.repo_map_str:
            project_parts.append(f"## Repository Structure\n{self.repo_map_str}")
        if self.memory_str:
            project_parts.append(f"## Memory / Notes\n{self.memory_str}")
        if self.injected_skills:
            project_parts.append(f"## Available Skills\n{self.injected_skills}")
        if project_parts:
            messages.append({"role": "system", "content": "\n\n".join(project_parts)})

        # Plan
        if self.plan:
            messages.append({"role": "system", "content": f"## Current Plan\n{self.plan}"})

        # L2: compacted summary
        if self.summary:
            messages.append({"role": "system", "content": f"## Earlier Work Summary\n{self.summary}"})

        # L1: recent steps (all steps fit within budget; compaction handles trimming)
        remaining_budget = self.budget_tokens - (
            self._system_tokens() + self._project_tokens() +
            self._plan_tokens() + self._summary_tokens()
        )
        steps_to_include = self._select_steps_for_budget(remaining_budget)
        for step in steps_to_include:
            messages.append(step.to_message())

        # Pending tool result
        if self.pending_tool_result:
            messages.append(self.pending_tool_result.to_message())

        # Task as final user message
        if task:
            messages.append({"role": "user", "content": task})

        return messages

    def _select_steps_for_budget(self, budget: int) -> list[Step]:
        """Select steps to include, preferring the most recent ones."""
        if budget <= 0:
            budget = 4000
        included: list[Step] = []
        used = 0
        for step in reversed(self.steps):
            if used + step.tokens > budget and included:
                break
            included.insert(0, step)
            used += step.tokens
        return included

    def needs_compaction(self) -> Optional[str]:
        """Return 'micro', 'auto', or None based on current token ratio."""
        ratio = self.token_usage_ratio()
        if ratio >= AUTO_COMPACT_THRESHOLD:
            return "auto"
        if ratio >= MICRO_COMPACT_THRESHOLD:
            return "micro"
        return None

    def compact(self, level: str, llm: Any) -> CompactResult:
        """Trigger a compaction at the given level ('micro' or 'auto')."""
        if level == "micro":
            to_compact = self.steps[:5] if len(self.steps) > RECENT_STEPS_KEEP else self.steps[:3]
            result = micro_compact([s.to_message() for s in to_compact], llm)
            if result.summary:
                self.summary = (self.summary + "\n\n" if self.summary else "") + result.summary
                self.steps = self.steps[len(to_compact):]
            return result
        elif level == "auto":
            keep_recent = RECENT_STEPS_KEEP
            steps_as_msgs = [s.to_message() for s in self.steps]
            result = auto_compact(steps_as_msgs, llm, keep_recent=keep_recent)
            if result.summary:
                self.summary = result.summary
                self.steps = self.steps[-keep_recent:] if len(self.steps) > keep_recent else self.steps
            return result
        else:
            return CompactResult(summary="", steps_compacted=0, tokens_saved=0)

    def update_actual_usage(self, prompt_tokens: int) -> None:
        """Calibrate token estimates with real usage data from API response."""
        estimated = self._current_total_tokens()
        if estimated > 0:
            self._actual_usage_calibration = prompt_tokens / estimated
