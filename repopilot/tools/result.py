"""Tool execution result."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from repopilot.tools.errors import ToolErrorCode, is_retryable


@dataclass
class ToolResult:
    """Result returned by every tool.execute().

    Attributes:
        content:     Human-readable output string (sent to the LLM).
        error:       Error message if the tool failed (None on success).
        error_code:  Structured error class (see ``ToolErrorCode``).  Present
                     whenever ``error`` is set.  Used by the agent loop and
                     UI to react to specific failure classes without brittle
                     string matching.
        retryable:   Whether the same call is worth retrying (usually only
                     for TIMEOUT / SANDBOX / INTERNAL).
        metadata:    Extra structured data for hooks / UI / cost tracking
                     (not sent to LLM).  Each tool documents its keys.
        duration_ms: Wall-clock duration of the tool call in milliseconds.
                     Optional — the REPL also measures independently.
    """
    content: str = ""
    error: str | None = None
    error_code: str | None = None
    retryable: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0

    def __bool__(self) -> bool:
        return self.error is None

    def to_message(self) -> str:
        if self.error:
            code = f"[{self.error_code}] " if self.error_code else ""
            return f"[ERROR] {code}{self.error}"
        return self.content if self.content else "[done]"


def error_result(
    message: str,
    code: ToolErrorCode,
    *,
    retryable: bool | None = None,
    **metadata: Any,
) -> ToolResult:
    """Convenience constructor for error returns.

    ``retryable`` defaults to whatever ``is_retryable(code)`` says; pass
    explicitly to override.
    """
    return ToolResult(
        error=message,
        error_code=code.value,
        retryable=is_retryable(code.value) if retryable is None else retryable,
        metadata=dict(metadata),
    )


def truncate_text(
    text: str,
    head: int = 500,
    tail: int = 1500,
    max_lines: int | None = None,
) -> str:
    """Truncate long text to fit within LLM context budgets."""
    if text is None:
        return ""
    if max_lines is not None:
        all_lines = text.splitlines(keepends=True)
        if len(all_lines) > max_lines:
            kept = "".join(all_lines[:max_lines])
            skipped = len(all_lines) - max_lines
            text = kept + f"\n...[truncated {skipped} lines]...\n"

    if len(text) <= head + tail + 50:
        return text

    keep_head = text[:head]
    keep_tail = text[-tail:]
    skipped_chars = len(text) - head - tail
    return f"{keep_head}\n...[truncated {skipped_chars} chars]...\n{keep_tail}"
