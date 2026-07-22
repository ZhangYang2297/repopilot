"""Codex-style human-friendly formatting for tool invocation lines.

Purpose: turn machine-y ``> tool_name(arg=v, ...)`` into readable lines like
``Ran <cmd>``, ``Read <path>``, ``Edited <path> (+12 -3)`` similar to
Codex CLI / Claude Code, without touching the tool implementations.
"""
from __future__ import annotations

from typing import Optional


def _truncate(s: str, n: int = 100) -> str:
    s = str(s)
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def format_tool_line(tool_name: str, args: dict) -> str:
    """Return a Rich-markup string describing the tool call in human language.

    Falls back to ``> tool_name(k=v, ...)`` for unknown tools.
    """
    a = args or {}

    if tool_name == "bash":
        cmd = a.get("command") or a.get("cmd") or ""
        return f"[cyan]Ran[/cyan] [white]{_truncate(cmd, 120)}[/white]"

    if tool_name == "run_python":
        code = a.get("code", "")
        first_line = code.strip().splitlines()[0] if code.strip() else ""
        return f"[cyan]Ran python[/cyan] [white]{_truncate(first_line, 100)}[/white]"

    if tool_name == "read_file":
        path = a.get("path", "")
        start = a.get("start_line") or a.get("start")
        end = a.get("end_line") or a.get("end")
        span = ""
        if start or end:
            span = f" [dim](L{start or 1}-L{end or '?'})[/dim]"
        return f"[cyan]Read[/cyan] [white]{path}[/white]{span}"

    if tool_name == "write_file":
        path = a.get("path", "")
        content = a.get("content", "")
        n = content.count("\n") + 1 if content else 0
        return f"[cyan]Wrote[/cyan] [white]{path}[/white] [dim]({n} lines)[/dim]"

    if tool_name == "edit_file":
        path = a.get("path", "")
        return f"[cyan]Edited[/cyan] [white]{path}[/white]"

    if tool_name == "grep":
        pattern = a.get("pattern", "")
        path = a.get("path", ".")
        return f"[cyan]Searched for[/cyan] [white]{_truncate(pattern, 60)}[/white] [dim]in {path}[/dim]"

    if tool_name == "glob":
        pattern = a.get("pattern", "")
        return f"[cyan]Globbed[/cyan] [white]{pattern}[/white]"

    if tool_name == "list_dir":
        path = a.get("path", ".")
        return f"[cyan]Listed[/cyan] [white]{path}[/white]"

    if tool_name == "get_repo_tree":
        return "[cyan]Read repo tree[/cyan]"

    if tool_name == "finish":
        return "[cyan]Finished[/cyan]"

    # Fallback: keep old machine-y format
    arg_str = ", ".join(f"[dim]{k}=[/dim]{_truncate(v, 40)}" for k, v in a.items())
    return f"[cyan]> {tool_name}[/cyan]([dim]{arg_str}[/dim])"


def format_result_suffix(tool_name: str, args: dict, result_metadata: Optional[dict]) -> str:
    """Extra info appended to the OK line, e.g. ``(+12 -3)`` for edits.

    Reads the metadata contract set by each tool (see repopilot/tools/*).
    Returns an empty string when no interesting metadata is present.
    """
    md = result_metadata or {}
    if tool_name == "edit_file":
        added = md.get("added_lines", 0)
        removed = md.get("removed_lines", 0)
        if added or removed:
            return f" [dim](+{added} -{removed})[/dim]"
        return ""
    if tool_name == "read_file":
        shown = md.get("lines_shown")
        total = md.get("total_lines")
        if shown is not None and total is not None and shown < total:
            return f" [dim]({shown}/{total} lines)[/dim]"
        if total is not None:
            return f" [dim]({total} lines)[/dim]"
        return ""
    if tool_name == "write_file":
        n = md.get("lines_written")
        if n is not None:
            tag = "created" if md.get("created") else "overwrote"
            return f" [dim]({tag}, {n} lines)[/dim]"
        return ""
    if tool_name in ("bash", "run_python"):
        rc = md.get("exit_code")
        if rc is not None and rc != 0:
            return f" [dim](exit {rc})[/dim]"
        return ""
    if tool_name == "grep":
        mc = md.get("match_count")
        fc = md.get("file_count")
        if mc is not None:
            return f" [dim]({mc} matches in {fc or 0} files)[/dim]"
        return ""
    if tool_name == "glob":
        n = md.get("file_count")
        if n is not None:
            return f" [dim]({n} files)[/dim]"
        return ""
    if tool_name == "list_dir":
        d = md.get("dir_count", 0); f = md.get("file_count", 0)
        if d or f:
            return f" [dim]({d} dirs, {f} files)[/dim]"
        return ""
    if tool_name == "get_repo_tree":
        s2 = md.get("source_files"); o = md.get("other_files")
        if s2 is not None:
            return f" [dim]({s2} src, {o or 0} other)[/dim]"
        return ""
    return ""
