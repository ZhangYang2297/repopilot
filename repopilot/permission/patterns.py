"""Dangerous patterns and safe command allow-lists for the Permission Engine.

Security philosophy (mirrors Claude Code / Codex CLI):
  - Safe prefix = TRULY read-only, zero side-effects, no network, no code execution.
  - Anything that can write, execute code, touch the network, or leak env vars
    requires confirmation even when the binary name looks "safe".
  - The Permission Engine is a UX layer (reduces noise for obviously-safe ops).
    Real security comes from the Docker sandbox (cgroups, bind mount scope,
    network isolation).  Patterns here are defense-in-depth.
"""
from __future__ import annotations
import re
from typing import Pattern

# ── Dangerous paths (fnmatch glob patterns) ─────────────────────────
# Write/edit operations targeting these are denied in ALL modes.
# Bash commands that cat/cp/mv/redirect to/from these paths are also flagged.
DANGEROUS_PATH_PATTERNS: list[str] = [
    # SSH keys / known_hosts
    "*/.ssh/*",
    "*id_rsa*",
    "*id_ed25519*",
    "*id_ecdsa*",
    # Secrets / credentials
    "*.env",
    "*.env.*",
    "*credentials*",
    "*.pem",
    "*.key",
    "*aws/credentials*",
    "*kube/config*",
    # Shell init files (backdoor injection)
    "*/.bashrc",
    "*/.zshrc",
    "*/.bash_profile",
    "*/.profile",
    "*/.zprofile",
    "*/.zsh_history",
    "*/.bash_history",
    # System directories
    "/etc/*",
    "/System/*",
    "/boot/*",
    "/usr/*",
    "/bin/*",
    "/sbin/*",
    "/lib/*",
    "/root/*",
    "/proc/*",
    "/sys/*",
    "/dev/*",
    "/var/run/*",
    # Git internals
    "*/.git/*",
    # Windows system
    "C:\\Windows\\*",
    "C:\\Program Files\\*",
    "C:\\Users\\*\\NTUSER.DAT*",
    # CI / deployment secrets
    "*docker/config.json*",
    "*npmrc*",
    "*netrc*",
]

# Regex fragments for paths inside bash commands
_SENSITIVE_PATH_RES = [
    r"~/\.ssh/", r"\.ssh/id_",
    r"~/\.bashrc", r"~/\.zshrc", r"~/\.profile", r"~/\.bash_profile",
    r"/etc/passwd", r"/etc/shadow", r"/etc/sudoers",
    r"~/\.aws/", r"~/\.kube/",
    r"\.env\b", r"\.env\.",
]

