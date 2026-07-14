"""ToolRegistry — registers tools, produces LLM schemas, dispatches execution
through the permission engine."""
from __future__ import annotations
from typing import Any, TYPE_CHECKING

from repopilot.tools.base import Tool, TIER_READONLY, AgentFinished, ApprovalRequired
from repopilot.tools.result import ToolResult

if TYPE_CHECKING:
    from repopilot.sandbox.base import Sandbox
    from repopilot.permission.engine import PermissionEngine


class ToolNotFoundError(KeyError):
    """Raised when a tool name is not registered."""


class ToolRegistry:
    """Central registry of available tools.

    Responsibilities:
      - Register / deregister Tool instances.
      - Emit the list of OpenAI function schemas for the LLM call.
      - Dispatch tool calls through the PermissionEngine before execution.
      - Route the call to the correct Tool.execute() with the active Sandbox.
    """

    def __init__(self, permission_engine: "PermissionEngine | None" = None,
                 approval_callback: Any = None):
        self._tools: dict[str, Tool] = {}
        self._permission = permission_engine
        self._approval_callback = approval_callback

    # ── registration ──────────────────────────────────────────
    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("Tool must have a name")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise ToolNotFoundError(f"Unknown tool: {name!r}. Available: {list(self._tools.keys())}")
        return self._tools[name]

    def has(self, name: str) -> bool:
        return name in self._tools

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def tool_names(self) -> list[str]:
        return list(self._tools.keys())

    def set_permission_engine(self, pe: "PermissionEngine") -> None:
        self._permission = pe

    def set_approval_callback(self, cb) -> None:
        """Set a callable(tool_name, args, reason) -> bool that decides approval."""
        self._approval_callback = cb

    # ── LLM schemas ───────────────────────────────────────────
    def schemas(self) -> list[dict[str, Any]]:
        """Return OpenAI-compatible function schemas for all registered tools."""
        return [tool.schema() for tool in self._tools.values()]

    # ── execution ─────────────────────────────────────────────
    def execute(
        self,
        name: str,
        args: dict[str, Any],
        sandbox: "Sandbox",
        extra: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Check permission then dispatch to tool.execute().

        Returns:
            ToolResult with content or error.  On permission denied the
            result has ``error`` set to the denial reason so the LLM can
            see why the call was rejected.

        Raises:
            ApprovalRequired: when the permission engine returns "ask" and
                no approval_callback is set (or the callback is handled by
                the REPL which will re-dispatch after prompting).
            AgentFinished: from the finish tool.
        """
        try:
            tool = self.get(name)
        except ToolNotFoundError as e:
            return ToolResult(error=str(e))

        # Permission check
        if self._permission is not None:
            decision = self._permission.check_tool(name, args or {})
            if decision.action == "deny":
                return ToolResult(error=f"Permission denied: {decision.reason}")
            if decision.action == "ask":
                # If an approval callback is set (non-interactive mode, e.g. chat cmd),
                # use it; otherwise raise to let the REPL handle interactive prompt.
                if self._approval_callback is not None:
                    approved = self._approval_callback(name, args or {}, decision.reason)
                    if not approved:
                        return ToolResult(error=f"User denied: {decision.reason}")
                    # User approved — proceed to execute
                else:
                    raise ApprovalRequired(name, args or {}, decision.reason)
            # action == "allow" → proceed

        # Actual execution
        try:
            result = tool.execute(args or {}, sandbox, extra=extra)
        except AgentFinished:
            raise  # AgentFinished is a control-flow signal, not an error
        except Exception as exc:
            result = ToolResult(error=f"{type(exc).__name__}: {exc}")
        return result
