"""Three-level context compaction for long agent sessions.

Levels:
- tool_compact: Rule-based truncation of tool outputs (no LLM call)
- micro_compact: Use fast LLM to summarize oldest 3-5 steps into 1-2 sentences
- auto_compact: Use fast LLM to summarize everything except recent K steps into
  a structured summary (completed actions / key findings / file locations / unresolved issues)

Token counting uses the approximation len(text) // 4 (works for mixed Chinese/English/code).
When real token counts are available from LLM usage responses, call update_actual_usage()
to calibrate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    pass

CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for code/English/mixed."""
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def tool_compact(text: str, max_chars: int = 12000, max_lines: int = 200) -> str:
    """Rule-based truncation for tool output. No LLM call.

    - If output fits, return as-is.
    - Otherwise keep head + tail with a truncation notice in between.
    - Always cap total lines to max_lines.
    """
    if not text:
        return ""
    if len(text) <= max_chars and text.count("\n") <= max_lines:
        return text

    lines = text.split("\n")
    if len(lines) > max_lines:
        head = lines[: max_lines // 2]
        tail = lines[-max_lines // 4 :]
        omitted_lines = len(lines) - len(head) - len(tail)
        text = "\n".join(head) + f"\n\n... ({omitted_lines} lines omitted due to output limit) ...\n\n" + "\n".join(tail)

    if len(text) > max_chars:
        head_chars = max_chars * 2 // 3
        tail_chars = max_chars // 6
        omitted = len(text) - head_chars - tail_chars
        text = text[:head_chars] + f"\n\n... ({omitted} chars omitted) ...\n\n" + text[-tail_chars:]

    return text


@dataclass
class CompactResult:
    """Result of a compaction operation."""
    summary: str
    steps_compacted: int
    tokens_saved: int


class LLMProtocol(Protocol):
    """Protocol for LLM service (avoids circular import)."""
    def chat_fast(self, messages: list[dict], **kwargs) -> str: ...


MICRO_COMPACT_PROMPT = """\
You are summarizing an old segment of a coding agent's conversation history.
Below are {n_steps} steps (user messages, assistant responses, and tool results).
Summarize them in 1-2 concise sentences, preserving:
- What was attempted
- Key outcomes or errors
- Any file paths or symbols that matter

Keep the summary under 80 words. Do NOT add speculation. Output only the summary.

Steps to summarize:
{steps_text}
"""

AUTO_COMPACT_PROMPT = """\
You are compressing a long coding agent conversation to fit within the context window.
Below is the conversation history (excluding the most recent {keep_recent} steps which are kept verbatim).
Produce a structured summary with exactly these sections:

## Completed Actions
- List concrete actions taken (files read/written/edited, commands run, tests executed)

## Key Findings
- Important discoveries: bugs found, code patterns identified, test failures observed

## File Locations
- Files and line numbers that were relevant (e.g., src/main.py:42)

## Unresolved
- Issues not yet addressed, errors still failing, next steps needed

Keep each bullet point under 20 words. Be specific (use actual filenames/symbols from the text).
Do NOT include information not present in the conversation. Total summary should be under 400 words.

Conversation to summarize:
{steps_text}
"""


def _format_steps_for_summary(steps: list[dict]) -> str:
    """Format a list of step dicts into text for LLM summarization."""
    parts = []
    for i, step in enumerate(steps, 1):
        role = step.get("role", step.get("type", "unknown"))
        content = step.get("content", step.get("text", ""))
        if isinstance(content, list):
            content_parts = []
            for c in content:
                if isinstance(c, dict):
                    content_parts.append(c.get("text", c.get("content", str(c))))
                else:
                    content_parts.append(str(c))
            content = "\n".join(content_parts)
        parts.append(f"[{i}] {role}: {content}")
    return "\n\n".join(parts)


def micro_compact(steps: list[dict], llm: Any) -> CompactResult:
    """Use fast LLM to summarize the oldest 3-5 steps into 1-2 sentences.

    `steps` is a list of message dicts (role/content pairs).
    `llm` must have a `chat_fast(messages) -> str` method.
    """
    if not steps:
        return CompactResult(summary="", steps_compacted=0, tokens_saved=0)

    steps_text = _format_steps_for_summary(steps)
    original_tokens = estimate_tokens(steps_text)

    prompt = MICRO_COMPACT_PROMPT.format(n_steps=len(steps), steps_text=steps_text)
    try:
        summary = llm.chat_fast([
            {"role": "system", "content": "You are a concise summarizer."},
            {"role": "user", "content": prompt},
        ])
    except Exception:
        summary = f"[{len(steps)} earlier steps summarized but LLM unavailable]"

    summary_tokens = estimate_tokens(summary)
    return CompactResult(
        summary=summary.strip(),
        steps_compacted=len(steps),
        tokens_saved=max(0, original_tokens - summary_tokens),
    )


def auto_compact(steps: list[dict], llm: Any, keep_recent: int = 10) -> CompactResult:
    """Use fast LLM to produce a structured summary of older steps.

    Steps[-keep_recent:] are NOT included (caller keeps those verbatim).
    Everything in steps[:-keep_recent] is summarized.
    """
    if not steps:
        return CompactResult(summary="", steps_compacted=0, tokens_saved=0)

    to_compact = steps[:-keep_recent] if len(steps) > keep_recent else steps
    if not to_compact:
        return CompactResult(summary="", steps_compacted=0, tokens_saved=0)

    steps_text = _format_steps_for_summary(to_compact)
    original_tokens = estimate_tokens(steps_text)

    prompt = AUTO_COMPACT_PROMPT.format(keep_recent=keep_recent, steps_text=steps_text)
    try:
        summary = llm.chat_fast([
            {"role": "system", "content": "You are a precise engineering summarizer."},
            {"role": "user", "content": prompt},
        ])
    except Exception:
        summary = f"## Summary\n[{len(to_compact)} earlier steps - LLM unavailable for detailed summary]"

    summary_tokens = estimate_tokens(summary)
    return CompactResult(
        summary=summary.strip(),
        steps_compacted=len(to_compact),
        tokens_saved=max(0, original_tokens - summary_tokens),
    )
