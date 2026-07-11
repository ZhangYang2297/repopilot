from __future__ import annotations
import abc
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from repopilot.tools.result import ToolResult

if TYPE_CHECKING:
    from repopilot.sandbox.base import Sandbox


class AgentFinished(Exception):
    """Raised by FinishTool to signal the agent loop that the task is complete.

    The loop catches this exception, reports the summary, and exits cleanly.
    """
    def __init__(self, summary: str, tests_passed: bool = True):
        self.summary = summary
        self.tests_passed = tests_passed
        super().__init__(summary)


# Tool tiers: controls permission / auto-approval behaviour
TIER_READONLY = 0   # grep/glob/read/list/tree — never needs approval
TIER_WRITE = 1      # write_file/edit_file — ask in confirm/edit-only
TIER_EXEC = 2       # bash/exec/run_python — ask in confirm, deny in edit-only
TIER_DANGEROUS = 3  # (reserved) network ops, package installs, git push


class Tool(abc.ABC):
    """Abstract base for all agent tools.

    Subclasses must set:
      - name:          Unique tool name (used in function_call).
      - description:   Human-readable description for the LLM.
      - parameters:    JSON Schema dict for the ``parameters`` field.
      - tier:          One of TIER_READONLY/TIER_WRITE/TIER_EXEC/TIER_DANGEROUS.
    """

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    tier: int = TIER_READONLY

    @abc.abstractmethod
    def execute(self, args: dict[str, Any], sandbox: "Sandbox",
                extra: dict[str, Any] | None = None) -> ToolResult:
        """Run the tool and return a ``ToolResult``.

        Args:
            args:    Parsed arguments from the LLM tool_call.
            sandbox: The active Sandbox instance (read/write/exec operations
                     go through this).
            extra:   Optional context (session id, cost tracker, etc.).
        """

    def schema(self) -> dict[str, Any]:
        """Return the OpenAI function-calling schema for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def __repr__(self) -> str:
        return f"<Tool {self.name!r} (tier={self.tier})>"
