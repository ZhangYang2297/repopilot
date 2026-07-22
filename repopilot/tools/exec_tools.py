"""Execution tools: bash and run_python."""
from __future__ import annotations
import os as _os
import platform as _platform
import uuid as _uuid
from typing import TYPE_CHECKING, Any

from repopilot.tools.base import Tool, TIER_EXEC
from repopilot.tools.errors import ToolErrorCode
from repopilot.tools.result import ToolResult, truncate_text, error_result
from repopilot.sandbox.command_guard import scan_command, scan_python_code

if TYPE_CHECKING:
    from repopilot.sandbox.base import Sandbox

_IS_WINDOWS = _platform.system() == "Windows"


class BashTool(Tool):
    name = "bash"
    description = (
        "Execute a shell command in the repository working directory. "
        "Use this to run tests, builds, linters, pip install, git commands, or any CLI tool. "
        "Output is truncated (head 2000 + tail 5000 chars). "
        "For Python scripts prefer run_python; for project commands (pytest, npm, git, pip) use bash.\n\n"
        "IMPORTANT PLATFORM NOTES:\n"
        f"- This system is {'Windows' if _IS_WINDOWS else 'Linux/macOS'}. Use the appropriate commands.\n"
        "- On Windows: use `dir` not `ls`, `type` not `cat`, `del` not `rm`, `copy` not `cp`, "
        "`move` not `mv`, `findstr` not `grep`. Avoid Unix-only commands like `pwd`, `chmod`, `which`, `/dev/null`."
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
                "description": "Timeout in seconds (default 120, max 600). "
                               "Use 300 for full test suites, 600 for npm/pip install.",
                "default": 120,
            },
            "cwd": {
                "type": "string",
                "description": "Working directory relative to repo root (optional).",
            },
        },
        "required": ["command"],
    }
    tier = TIER_EXEC

    DEFAULT_TIMEOUT = 120
    MAX_TIMEOUT = 600

    def execute(self, args: dict[str, Any], sandbox: "Sandbox", extra=None) -> ToolResult:
        cmd = args.get("command", "").strip()
        if not cmd:
            return error_result("bash requires 'command' argument", ToolErrorCode.INVALID_ARGS)
        try:
            timeout = min(int(args.get("timeout", self.DEFAULT_TIMEOUT)), self.MAX_TIMEOUT)
        except (TypeError, ValueError):
            return error_result(
                f"bash 'timeout' must be an integer, got {args.get('timeout')!r}",
                ToolErrorCode.INVALID_ARGS,
            )
        cwd = args.get("cwd")

        blocked, reason = scan_command(cmd)
        if blocked:
            return error_result(f"Refused dangerous command ({reason})", ToolErrorCode.PERMISSION)

        # Auto-translate common Unix commands on Windows (best-effort)
        if _IS_WINDOWS:
            cmd = _windows_cmd_fix(cmd)

        try:
            result = sandbox.exec(cmd, timeout=timeout, cwd=cwd)
        except PermissionError as e:
            return error_result(str(e), ToolErrorCode.PERMISSION)
        except Exception as e:
            return error_result(f"{type(e).__name__}: {e}", ToolErrorCode.SANDBOX)

        output = result.truncated(head=2000, tail=5000)
        meta = {
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "duration_ms": result.duration_ms,
            "stdout_bytes": len(result.stdout or ""),
        }
        if getattr(result, "interrupted", False):
            output += "\n[command interrupted by user (Ctrl-C)]"
            return ToolResult(content=output, error="Interrupted by user",
                              error_code=ToolErrorCode.INTERRUPTED.value, retryable=False, metadata=meta,
                              duration_ms=result.duration_ms)
        if result.timed_out:
            output += f"\n[command timed out after {timeout}s — try increasing timeout parameter]"
            return ToolResult(content=output, error=f"Command timed out after {timeout}s",
                              error_code=ToolErrorCode.TIMEOUT.value, retryable=True, metadata=meta,
                              duration_ms=result.duration_ms)
        if result.exit_code != 0:
            return ToolResult(content=output, error=f"Command exited with code {result.exit_code}",
                              error_code=ToolErrorCode.EXEC_FAILED.value, retryable=False, metadata=meta,
                              duration_ms=result.duration_ms)
        return ToolResult(content=output, metadata=meta, duration_ms=result.duration_ms)