# ── Dangerous command patterns (regex) ──────────────────────────────
# Commands matching these are DENIED regardless of mode.
_DANGEROUS_CMD_RES: list[Pattern] = [
    # Destructive filesystem
    re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+(?:--no-preserve-root\s+)?(/\*?|/\s|~\s|\*|~)\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\brm\s+-rf\s+(?:--no-preserve-root\s+)?(/\*?|~)\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bdd\s+if=", re.IGNORECASE),
    re.compile(r":\(\)\s*{\s*:\|:&\s*};:", re.IGNORECASE),          # fork bomb
    re.compile(r">\s*/dev/sd[a-z]\b", re.IGNORECASE),             # raw disk write
    # Privilege escalation
    re.compile(r"^\s*sudo\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*su\s", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\bdoas\b", re.IGNORECASE),
    re.compile(r"\bpkexec\b", re.IGNORECASE),
    # Piped remote code execution
    re.compile(r"\bcurl\b.*\|\s*(?:sudo\s+)?(?:ba)?sh\b", re.IGNORECASE),
    re.compile(r"\bwget\b.*\|\s*(?:sudo\s+)?(?:ba)?sh\b", re.IGNORECASE),
    re.compile(r"curl\b.*\|\s*python", re.IGNORECASE),
    re.compile(r"wget\b.*\|\s*python", re.IGNORECASE),
    re.compile(r"curl\b.*\|\s*perl", re.IGNORECASE),
    re.compile(r"curl\b.*\|\s*ruby", re.IGNORECASE),
    re.compile(r"curl\b.*>\s*/etc/", re.IGNORECASE),
    # Dangerous permission changes
    re.compile(r"\bchmod\s+(-[a-zA-Z]*R[a-zA-Z]*|--recursive)\s+777\b", re.IGNORECASE),
    re.compile(r"\bchown\s+-R\s+(?:root|0:0)\s+/\b", re.IGNORECASE),
    # Destructive git operations
    re.compile(r"^\s*git\s+push\s+(-f|--force)\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*git\s+push\s+.*--force\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*git\s+reset\s+--hard\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*git\s+clean\s+-[a-z]*f", re.IGNORECASE | re.MULTILINE),
    # History wiping
    re.compile(r"\bhistory\s*-c\b", re.IGNORECASE),
    re.compile(r">\s*~/\.bash_history", re.IGNORECASE),
    re.compile(r">\s*~/\.zsh_history", re.IGNORECASE),
    # Crontab / scheduled persistence
    re.compile(r"^\s*crontab\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*(?:systemctl|service|launchctl)\s+(?:start|stop|enable|disable|restart)\b", re.IGNORECASE | re.MULTILINE),
    # Reboot / shutdown
    re.compile(r"^\s*(?:reboot|shutdown|poweroff|halt|init\s+[06])\b", re.IGNORECASE | re.MULTILINE),
    # Encoded / obfuscated execution pipelines
    re.compile(r"\bbase64\b.*\|\s*(?:sh|bash|zsh|python|perl|ruby)\b", re.IGNORECASE),
    re.compile(r"\bxxd\b.*\|\s*(?:sh|bash)\b", re.IGNORECASE),
    re.compile(r"\beval\b.*\$", re.IGNORECASE),
    re.compile(r"^\s*(?:source|\.)\s+~/", re.IGNORECASE | re.MULTILINE),  # source ~/.bashrc etc.
]

# Commands that REQUIRE CONFIRMATION even in "confirm" mode (not auto-allowed).
# These are commands that are technically common but have side-effects or
# touch sensitive data.  The truly-safe list is very short.
REQUIRE_CONFIRM_PREFIXES: frozenset[str] = frozenset({
    "env", "printenv", "export", "set",
    "whoami", "id", "hostname", "uname",
    "curl", "wget",
    "pip", "pip3", "npm", "npx", "yarn", "pnpm", "uv", "poetry",
    "apt", "apt-get", "yum", "dnf", "apk", "brew", "choco", "scoop",
    "ssh", "scp", "sftp", "nc", "netcat", "telnet",
    "docker", "kubectl", "helm",
    "chmod", "chown", "chgrp",
    "kill", "killall", "pkill",
    "python", "python3", "py", "node", "nodejs", "deno", "bun",
    "ruby", "perl", "php",
    "java", "javac", "go", "rustc", "cargo",
})

# Commands that are ALWAYS safe to auto-allow in every mode.
# These must be: read-only, no network, no code execution, no env leakage,
# no side-effects whatsoever.
SAFE_CMD_PREFIXES: list[str] = [
    # File listing / navigation (pure read)
    "ls", "dir", "pwd", "echo", "true", "false",
    # File reading
    "cat", "head", "tail", "less", "more", "wc", "nl", "strings",
    # Search
    "grep", "rg", "ag", "find", "locate",
    # Text processing (read-only filters)
    "sed", "awk", "cut", "sort", "uniq", "tr", "diff", "comm", "paste",
    "column", "rev", "tee",
    # Git read-only
    "git",
    # File metadata
    "file", "stat", "du", "df", "tree", "which", "where", "type",
    # Archive listing (not extraction)
    "tar", "zipinfo", "unzip",
    # Build/test commands considered safe for project context
    "pytest", "py.test",
    "ruff", "flake8", "pylint", "mypy", "black", "ruff",
    "tox", "pre-commit",
]

# Commands that CONTACT THE NETWORK.  When sandbox network=none these are
# denied; in confirm mode they require approval (because they may exfiltrate).
NETWORK_CMD_PATTERNS: list[Pattern] = [
    re.compile(r"^\s*curl\b", re.IGNORECASE),
    re.compile(r"^\s*wget\b", re.IGNORECASE),
    re.compile(r"\bpip\s+install\b", re.IGNORECASE),
    re.compile(r"\bpip3\s+install\b", re.IGNORECASE),
    re.compile(r"\bnpm\s+install\b", re.IGNORECASE),
    re.compile(r"\bnpx\b", re.IGNORECASE),
    re.compile(r"\byarn\b", re.IGNORECASE),
    re.compile(r"\bpnpm\b", re.IGNORECASE),
    re.compile(r"\bapt\s+(?:install|update|upgrade)\b", re.IGNORECASE),
    re.compile(r"\byum\s+(?:install|update)\b", re.IGNORECASE),
    re.compile(r"\bapk\s+(?:add|upgrade)\b", re.IGNORECASE),
    re.compile(r"\bgit\s+(?:clone|fetch|pull|push)\b", re.IGNORECASE),
    re.compile(r"\buv\s+pip\s+install\b", re.IGNORECASE),
    re.compile(r"\bpoetry\s+add\b", re.IGNORECASE),
    re.compile(r"\bbrew\s+install\b", re.IGNORECASE),
    re.compile(r"\bscoop\s+install\b", re.IGNORECASE),
    re.compile(r"\bcargo\s+(?:install|add)\b", re.IGNORECASE),
    re.compile(r"^\s*ssh\b", re.IGNORECASE),
    re.compile(r"^\s*scp\b", re.IGNORECASE),
    re.compile(r"^\s*nc\b", re.IGNORECASE),
    re.compile(r"^\s*telnet\b", re.IGNORECASE),
]

# Flags that make a "safe" command UNSAFE (e.g. python -c / node -e = code exec)
CODE_EXEC_FLAGS = ("-c", "-e", "--eval", "--command", "-m", "-r", "--run")
WRITE_FLAGS = (">", ">>", "|", "tee", "&&", ";")

# Tool classifications
READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "read_file", "grep", "glob", "list_dir", "get_repo_tree",
})

