"""Execution tools: bash and run_python."""
from __future__ import annotations
from typing import TYPE_CHECKING, Any

from repopilot.tools.base import Tool, TIER_EXEC, TIER_DANGEROUS
from repopilot.tools.result import ToolResult, truncate_text

if TYPE_CHECKING:
    from repopilot.sandbox.base import Sandbox


def _shquote_sh(s: str) -> str:
    """Single-quote a string for POSIX sh (used in Docker sandbox)."""
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _shquote_cmd(s: str) -> str:
    """Double-quote a string for Windows cmd.exe."""
    return '"' + s.replace('"', '""') + '"'


class BashTool(Tool):
    name = "bash"
    description = (
        "Execute a shell command in the repository working directory. "
        "Use this to run tests, builds, linters, git commands, or any CLI tool. "
        "Output is truncated (head 500 + tail 1500 chars). "
        "For Python scripts use run_python; for project commands (pytest, npm, git) use bash."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 30, max 120).",
                "default": 30,
            },
            "cwd": {
                "type": "string",
                "description": "Working directory relative to repo root (optional).",
            },
        },
        "required": ["command"],
    }
    tier = TIER_EXEC

    DEFAULT_TIMEOUT = 30
    MAX_TIMEOUT = 120

    def execute(self, args: dict[str, Any], sandbox: "Sandbox", extra=None) -> ToolResult:
        cmd = args.get("command", "").strip()
        if not cmd:
            return ToolResult(error="bash requires 'command' argument")
        timeout = min(int(args.get("timeout", self.DEFAULT_TIMEOUT)), self.MAX_TIMEOUT)
        cwd = args.get("cwd")

        try:
            result = sandbox.exec(cmd, timeout=timeout, cwd=cwd)
        except PermissionError as e:
            return ToolResult(error=str(e))
        except Exception as e:
            return ToolResult(error=f"{type(e).__name__}: {e}")

        output = result.truncated(head=500, tail=1500)
        meta = {
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "duration_ms": result.duration_ms,
        }
        if result.timed_out:
            output += f"\n[command timed out after {timeout}s]"
        return ToolResult(content=output, metadata=meta)


class RunPythonTool(Tool):
    name = "run_python"
    description = (
        "Execute Python code in the sandbox and return stdout/stderr. "
        "The code runs with 'python -c' in the repo root. "
        "Use this for quick computations, data inspection, testing Python APIs. "
        "Do NOT use this for project commands like pytest — use bash for those."
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python source code to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 10, max 60).",
                "default": 10,
            },
        },
        "required": ["code"],
    }
    tier = TIER_EXEC

    DEFAULT_TIMEOUT = 10
    MAX_TIMEOUT = 60

    def execute(self, args: dict[str, Any], sandbox: "Sandbox", extra=None) -> ToolResult:
        code = args.get("code", "")
        if not code:
            return ToolResult(error="run_python requires 'code' argument")
        timeout = min(int(args.get("timeout", self.DEFAULT_TIMEOUT)), self.MAX_TIMEOUT)

        # Use python -c with platform-appropriate quoting
        # LocalSandbox on Windows uses cmd.exe, DockerSandbox uses sh
        import platform
        is_windows = platform.system() == "Windows"
        # Heuristic: if sandbox has _container attr it's Docker (sh), else local
        is_docker = hasattr(sandbox, "_container")
        if is_docker or not is_windows:
            cmd = f"python -c {_shquote_sh(code)}"
        else:
            cmd = f'python -c {_shquote_cmd(code)}'

        try:
            result = sandbox.exec(cmd, timeout=timeout)
        except PermissionError as e:
            return ToolResult(error=str(e))
        except Exception as e:
            return ToolResult(error=f"{type(e).__name__}: {e}")

        output = result.truncated(head=500, tail=2000)
        meta = {
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "duration_ms": result.duration_ms,
        }
        if result.timed_out:
            output += f"\n[script timed out after {timeout}s]"
        return ToolResult(content=output, metadata=meta)
