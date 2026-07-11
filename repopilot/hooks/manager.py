"""Hook Manager — lifecycle event system for the agent loop.

Hooks allow cross-cutting concerns (logging, cost tracking, permission
checks, metrics) to observe and modify the agent loop without coupling
them to the core logic.

Events:
  pre_llm      — Before an LLM call. Receives (messages, tools, tier).
                 Return HookResult.skip to cancel the call; return override
                 to inject a fake LLMResponse.
  post_llm     — After an LLM call. Receives (response).
  pre_tool     — Before a tool execution. Receives (tool_name, args).
                 Return HookResult.deny to block; return override to fake result.
  post_tool    — After a tool execution. Receives (tool_name, args, result, duration_ms).
  on_finish    — When the agent finishes. Receives (summary, tests_passed).
  on_error     — When an unhandled error occurs. Receives (exception).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class HookResult:
    """What a hook returns to influence control flow.

    action:
      - "continue": let processing proceed normally
      - "skip":     skip this step (pre_llm: don't call LLM; pre_tool: don't run tool)
      - "deny":     deny the action with reason (similar to permission denied)
    override: replacement return value (for skip/deny)
    reason:   human-readable explanation for deny/skip
    """
    action: str = "continue"  # "continue" | "skip" | "deny"
    override: Any = None
    reason: str = ""

    def __post_init__(self):
        if self.action not in ("continue", "skip", "deny"):
            raise ValueError(f"Invalid HookResult action: {self.action!r}")

    @classmethod
    def cont(cls) -> "HookResult":
        return cls(action="continue")

    @classmethod
    def deny(cls, reason: str = "", override: Any = None) -> "HookResult":
        return cls(action="deny", reason=reason, override=override)

    @classmethod
    def skip(cls, override: Any = None, reason: str = "") -> "HookResult":
        return cls(action="skip", override=override, reason=reason)


# Valid event names
VALID_EVENTS = {
    "pre_llm", "post_llm",
    "pre_tool", "post_tool",
    "on_finish", "on_error",
}


class HookManager:
    """Collects and fires hooks for lifecycle events."""

    def __init__(self) -> None:
        self._hooks: dict[str, list[Callable[..., HookResult | None]]] = {
            ev: [] for ev in VALID_EVENTS
        }

    def register(self, event: str, fn: Callable[..., HookResult | None]) -> None:
        """Register a hook function for an event.

        Hook functions may return HookResult to influence flow; return None
        is treated as continue.
        """
        if event not in VALID_EVENTS:
            raise ValueError(f"Unknown event: {event!r}. Valid: {sorted(VALID_EVENTS)}")
        self._hooks[event].append(fn)

    def fire(self, event: str, *args: Any, **kwargs: Any) -> HookResult:
        """Fire all hooks for `event`. Returns the first non-continue result
        (deny wins over skip) or HookResult.cont() if all hooks pass."""
        if event not in VALID_EVENTS:
            raise ValueError(f"Unknown event: {event!r}")
        for fn in self._hooks[event]:
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                # Hook errors should not crash the agent; log and continue
                import logging
                logging.getLogger("repopilot.hooks").warning(
                    "Hook %s for event %s raised: %s", fn.__name__, event, exc
                )
                continue
            if result is None:
                continue
            if result.action == "deny":
                return result  # deny is highest priority
            if result.action == "skip":
                return result
        return HookResult.cont()
