"""Dangerous-command detector for the local sandbox.

Because ``bash`` and ``run_python`` spawn arbitrary user-level subprocesses,
the sandbox path-guard cannot prevent them from deleting or overwriting files
outside the repo.  This module implements a defense-in-depth pattern matcher
that refuses obviously destructive commands before they reach ``Popen``.

It is *not* a complete security boundary — that role belongs to the Docker
sandbox.  Local-mode users get best-effort protection against accidents and
well-known attack payloads.
"""
from __future__ import annotations
import re
from typing import Tuple

# ── Whole-command signatures ──────────────────────────────
_FORK_BOMB = re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:")
_PIPE_TO_SHELL = re.compile(
    r"\b(curl|wget)\b[^|;&]*\|\s*(sh|bash|zsh|ksh|python|python3|perl|node|ruby)\b"
)

# ── Per-segment signatures ────────────────────────────────
_RM_RF_PROTECTED = re.compile(
    r"\brm\b\s+(?:(?:-[frR]+|--recursive|--force|--no-preserve-root)\s+)+"
    r"(?:/|/\*|~|\$HOME|\.|\.\.)(?:\s|$|/)"
)
_RMDIR_DRIVE = re.compile(r"\brmdir\b\s+/[sS](?:\s+/[qQ])?\s+[A-Za-z]:\\?", re.IGNORECASE)
_DEL_DRIVE = re.compile(
    r"\bdel\b\s+(?:/[fFsSqQ]\s+){2,}(?:[A-Za-z]:\\?|%SystemDrive%\\?)",
    re.IGNORECASE,
)
_FORMAT_DRIVE = re.compile(r"\bformat\b\s+[A-Za-z]:", re.IGNORECASE)
_MKFS = re.compile(r"\bmkfs(?:\.\w+|\s+-t\s+\w+)?\s+/dev/")
_DD_DEVICE = re.compile(r"\bdd\b[^|;&]*\bof=/dev/")
_CHMOD_ROOT = re.compile(r"\bchmod\b\s+-R\s+\S+\s+/(?:\s|$)")
_CHOWN_ROOT = re.compile(r"\bchown\b\s+-R\s+\S+\s+/(?:\s|$)")
_POWER = re.compile(r"\b(shutdown|reboot|halt|poweroff)\b", re.IGNORECASE)
_REDIR_DEVICE = re.compile(r">\s*/dev/(sda|sdb|hda|nvme|xvd)")

_SEGMENT_PATTERNS = [
    (_RM_RF_PROTECTED, "rm -rf on protected path"),
    (_RMDIR_DRIVE, "rmdir /s on drive root"),
    (_DEL_DRIVE, "del /s /q on drive root"),
    (_FORMAT_DRIVE, "format drive"),
    (_MKFS, "mkfs on device"),
    (_DD_DEVICE, "dd to raw device"),
    (_CHMOD_ROOT, "chmod -R on /"),
    (_CHOWN_ROOT, "chown -R on /"),
    (_POWER, "system power command"),
    (_REDIR_DEVICE, "redirect to raw device"),
]

# Commands whose *first* word is a benign consumer of arbitrary strings
_INERT_HEADS = {
    "echo", "printf", "grep", "egrep", "fgrep", "rg", "ripgrep",
    "find", "awk", "sed", "cat", "type", "head", "tail", "less", "more",
    "findstr", "select-string",
}

_SPLIT_RE = re.compile(r"\s*(?:&&|\|\||;|\|)\s*")


# Extract python/perl/ruby -c payloads for deep inspection.
_INLINE_CODE_RE = re.compile(
    r"""\b(?:python3?|py|perl|ruby|node)\b[^|;&]*?\s-c\s*"""
    r"""(?:'([^']*)'|"((?:[^"\\]|\\.)*)")""",
    re.DOTALL,
)


def _extract_inline_scripts(cmd: str) -> list[str]:
    out: list[str] = []
    for m in _INLINE_CODE_RE.finditer(cmd):
        payload = m.group(1) if m.group(1) is not None else m.group(2) or ""
        if payload:
            out.append(payload)
    return out


def scan_command(cmd: str) -> Tuple[bool, str]:
    """Return (blocked, reason).  Blocked means the command must not run."""
    if not cmd or not cmd.strip():
        return False, ""

    if _FORK_BOMB.search(cmd):
        return True, "fork bomb"
    if _PIPE_TO_SHELL.search(cmd):
        return True, "pipe-to-shell download"

    for raw_seg in _SPLIT_RE.split(cmd):
        seg = raw_seg.strip()
        if not seg:
            continue
        # strip leading 'sudo' / 'doas' / 'env VAR=x' style prefixes
        seg2 = re.sub(r"^(?:sudo|doas)\s+", "", seg)
        first = seg2.split(None, 1)[0].lower() if seg2 else ""
        if first in _INERT_HEADS:
            continue
        # remove quoted string literals so `grep 'rm -rf /' f` style false-
        # positives inside quotes never fire from other command heads
        clean = re.sub(r"'[^']*'|\"[^\"]*\"", "", seg2)
        for pat, reason in _SEGMENT_PATTERNS:
            if pat.search(clean):
                return True, reason
    # Deep check: any inline `python -c "..."` payload embedded in the
    # command line is scanned as if it had been submitted to run_python.
    for payload in _extract_inline_scripts(cmd):
        blocked, reason = scan_python_code(payload)
        if blocked:
            return True, f"inline script: {reason}"
    return False, ""


# ── Python-code signatures ────────────────────────────────
_PY_RMTREE_PROTECTED = re.compile(
    r"(?:shutil\.)?rmtree\s*\(\s*['\"](?:/|\.|\.\.|~|/\*)?['\"]"
)
_PY_OS_SYSTEM = re.compile(
    r"(?:os\.system|subprocess\.(?:run|call|Popen|check_output|check_call))\s*\("
    r"[^)]*?(?:rm\s+-[frR]+|format\s+[A-Za-z]:|mkfs|shutdown|reboot)"
)
_PY_IMPORT_OS_SYSTEM = re.compile(r"__import__\s*\(\s*['\"]os['\"]\s*\)\s*\.\s*system\s*\(")
_PY_SUBPROCESS_RM = re.compile(
    r"subprocess\.(?:run|call|Popen|check_output|check_call)\s*\(\s*\["
    r"[^\]]*?['\"]rm['\"]\s*,\s*['\"]-[frR]+['\"]"
)
_PY_OPEN_DEVICE = re.compile(r"open\s*\(\s*['\"]/dev/(sda|sdb|hda|nvme|xvd)")

_PY_PATTERNS = [
    (_PY_RMTREE_PROTECTED, "shutil.rmtree on protected path"),
    (_PY_OS_SYSTEM, "os.system dangerous command"),
    (_PY_IMPORT_OS_SYSTEM, "__import__('os').system(...)"),
    (_PY_SUBPROCESS_RM, "subprocess rm -rf"),
    (_PY_OPEN_DEVICE, "open raw block device"),
]


def scan_python_code(code: str) -> Tuple[bool, str]:
    """Return (blocked, reason) for a Python source snippet."""
    if not code:
        return False, ""
    for pat, reason in _PY_PATTERNS:
        if pat.search(code):
            return True, reason
    return False, ""
