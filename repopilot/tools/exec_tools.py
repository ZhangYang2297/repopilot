"""Execution tools: bash and run_python."""
from __future__ import annotations
import os as _os
import uuid as _uuid
from typing import TYPE_CHECKING, Any

from repopilot.tools.base import Tool, TIER_EXEC
from repopilot.tools.result import ToolResult, truncate_text

if TYPE_CHECKING:
    from repopilot.sandbox.base import Sandbox


class BashTool(Tool):
    name = "bash"
    description = (
        "Execute a shell command in the repository working directory. "
        "Use this to run tests, builds, linters, git commands, or any CLI tool. "
        "Output is truncated (head 500 + tail 1500 chars). "
        "For Python scripts prefer run_python; for project commands (pytest, npm, git) use bash."
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
        "The code is written to a temporary .py file in the workspace root and "
        "executed with 'python tmp.py'. Multiline code works correctly. "
        "Use this for quick computations, data inspection, or testing Python APIs."
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

        tmp_name = f"_runpy_{_uuid.uuid4().hex[:8]}.py"
        try:
            sandbox.write_file(tmp_name, code)
        except Exception as e:
            return ToolResult(error=f"Failed to write temp script: {e}")
        try:
            result = sandbox.exec(f"python {tmp_name}", timeout=timeout)
        except PermissionError as e:
            return ToolResult(error=str(e))
        except Exception as e:
            return ToolResult(error=f"{type(e).__name__}: {e}")
        finally:
            try:
                rm_cmd = "del " + tmp_name if _os.name == "nt" else "rm -f " + tmp_name
                sandbox.exec(rm_cmd, timeout=5)
            except Exception:
                pass

        output = result.truncated(head=500, tail=2000)
        meta = {
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "duration_ms": result.duration_ms,
        }
        if result.timed_out:
            output += f"\n[script timed out after {timeout}s]"
        return ToolResult(content=output, metadata=meta)