def _windows_cmd_fix(cmd: str) -> str:
    """Best-effort translation of common Unix commands to Windows equivalents.
    Only applies to the first word in a pipeline/chain to avoid breaking things."""
    # Strip leading/trailing whitespace
    stripped = cmd.strip()
    # Simple translations for common patterns (not full shell translation)
    translations = {
        "ls": "dir",
        "cat": "type",
        "pwd": "cd",
        "cp": "copy",
        "mv": "move",
        "which": "where",
        "clear": "cls",
        "diff": "fc",
    }
    if any(kw in stripped for kw in ["&&", "||", "|", ";"]):
        return cmd
    parts = stripped.split(None, 2)
    if not parts:
        return cmd
    first = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""
    rest2 = parts[2] if len(parts) > 2 else ""
    if any(stripped.startswith(p) for p in ("python", "py", "pip", "git", "npm", "node", "pytest", "uv", "dir", "type", "del", "copy", "move", "rmdir", "findstr", "cls", "cd", "set", "echo", "where", "fc")):
        return cmd
    if first == "rm" and rest.startswith("-rf"):
        return f"rmdir /s /q {rest2}".strip()
    if first == "rm" and rest.startswith("-r"):
        return f"rmdir /s /q {rest2}".strip()
    if first == "rm":
        return f"del /q {rest}".strip()
    if first == "touch":
        return f"type nul > {rest}".strip()
    if first == "grep":
        return f"findstr {rest}".strip()
    if first == "export" and "=" in rest:
        return f"set {rest}".strip()
    if first in translations:
        return f"{translations[first]} {rest}".strip()
    return cmd


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
                "description": "Timeout in seconds (default 30, max 300).",
                "default": 30,
            },
        },
        "required": ["code"],
    }
    tier = TIER_EXEC

    DEFAULT_TIMEOUT = 30
    MAX_TIMEOUT = 300

    def execute(self, args: dict[str, Any], sandbox: "Sandbox", extra=None) -> ToolResult:
        code = args.get("code", "")
        if not code:
            return error_result("run_python requires 'code' argument", ToolErrorCode.INVALID_ARGS)
        blocked, reason = scan_python_code(code)
        if blocked:
            return error_result(f"Refused dangerous code ({reason})", ToolErrorCode.PERMISSION)
        try:
            timeout = min(int(args.get("timeout", self.DEFAULT_TIMEOUT)), self.MAX_TIMEOUT)
        except (TypeError, ValueError):
            return error_result(
                f"run_python 'timeout' must be an integer, got {args.get('timeout')!r}",
                ToolErrorCode.INVALID_ARGS,
            )

        tmp_name = f"_runpy_{_uuid.uuid4().hex[:8]}.py"
        tmp_host_path = None
        try:
            sandbox.write_file(tmp_name, code)
            tmp_host_path = sandbox.repo_path / tmp_name
        except Exception as e:
            return error_result(f"Failed to write temp script: {e}", ToolErrorCode.SANDBOX)
        try:
            result = sandbox.exec(f"python {tmp_name}", timeout=timeout)
        except PermissionError as e:
            return error_result(str(e), ToolErrorCode.PERMISSION)
        except Exception as e:
            return error_result(f"{type(e).__name__}: {e}", ToolErrorCode.SANDBOX)
        finally:
            if tmp_host_path and tmp_host_path.exists():
                try:
                    tmp_host_path.unlink()
                except OSError:
                    pass
            try:
                if hasattr(sandbox, "_docker_exec"):
                    sandbox._docker_exec(f"rm -f /workspace/{tmp_name}", timeout=5)
            except Exception:
                pass

        output = result.truncated(head=2000, tail=5000)
        meta = {
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "duration_ms": result.duration_ms,
            "stdout_bytes": len(result.stdout or ""),
        }
        if getattr(result, "interrupted", False):
            output += "\n[script interrupted by user (Ctrl-C)]"
            return ToolResult(content=output, error="Interrupted by user",
                              error_code=ToolErrorCode.INTERRUPTED.value, retryable=False, metadata=meta,
                              duration_ms=result.duration_ms)
        if result.timed_out:
            output += f"\n[script timed out after {timeout}s]"
            return ToolResult(content=output, error=f"Script timed out after {timeout}s",
                              error_code=ToolErrorCode.TIMEOUT.value, retryable=True, metadata=meta,
                              duration_ms=result.duration_ms)
        if result.exit_code != 0:
            return ToolResult(content=output, error=f"Script exited with code {result.exit_code}",
                              error_code=ToolErrorCode.EXEC_FAILED.value, metadata=meta,
                              duration_ms=result.duration_ms)
        return ToolResult(content=output, metadata=meta, duration_ms=result.duration_ms)
