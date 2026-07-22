"""Structured error codes for tool results.

Every tool ``execute()`` that returns ``ToolResult(error=...)`` should also
set ``error_code`` to one of these enum values so the agent loop,
UI, and hooks can react to specific failure classes instead of
string-matching human-readable messages.
"""
from __future__ import annotations
from enum import Enum


class ToolErrorCode(str, Enum):
    """Categorical error codes shared by all tools."""

    # File/path/target not present.  Do not retry the same path.
    NOT_FOUND = "E_NOT_FOUND"
    # Sandbox blocked the operation (path escape, symlink, denied dir, ...).
    PERMISSION = "E_PERMISSION"
    # Subprocess or LLM call exceeded its timeout budget.  Retryable with
    # a larger timeout.
    TIMEOUT = "E_TIMEOUT"
    # Arguments failed validation (missing key, wrong type, empty required).
    INVALID_ARGS = "E_INVALID_ARGS"
    # edit_file: old_string not present in the file.
    NOT_MATCHED = "E_NOT_MATCHED"
    # edit_file: old_string is not unique; caller must set replace_all=true.
    AMBIGUOUS = "E_AMBIGUOUS"
    # Content or output exceeded the tool's size cap.
    SIZE_LIMIT = "E_SIZE_LIMIT"
    # Sandbox-level infrastructure failure (container died, docker unreachable).
    SANDBOX = "E_SANDBOX"
    # Command completed with a non-zero exit code (not a crash of ours).
    EXEC_FAILED = "E_EXEC_FAILED"
    # User interrupted (Ctrl-C).
    INTERRUPTED = "E_INTERRUPTED"
    # Uncategorised.  Indicates a bug in RepoPilot itself.
    INTERNAL = "E_INTERNAL"


# Codes for which the loop MAY retry the same call (usually with backoff).
RETRYABLE = frozenset({
    ToolErrorCode.TIMEOUT.value,
    ToolErrorCode.SANDBOX.value,
    ToolErrorCode.INTERNAL.value,
})


def is_retryable(code: str | None) -> bool:
    return code in RETRYABLE if code else False
