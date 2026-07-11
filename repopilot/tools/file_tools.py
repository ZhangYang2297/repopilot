"""File operation tools: read_file, write_file, edit_file."""
from __future__ import annotations
from typing import TYPE_CHECKING, Any

from repopilot.tools.base import Tool, TIER_READONLY, TIER_WRITE
from repopilot.tools.result import ToolResult, truncate_text

if TYPE_CHECKING:
    from repopilot.sandbox.base import Sandbox


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a file from the repository with line numbers. Use offset/limit for "
        "large files. Always prefer reading files over guessing their contents. "
        "Line numbers appear as '  12|code here' in the output."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to the repository root.",
            },
            "offset": {
                "type": "integer",
                "description": "0-based line offset to start reading from (default 0).",
                "default": 0,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to read (default 200).",
                "default": 200,
            },
        },
        "required": ["path"],
    }
    tier = TIER_READONLY

    def execute(self, args: dict[str, Any], sandbox: "Sandbox", extra=None) -> ToolResult:
        path = args.get("path", "")
        if not path:
            return ToolResult(error="read_file requires 'path' argument")
        offset = int(args.get("offset", 0))
        limit = int(args.get("limit", 200))
        try:
            result = sandbox.read_file(path, offset=offset, limit=limit)
        except FileNotFoundError as e:
            return ToolResult(error=f"File not found: {path}")
        except PermissionError as e:
            return ToolResult(error=str(e))
        except Exception as e:
            return ToolResult(error=f"{type(e).__name__}: {e}")

        content = result.content
        if result.truncated:
            content += f"\n...[file has {result.total_lines} lines, showing {result.start_line}-{result.start_line + limit - 1}. Use offset={result.start_line + limit - 1} to continue]..."
        return ToolResult(content=content, metadata={"total_lines": result.total_lines, "path": path})


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Write/create a file with the given content, overwriting any existing file. "
        "Use edit_file instead when you want to modify an existing file surgically."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to the repository root.",
            },
            "content": {
                "type": "string",
                "description": "Complete file content to write.",
            },
        },
        "required": ["path", "content"],
    }
    tier = TIER_WRITE

    def execute(self, args: dict[str, Any], sandbox: "Sandbox", extra=None) -> ToolResult:
        path = args.get("path", "")
        content = args.get("content", "")
        if not path:
            return ToolResult(error="write_file requires 'path'")
        try:
            sandbox.write_file(path, content)
        except PermissionError as e:
            return ToolResult(error=str(e))
        except Exception as e:
            return ToolResult(error=f"{type(e).__name__}: {e}")
        lines = content.count("\n") + 1
        return ToolResult(content=f"Wrote {lines} lines to {path}")


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Replace the first occurrence of old_string with new_string in a file. "
        "Returns a unified diff showing the change. Prefer this over write_file "
        "for small, targeted edits. old_string must match exactly (including whitespace)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to the repository root.",
            },
            "old_string": {
                "type": "string",
                "description": "Exact text to find and replace.",
            },
            "new_string": {
                "type": "string",
                "description": "Replacement text.",
            },
        },
        "required": ["path", "old_string", "new_string"],
    }
    tier = TIER_WRITE

    def execute(self, args: dict[str, Any], sandbox: "Sandbox", extra=None) -> ToolResult:
        path = args.get("path", "")
        old = args.get("old_string", "")
        new = args.get("new_string", "")
        if not path:
            return ToolResult(error="edit_file requires 'path'")
        if old == new:
            return ToolResult(error="old_string and new_string are identical; no change needed")
        try:
            diff = sandbox.edit_file(path, old, new)
        except FileNotFoundError:
            return ToolResult(error=f"File not found: {path}")
        except ValueError as e:
            hint = str(e)
            return ToolResult(error=f"Edit failed: {hint}")
        except PermissionError as e:
            return ToolResult(error=str(e))
        except Exception as e:
            return ToolResult(error=f"{type(e).__name__}: {e}")
        truncated_diff = truncate_text(diff, head=500, tail=2000)
        return ToolResult(content=f"Applied edit to {path}:\n{truncated_diff}")