WRITE_TOOLS: frozenset[str] = frozenset({
    "write_file", "edit_file",
})

EXEC_TOOLS: frozenset[str] = frozenset({
    "bash", "exec", "run_python",
})


def _check_dangerous_path_in_cmd(cmd: str) -> str | None:
    """Check if a bash command references a dangerous path (cat ~/.ssh/id_rsa, etc)."""
    cmd_lower = cmd.lower()
    for fragment in _SENSITIVE_PATH_RES:
        if re.search(fragment, cmd_lower):
            return f"References sensitive path: {fragment}"
    return None


def is_dangerous_command(cmd: str) -> str | None:
    """Return reason string if command matches a hard-denied pattern, else None."""
    cmd_stripped = cmd.strip()
    for pat in _DANGEROUS_CMD_RES:
        if pat.search(cmd_stripped):
            return pat.pattern
    # Check for sensitive paths in the command (cat ~/.ssh/id_rsa, etc.)
    dp = _check_dangerous_path_in_cmd(cmd_stripped)
    if dp:
        return dp
    return None


def is_network_command(cmd: str) -> bool:
    cmd_stripped = cmd.strip()
    return any(p.search(cmd_stripped) for p in NETWORK_CMD_PATTERNS)


def _get_first_cmd(cmd: str) -> str:
    """Extract the first command word, stripping env vars and leading whitespace."""
    parts = cmd.strip().split()
    for p in parts:
        if "=" in p and not p.startswith("-"):
            continue
        return p
    return ""


def is_safe_cmd(cmd: str) -> bool:
    """Return True if the command is read-only and safe to auto-allow.

    Unlike the old is_safe_cmd_prefix, this also checks:
    - No redirection/write operators (>, >>, | tee)
    - No code-exec flags (-c, -e) on interpreters
    - No sensitive paths in arguments
    - Not just a prefix match — the whole command must be safe
    """
    cmd_stripped = cmd.strip()
    if not cmd_stripped:
        return False

    first = _get_first_cmd(cmd_stripped)
    if not first:
        return False

    # Strip path: /usr/bin/ls -> ls
    first = first.rsplit("/", 1)[-1] if "/" in first else first

    if first not in SAFE_CMD_PREFIXES:
        return False

    # Block write operators anywhere in command
    for op in (">", ">>", "| tee"):
        if op in cmd_stripped:
            return False

    # Block code-exec flags on interpreters
    for flag in CODE_EXEC_FLAGS:
        if re.search(rf"\s{re.escape(flag)}\s", cmd_stripped):
            # Special case: git diff -c is fine; tar -x is fine; etc.
            # Only block -c/-e on actual interpreters, not git/tar/etc.
            if first in ("python", "python3", "py", "node", "nodejs",
                         "deno", "bun", "ruby", "perl", "bash", "sh", "zsh"):
                return False

    # Block dangerous path references
    if _check_dangerous_path_in_cmd(cmd_stripped):
        return False

    # Block absolute paths outside workspace (starting with /) for read cmds
    # (in Docker mode cwd is /workspace so absolute paths are a smell;
    #  in local mode the _safe_path on tools already blocks this for file tools)
    if re.search(r"\s/(etc|root|proc|sys|usr|boot|bin|sbin|var)/", cmd_stripped):
        return False

    # git subcommands must be read-only
    if first == "git":
        write_git = re.search(r"\b(clone|fetch|pull|push|commit|merge|rebase|reset|checkout|clean|tag|branch\s+-D)\b", cmd_stripped)
        if write_git:
            return False

    # tar must not extract (no -x/-xzf etc when listing only)
    if first == "tar":
        if re.search(r"\s-x", cmd_stripped) or re.search(r"\bc[rx]\b", cmd_stripped):
            return False

    # unzip must not extract (we only allow -l listing)
    if first == "unzip":
        if "-l" not in cmd_stripped:
            return False

    return True


def requires_confirmation(cmd: str) -> bool:
    """Return True if command needs user confirmation (even in confirm mode)."""
    first = _get_first_cmd(cmd.strip())
    if not first:
        return True
    first = first.rsplit("/", 1)[-1] if "/" in first else first
    if first in REQUIRE_CONFIRM_PREFIXES:
        return True
    # Code execution flags on any interpreter
    if first in ("python", "python3", "py", "node", "nodejs", "deno", "bun",
                 "ruby", "perl", "bash", "sh", "zsh", "php"):
        for flag in CODE_EXEC_FLAGS:
            if re.search(rf"\s{re.escape(flag)}\s", cmd):
                return True
    return False
