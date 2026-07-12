"""Output parser — extracts tool calls and finish signals from LLM responses.

Supports:
- Native OpenAI/Anthropic tool_calls (preferred, most reliable)
- XML-style <tool_call> fallback (for models that don't support native tool calling)
- Plain text detection (no tool calls → final answer)
- JSON parse error recovery (returns raw text, doesn't crash)
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ParsedResponse:
    """Result of parsing an LLM response."""
    action: str  # "tool" | "finish" | "text"
    content: str = ""  # plain text content (thoughts/final answer)
    tool_calls: list[dict] = field(default_factory=list)  # list of {id, name, arguments: dict}
    finish_reason: str = ""  # stop | tool_calls | length | error

    # Tool call parsing
    # Format: {"name": "tool_name", "arguments": {...}}

    @property
    def is_tool_call(self) -> bool:
        return self.action == "tool"

    @property
    def is_finish(self) -> bool:
        return self.action == "finish"

    @property
    def is_text(self) -> bool:
        return self.action == "text"


_XML_TOOL_RE = re.compile(
    r"<tool_call>\s*<name>(.*?)</name>\s*<arguments>(.*?)</arguments>\s*</tool_call>",
    re.DOTALL,
)
_XML_FINISH_RE = re.compile(
    r"<finish>\s*(?:<summary>(.*?)</summary>)?\s*(?:<tests_passed>(.*?)</tests_passed>)?\s*</finish>",
    re.DOTALL,
)


def parse_response(
    content: Optional[str],
    tool_calls: Optional[list[dict]] = None,
    finish_reason: str = "stop",
) -> ParsedResponse:
    """Parse an LLM response into a structured action.

    Priority:
    1. Native tool_calls (from OpenAI API response)
    2. XML <tool_call> tags in content (fallback)
    3. <finish> tag in content
    4. Plain text (final answer)
    """
    text_content = (content or "").strip()

    # 1. Native tool calls (OpenAI/LiteLLM format)
    if tool_calls:
        parsed_calls = []
        for tc in tool_calls:
            fn = tc.get("function", tc)
            name = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")
            if isinstance(raw_args, dict):
                args = raw_args
            else:
                try:
                    args = json.loads(raw_args) if raw_args else {}
                except (json.JSONDecodeError, TypeError):
                    args = {"_raw": raw_args}
            call_id = tc.get("id", f"call_{name}_{len(parsed_calls)}")
            parsed_calls.append({"id": call_id, "name": name, "arguments": args})

        if parsed_calls:
            finish_name = parsed_calls[-1].get("name", "")
            if finish_name == "finish" or any(c["name"] == "finish" for c in parsed_calls):
                # Extract finish from tool calls
                finish_call = next(c for c in parsed_calls if c["name"] == "finish")
                summary = finish_call["arguments"].get("summary", text_content)
                tests_passed = finish_call["arguments"].get("tests_passed", True)
                return ParsedResponse(
                    action="finish",
                    content=summary or text_content,
                    finish_reason="tool_calls",
                )
            return ParsedResponse(
                action="tool",
                content=text_content,
                tool_calls=parsed_calls,
                finish_reason=finish_reason,
            )

    # 2. Check for <finish> XML tag (highest priority in content)
    finish_match = _XML_FINISH_RE.search(text_content)
    if finish_match:
        summary = finish_match.group(1) or text_content
        return ParsedResponse(
            action="finish",
            content=summary.strip() if summary else text_content,
            finish_reason="stop",
        )

    # 3. Check for XML-style tool calls in content
    xml_calls = _XML_TOOL_RE.findall(text_content)
    if xml_calls:
        parsed_calls = []
        for name, raw_args in xml_calls:
            name = name.strip()
            raw_args = raw_args.strip()
            try:
                args = json.loads(raw_args) if raw_args else {}
            except (json.JSONDecodeError, TypeError):
                args = {"_raw": raw_args}
            parsed_calls.append({
                "id": f"xml_call_{len(parsed_calls)}",
                "name": name,
                "arguments": args,
            })
        if parsed_calls:
            if any(c["name"] == "finish" for c in parsed_calls):
                finish_call = next(c for c in parsed_calls if c["name"] == "finish")
                return ParsedResponse(
                    action="finish",
                    content=finish_call["arguments"].get("summary", text_content),
                    finish_reason="stop",
                )
            return ParsedResponse(
                action="tool",
                content=text_content,
                tool_calls=parsed_calls,
                finish_reason="stop",
            )

    # 4. Check if content references "finish" or completion patterns
    # (Models sometimes indicate completion in natural language)
    if not text_content:
        return ParsedResponse(action="text", content="", finish_reason=finish_reason)

    # 5. Plain text
    return ParsedResponse(
        action="text",
        content=text_content,
        finish_reason=finish_reason,
    )
