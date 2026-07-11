"""Meta tools: finish (signals task completion)."""
from __future__ import annotations
from typing import TYPE_CHECKING, Any

from repopilot.tools.base import Tool, TIER_READONLY, AgentFinished
from repopilot.tools.result import ToolResult

if TYPE_CHECKING:
    from repopilot.sandbox.base import Sandbox


class FinishTool(Tool):
    name = "finish"
    description = (
        "Call this tool when you have completed the task and have no more "
        "actions to take. Provide a summary of what was done. If tests were "
        "run and passed, set tests_passed=true. After calling this tool the "
        "session ends."
    )
    parameters = {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "Summary of changes made and final status.",
            },
            "tests_passed": {
                "type": "boolean",
                "description": "Whether tests were run and passed (default true).",
                "default": True,
            },
        },
        "required": ["summary"],
    }
    tier = TIER_READONLY

    def execute(self, args: dict[str, Any], sandbox: "Sandbox", extra=None) -> ToolResult:
        summary = args.get("summary", "Task completed.")
        tests_passed = bool(args.get("tests_passed", True))
        # Raise AgentFinished to break out of the agent loop
        raise AgentFinished(summary=summary, tests_passed=tests_passed)
