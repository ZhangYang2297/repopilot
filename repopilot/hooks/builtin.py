"""Built-in hooks: audit log, cost tracker integration."""
from __future__ import annotations
from typing import Any, TYPE_CHECKING

from repopilot.hooks.manager import HookResult

if TYPE_CHECKING:
    from repopilot.agent.cost import CostTracker
    import logging


def make_audit_log_hook(logger: "logging.Logger"):
    """Create a pre_tool/post_tool hook that logs every tool call."""

    def pre_tool(tool_name: str, args: dict) -> HookResult | None:
        safe_args = {k: v for k, v in args.items() if k != "content" or len(str(v)) < 200}
        logger.debug("pre_tool: %s args=%s", tool_name, safe_args)
        return None

    def post_tool(tool_name: str, args: dict, result: Any, duration_ms: int) -> None:
        ok = getattr(result, "error", None) is None
        logger.info("tool=%s ok=%s duration_ms=%d", tool_name, ok, duration_ms)

    return {"pre_tool": pre_tool, "post_tool": post_tool}


def make_cost_hooks(tracker: "CostTracker"):
    """Create hooks that feed the CostTracker."""

    def post_llm(response: Any) -> None:
        usage = getattr(response, "usage", {}) or {}
        model = getattr(response, "model", "")
        if usage:
            tracker.on_llm_call(usage, model)

    def post_tool(tool_name: str, args: dict, result: Any, duration_ms: int) -> None:
        tracker.on_tool_call(tool_name, duration_ms)

    return {"post_llm": post_llm, "post_tool": post_tool}


def install_builtin_hooks(hook_manager, cost_tracker: "CostTracker | None" = None,
                          logger: "logging.Logger | None" = None) -> None:
    """Convenience: install all builtin hooks on a HookManager."""
    import logging as _logging
    if logger is None:
        logger = _logging.getLogger("repopilot")
    # Audit log hooks
    audit = make_audit_log_hook(logger)
    hook_manager.register("pre_tool", audit["pre_tool"])
    hook_manager.register("post_tool", audit["post_tool"])

    # Cost hooks
    if cost_tracker is not None:
        cost_hooks = make_cost_hooks(cost_tracker)
        hook_manager.register("post_llm", cost_hooks["post_llm"])
        hook_manager.register("post_tool", cost_hooks["post_tool"])
