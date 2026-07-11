"""Search/navigation tools: grep, glob, list_dir, get_repo_tree."""
from __future__ import annotations
from typing import TYPE_CHECKING, Any

from repopilot.tools.base import Tool, TIER_READONLY
from repopilot.tools.result import ToolResult, truncate_text

if TYPE_CHECKING:
    from repopilot.sandbox.base import Sandbox


class GrepTool(Tool):
    name = "grep"
    description = (
        "Search for a regex pattern in all files in the repository. "
        "Returns matching lines with file paths and line numbers. "
        "Use this to find function definitions, variable references, imports, etc."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regular expression pattern to search for.",
            },
            "glob": {
                "type": "string",
                "description": "Optional glob pattern to filter files (e.g. '*.py').",
            },
            "ignore_case": {
                "type": "boolean",
                "description": "Case-insensitive search (default false).",
                "default": False,
            },
        },
        "required": ["pattern"],
    }
    tier = TIER_READONLY
    MAX_RESULTS = 100

    def execute(self, args: dict[str, Any], sandbox: "Sandbox", extra=None) -> ToolResult:
        pattern = args.get("pattern", "")
        if not pattern:
            return ToolResult(error="grep requires 'pattern'")
        glob_filter = args.get("glob") or None
        ignore_case = bool(args.get("ignore_case", False))
        try:
            matches = sandbox.grep(pattern, glob_filter=glob_filter, ignore_case=ignore_case)
        except ValueError as e:
            return ToolResult(error=str(e))
        except Exception as e:
            return ToolResult(error=f"{type(e).__name__}: {e}")

        if not matches:
            return ToolResult(content=f"No matches found for pattern: {pattern!r}")

        lines = [f"Found {len(matches)} matches for {pattern!r}:\n"]
        shown = 0
        for m in matches:
            if shown >= self.MAX_RESULTS:
                lines.append(f"\n... and {len(matches) - shown} more matches (refine your pattern)")
                break
            lines.append(f"{m.file}:{m.line_no}: {m.content}")
            shown += 1
        return ToolResult(
            content=truncate_text("\n".join(lines), head=500, tail=3000),
            metadata={"match_count": len(matches)},
        )


class GlobTool(Tool):
    name = "glob"
    description = (
        "Find files matching a glob pattern (e.g. '**/*.py', 'src/*.ts'). "
        "Use this to discover file locations before reading them."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern. Use '**/' prefix for recursive search.",
            },
        },
        "required": ["pattern"],
    }
    tier = TIER_READONLY
    MAX_RESULTS = 200

    def execute(self, args: dict[str, Any], sandbox: "Sandbox", extra=None) -> ToolResult:
        pattern = args.get("pattern", "")
        if not pattern:
            return ToolResult(error="glob requires 'pattern'")
        try:
            files = sandbox.glob(pattern)
        except Exception as e:
            return ToolResult(error=f"{type(e).__name__}: {e}")
        if not files:
            return ToolResult(content=f"No files match pattern: {pattern!r}")
        shown = files[:self.MAX_RESULTS]
        lines = [f"Found {len(files)} files matching {pattern!r}:\n"]
        for f in shown:
            lines.append(f"  {f}")
        if len(files) > self.MAX_RESULTS:
            lines.append(f"\n... and {len(files) - self.MAX_RESULTS} more files")
        return ToolResult(content="\n".join(lines), metadata={"file_count": len(files)})


class ListDirTool(Tool):
    name = "list_dir"
    description = (
        "List directory contents as a tree. Use '.' for the repo root. "
        "Shows directories with trailing '/' and files without."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path relative to repo root (default '.').",
                "default": ".",
            },
            "max_depth": {
                "type": "integer",
                "description": "Maximum recursion depth (default 2).",
                "default": 2,
            },
        },
    }
    tier = TIER_READONLY

    def execute(self, args: dict[str, Any], sandbox: "Sandbox", extra=None) -> ToolResult:
        path = args.get("path", ".")
        depth = int(args.get("max_depth", 2))
        try:
            tree = sandbox.list_dir(path, max_depth=depth)
        except PermissionError as e:
            return ToolResult(error=str(e))
        except Exception as e:
            return ToolResult(error=f"{type(e).__name__}: {e}")
        if not tree:
            return ToolResult(content=f"Directory not found or empty: {path}")
        lines = [f"Directory: {path}\n"]
        self._render(tree, lines, prefix="")
        return ToolResult(content="\n".join(lines))

    def _render(self, tree: dict, lines: list[str], prefix: str) -> None:
        items = list(tree.items())
        for i, (name, children) in enumerate(items):
            is_last = i == len(items) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{name}")
            if children is not None and isinstance(children, dict):
                extension = "    " if is_last else "│   "
                self._render(children, lines, prefix + extension)


class RepoTreeTool(Tool):
    name = "get_repo_tree"
    description = (
        "Get a high-level overview of the repository structure. "
        "Call this early in a task to understand the codebase layout."
    )
    parameters = {
        "type": "object",
        "properties": {
            "max_tokens": {
                "type": "integer",
                "description": "Approximate token budget for the output (default 4000).",
                "default": 4000,
            },
        },
    }
    tier = TIER_READONLY

    def execute(self, args: dict[str, Any], sandbox: "Sandbox", extra=None) -> ToolResult:
        max_tokens = int(args.get("max_tokens", 4000))
        try:
            tree = sandbox.get_repo_tree(max_tokens=max_tokens)
        except Exception as e:
            return ToolResult(error=f"{type(e).__name__}: {e}")
        return ToolResult(content=tree)
