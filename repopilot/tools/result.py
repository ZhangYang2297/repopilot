"""Tool execution result."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """Result returned by every tool.execute().

    Attributes:
        content:  Human-readable output string (sent to the LLM).
        error:    Error message if the tool failed (None on success).
        metadata: Extra structured data for hooks/cost tracking (not sent to LLM).
    """
    content: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        """True when the tool succeeded (no error)."""
        return self.error is None

    def to_message(self) -> str:
        """Format result for inclusion in the LLM conversation."""
        if self.error:
            return f"[ERROR] {self.error}"
        return self.content if self.content else "[done]"


def truncate_text(
    text: str,
    head: int = 500,
    tail: int = 1500,
    max_lines: int | None = None,
) -> str:
    """Truncate long text to fit within LLM context budgets.

    Strategy: keep the first ``head`` characters and the last ``tail``
    characters, replacing the middle with a skip notice.  If ``max_lines``
    is set, also limit line count from the head.

    This is the single choke-point used by every tool to bound output
    size so that a single runaway command cannot blow the context window.
    """
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
