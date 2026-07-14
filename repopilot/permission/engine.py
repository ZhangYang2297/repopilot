"""Permission engine — decides whether a tool call is allowed, denied, or
needs user confirmation.

Security model (aligned with Claude Code / Codex CLI):
  - Read-only tools (read_file, grep, glob, list_dir, get_repo_tree) are
    always allowed.
  - Dangerous-pattern matches are ALWAYS denied (rm -rf /, sudo, curl|sh,
    writes to ~/.ssh, etc.) regardless of approval mode.
  - In "auto" mode, non-dangerous exec/write tools are allowed (the user
    explicitly trusts the agent).  Network commands are still subject to
    the network_enabled flag.
  - In "confirm" mode (default):
      * Truly safe read commands (ls, cat project files, pytest, git status)
        auto-allow via is_safe_cmd().
      * Everything else (python/node, curl, pip install, chmod, any code
        execution, env vars dump) requires confirmation.
  - In "edit-only" mode: exec tools are denied, writes require confirmation.
  - In "deny" mode: only read-only tools are allowed.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import fnmatch
from pathlib import Path

from repopilot.permission.patterns import (
    DANGEROUS_PATH_PATTERNS,
    READ_ONLY_TOOLS,
    WRITE_TOOLS,
    EXEC_TOOLS,
    is_dangerous_command,
    is_network_command,
    is_safe_cmd,
    requires_confirmation,
)


@dataclass
class PermissionDecision:
    """Result of a permission check.

    action:
        - "allow": execute immediately
        - "deny":  refuse execution, return error to agent
        - "ask":   pause and prompt the user
    """
    action: str  # "allow" | "deny" | "ask"
    reason: str = ""

    def __post_init__(self):
        if self.action not in ("allow", "deny", "ask"):
            raise ValueError(f"Invalid permission action: {self.action!r}")


ALLOW = PermissionDecision("allow")


class PermissionEngine:
    """Policy engine for tool approval.

    Modes:
        auto:       Execute everything that does not match the blacklist.
                    Network commands respect network_enabled flag.
        confirm:    Read-only & truly-safe commands auto-allow; all code
                    execution, network access, env inspection, and writes
                    ask the user.  Blacklist always deny.
        edit-only:  Read-only auto-allow; write operations ask; exec → deny.
        deny:       Read-only auto-allow; everything else deny.
    """

    VALID_MODES = ("auto", "confirm", "edit-only", "deny")

    def __init__(self, mode: str = "confirm", network_enabled: bool = True):
        if mode not in self.VALID_MODES:
            raise ValueError(f"Invalid approval mode: {mode!r}. "
                             f"Must be one of {self.VALID_MODES}")
        self.mode = mode
        self.network_enabled = network_enabled
        self._always_allow: set[tuple[str, str]] = set()

    # ── public API ──────────────────────────────────────────────────
    def check_tool(self, tool_name: str, args: dict) -> PermissionDecision:
        # 1. Read-only tools are always allowed
        if tool_name in READ_ONLY_TOOLS:
            return ALLOW

        # 2. Check session "always allow" memory
        arg_key = self._arg_signature(tool_name, args)
        if (tool_name, arg_key) in self._always_allow:
            return ALLOW

        cmd = args.get("command", "") or args.get("cmd", "")
        path = args.get("path", "") or args.get("file", "") or args.get("file_path", "")

        # 3. Dangerous path check for write tools
        if tool_name in WRITE_TOOLS and path:
            danger = self._check_dangerous_path(path)
            if danger:
                return PermissionDecision("deny", f"Access denied: {danger}")

        # 4. Exec tool checks
        if tool_name in EXEC_TOOLS and cmd:
            # 4a. Hard deny: dangerous commands (rm -rf /, sudo, curl|sh, etc.)
            bad = is_dangerous_command(cmd)
            if bad:
                return PermissionDecision("deny", f"Command matches dangerous pattern")

            # 4b. Hard deny: network commands when sandbox is offline
            if not self.network_enabled and is_network_command(cmd):
                return PermissionDecision(
                    "deny",
                    "Network command blocked: sandbox network is disabled",
                )

            # 4c. Hard deny: exec commands that read/write dangerous paths
            dp = self._check_dangerous_path_in_cmd(cmd)
            if dp:
                return PermissionDecision("deny", dp)

        # 5. Mode-based decision
        if self.mode == "auto":
            return ALLOW

        if self.mode == "deny":
            return PermissionDecision("deny", f"Tool {tool_name!r} denied (deny mode)")

        if self.mode == "edit-only":
            if tool_name in EXEC_TOOLS:
                return PermissionDecision("deny", f"Exec tool {tool_name!r} denied (edit-only mode)")
            if tool_name in WRITE_TOOLS:
                return PermissionDecision("ask", f"Write: {tool_name} {self._summarize_args(args)}")
            return ALLOW

        # confirm mode (default)
        if tool_name in EXEC_TOOLS and cmd:
            if is_safe_cmd(cmd):
                return ALLOW
            return PermissionDecision("ask", f"Command: {self._truncate(cmd, 80)}")
        if tool_name in WRITE_TOOLS:
            return PermissionDecision("ask", f"Write: {tool_name} {self._summarize_args(args)}")
        return ALLOW

    def remember_always(self, tool_name: str, args: dict) -> None:
        self._always_allow.add((tool_name, self._arg_signature(tool_name, args)))

    def reset_memory(self) -> None:
        self._always_allow.clear()

    # ── internal helpers ────────────────────────────────────────────
    def _check_dangerous_path(self, path_str: str) -> Optional[str]:
        expanded = Path(path_str).expanduser()
        resolved_str = str(expanded).replace("\\", "/")
        for pattern in DANGEROUS_PATH_PATTERNS:
            p_norm = pattern.replace("\\", "/")
            if fnmatch.fnmatch(resolved_str, p_norm) or resolved_str.startswith(p_norm.rstrip("*")):
                return f"Path {path_str!r} matches dangerous pattern {pattern!r}"
        return None

    def _check_dangerous_path_in_cmd(self, cmd: str) -> Optional[str]:
        """Check if a bash command references dangerous paths."""
        from repopilot.permission.patterns import _SENSITIVE_PATH_RES
        import re as _re
        cmd_lower = cmd.lower()
        for fragment in _SENSITIVE_PATH_RES:
            if _re.search(fragment, cmd_lower):
                return f"Command references sensitive path matching {fragment!r}"
        # Also: explicit reads/writes to absolute system paths
        if _re.search(r"\s/(etc|root|proc|sys|boot|bin|sbin)/\S+", cmd):
            return "Command accesses system directory"
        # Parent directory traversal out of workspace
        if _re.search(r"(?:cat|type|more|less|head|tail|cat|open|read)\s+\.\.[/\\]", cmd) or _re.search(r"[><>]\s*\.\.[/\\]", cmd):
            return "Command references path outside workspace (../)"
        return None

    @staticmethod
    def _arg_signature(tool_name: str, args: dict) -> str:
        if "path" in args:
            return f"path={args['path']}"
        if "file" in args:
            return f"path={args['file']}"
        if "command" in args or "cmd" in args:
            cmd = args.get("command", "") or args.get("cmd", "")
            first = cmd.strip().split()[0] if cmd.strip() else ""
            return f"cmd={first}"
        return ""

    @staticmethod
    def _summarize_args(args: dict) -> str:
        parts = []
        for k in ("path", "file", "file_path"):
            if k in args:
                parts.append(str(args[k]))
                break
        if "command" in args:
            parts.append(PermissionEngine._truncate(args["command"], 60))
        return " ".join(parts) if parts else ""

    @staticmethod
    def _truncate(s: str, n: int) -> str:
        s = s.replace("\n", " ")
        return s[:n] + ("..." if len(s) > n else "")


