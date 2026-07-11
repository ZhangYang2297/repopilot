from repopilot.tools.base import Tool, TIER_READONLY, TIER_WRITE, TIER_EXEC, TIER_DANGEROUS
from repopilot.tools.result import ToolResult, truncate_text
from repopilot.tools.registry import ToolRegistry, ToolNotFoundError
from repopilot.tools.file_tools import ReadFileTool, WriteFileTool, EditFileTool
from repopilot.tools.search_tools import GrepTool, GlobTool, ListDirTool, RepoTreeTool


def build_default_registry(permission_engine=None) -> "ToolRegistry":
    """Create a ToolRegistry pre-loaded with all standard tools."""
    reg = ToolRegistry(permission_engine=permission_engine)
    for tool_cls in (
        ReadFileTool, WriteFileTool, EditFileTool,
        GrepTool, GlobTool, ListDirTool, RepoTreeTool,
    ):
        reg.register(tool_cls())
    return reg


__all__ = [
    "Tool", "ToolResult", "ToolRegistry", "ToolNotFoundError",
    "TIER_READONLY", "TIER_WRITE", "TIER_EXEC", "TIER_DANGEROUS",
    "truncate_text",
    "ReadFileTool", "WriteFileTool", "EditFileTool",
    "GrepTool", "GlobTool", "ListDirTool", "RepoTreeTool",
    "build_default_registry",
]
