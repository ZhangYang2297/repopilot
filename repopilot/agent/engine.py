"""Shared execution core for all RepoPilot agent entry points."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from repopilot.agent.context import ContextManager
from repopilot.agent.parser import ParsedResponse, parse_response
from repopilot.hooks.manager import HookManager
from repopilot.llm.service import LLMService, Tier
from repopilot.permission.engine import PermissionEngine
from repopilot.sandbox.base import Sandbox
from repopilot.tools.base import ApprovalRequired, Tool
from repopilot.tools.registry import ToolRegistry
from repopilot.tools.result import ToolResult


@dataclass
class StepResult:
    """Normalized result of one LLM decision step."""

    status: str
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    parsed: Optional[ParsedResponse] = None
    raw_response: Any = None


ApprovalHandler = Callable[[ApprovalRequired], bool]


class AgentLoopCore:
    """UI-agnostic LLM decision and tool execution core."""

    def __init__(
        self,
        llm: LLMService,
        sandbox: Sandbox,
        permission_engine: Optional[PermissionEngine] = None,
        tools: Optional[list[Tool]] = None,
        hooks: Optional[HookManager] = None,
        stream_callback: Optional[Callable] = None,
        verbose: bool = False,
        registry: Optional[ToolRegistry] = None,
    ) -> None:
        self.llm = llm
        self.sandbox = sandbox
        self.hooks = hooks or HookManager()
        self.stream_callback = stream_callback
        self.verbose = verbose
        self.registry = registry or ToolRegistry(
            permission_engine or PermissionEngine(mode="auto")
        )
        if registry is None:
            if tools:
                for tool in tools:
                    self.registry.register(tool)
            else:
                self._register_default_tools()
        self.last_duration_ms = 0

    def _register_default_tools(self) -> None:
        from repopilot.tools.exec_tools import BashTool, RunPythonTool
        from repopilot.tools.file_tools import EditFileTool, ReadFileTool, WriteFileTool
        from repopilot.tools.meta_tools import FinishTool
        from repopilot.tools.search_tools import GlobTool, GrepTool, ListDirTool, RepoTreeTool

        for tool_class in (
            ReadFileTool, WriteFileTool, EditFileTool, GrepTool, GlobTool,
            ListDirTool, RepoTreeTool, BashTool, RunPythonTool, FinishTool,
        ):
            self.registry.register(tool_class())

    @property
    def tool_schemas(self) -> list[dict]:
        return self.registry.schemas()

    def execute_step(
        self,
        context: ContextManager,
        tool_schemas: Optional[list[dict]] = None,
        temperature: float = 0.2,
    ) -> StepResult:
        """Call the LLM once and normalize parsing for every entry point."""
        response = self.llm.chat(
            messages=context.build_messages(),
            tools=tool_schemas or self.tool_schemas,
            tier=Tier.DEFAULT,
            temperature=temperature,
        )
        return self.normalize_response(response)

    def normalize_response(self, response: Any) -> StepResult:
        """Normalize an LLM response, including responses supplied by hooks."""
        text = response.content or ""
        parsed = parse_response(
            content=text,
            tool_calls=response.tool_calls,
            finish_reason="stop",
        )
        if parsed.is_tool_call and parsed.tool_calls:
            status = "tool_call"
        elif parsed.is_finish:
            status = "finish"
        else:
            status = "text"
        return StepResult(
            status=status,
            text=text,
            tool_calls=parsed.tool_calls or [],
            usage=response.usage or {},
            parsed=parsed,
            raw_response=response,
        )

    def execute_tool(
        self,
        tool_name: str,
        tool_args: dict,
        call_id: str = "",
        approval_handler: Optional[ApprovalHandler] = None,
    ) -> ToolResult:
        """Execute one tool with shared timing and approval retry semantics."""
        started_at = time.perf_counter()
        try:
            try:
                return self.registry.execute(tool_name, tool_args, self.sandbox)
            except ApprovalRequired as approval:
                if approval_handler is None or not approval_handler(approval):
                    return ToolResult(error=f"User denied: {approval.reason}")
                self.registry.set_approval_callback(lambda *_args, **_kwargs: True)
                try:
                    return self.registry.execute(tool_name, tool_args, self.sandbox)
                finally:
                    self.registry.set_approval_callback(None)
        finally:
            self.last_duration_ms = int((time.perf_counter() - started_at) * 1000)
