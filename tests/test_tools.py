from __future__ import annotations
import pytest
from repopilot.tools import (
    Tool, ToolResult, ToolRegistry, ToolNotFoundError,
    TIER_READONLY, TIER_WRITE, TIER_EXEC, truncate_text,
)


# ── ToolResult tests ──────────────────────────────────────────

class TestToolResult:
    def test_success_is_truthy(self):
        r = ToolResult(content="hello")
        assert bool(r) is True
        assert r.error is None

    def test_error_is_falsy(self):
        r = ToolResult(error="not found")
        assert bool(r) is False

    def test_to_message_success(self):
        r = ToolResult(content="file contents here")
        assert r.to_message() == "file contents here"

    def test_to_message_error(self):
        r = ToolResult(error="permission denied")
        assert r.to_message() == "[ERROR] permission denied"

    def test_to_message_empty(self):
        r = ToolResult()
        assert r.to_message() == "[done]"

    def test_metadata_default_empty(self):
        r = ToolResult(content="x")
        assert r.metadata == {}


# ── truncate_text tests ──────────────────────────────────────

class TestTruncateText:
    def test_short_text_unchanged(self):
        assert truncate_text("hello") == "hello"
        assert truncate_text("a" * 1000) == "a" * 1000  # under head+tail+50

    def test_long_text_truncated(self):
        text = "x" * 5000
        r = truncate_text(text, head=500, tail=1500)
        assert len(r) < len(text)
        assert r.startswith("x" * 500)
        assert r.rstrip().endswith("x" * 100)  # tail present
        assert "truncated" in r

    def test_none_returns_empty(self):
        assert truncate_text(None) == ""

    def test_max_lines(self):
        text = "\n".join(f"line {i}" for i in range(100))
        r = truncate_text(text, max_lines=10)
        assert "line 0" in r
        assert "truncated" in r
        assert "line 90" not in r  # later lines not included

    def test_exact_boundary(self):
        text = "a" * (500 + 1500 + 50)
        assert truncate_text(text) == text  # exactly at threshold


# ── Concrete tool for testing ────────────────────────────────

class _EchoTool(Tool):
    name = "echo"
    description = "Echo back the message argument."
    parameters = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Text to echo back"},
        },
        "required": ["message"],
    }
    tier = TIER_READONLY

    def execute(self, args, sandbox=None, extra=None):
        return ToolResult(content=args.get("message", ""))


class _WriteTool(Tool):
    name = "write_file"
    description = "Write a file."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    }
    tier = TIER_WRITE

    def execute(self, args, sandbox=None, extra=None):
        return ToolResult(content=f"wrote {args.get('path', '?')}")


class _BashTool(Tool):
    name = "bash"
    description = "Run a shell command."
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
        },
        "required": ["command"],
    }
    tier = TIER_EXEC

    def execute(self, args, sandbox=None, extra=None):
        return ToolResult(content=f"$ {args.get('command', '')}\noutput")


# ── ToolRegistry tests ───────────────────────────────────────

class TestToolRegistry:
    def _make_registry(self, mode="auto"):
        from repopilot.permission import PermissionEngine
        pe = PermissionEngine(mode=mode)
        reg = ToolRegistry(permission_engine=pe)
        reg.register(_EchoTool())
        reg.register(_WriteTool())
        reg.register(_BashTool())
        return reg

    def test_register_and_get(self):
        reg = ToolRegistry()
        t = _EchoTool()
        reg.register(t)
        assert reg.get("echo") is t
        assert reg.has("echo") is True
        assert reg.has("nonexist") is False

    def test_get_unknown_raises(self):
        reg = ToolRegistry()
        with pytest.raises(ToolNotFoundError):
            reg.get("nonexist")

    def test_list_tools(self):
        reg = self._make_registry()
        names = reg.tool_names()
        assert set(names) == {"echo", "write_file", "bash"}

    def test_schemas(self):
        reg = self._make_registry()
        schemas = reg.schemas()
        assert len(schemas) == 3
        for s in schemas:
            assert s["type"] == "function"
            assert "name" in s["function"]
            assert "description" in s["function"]
            assert "parameters" in s["function"]

    def test_execute_readonly_auto(self):
        reg = self._make_registry(mode="auto")
        r = reg.execute("echo", {"message": "hi"}, sandbox=None)
        assert bool(r) is True
        assert r.content == "hi"

    def test_execute_unknown_tool_returns_error(self):
        reg = self._make_registry()
        r = reg.execute("ghost", {}, sandbox=None)
        assert bool(r) is False
        assert "Unknown tool" in r.error

    def test_execute_dangerous_cmd_denied(self):
        reg = self._make_registry(mode="confirm")
        r = reg.execute("bash", {"command": "rm -rf /"}, sandbox=None)
        assert bool(r) is False
        assert "denied" in r.error.lower() or "denied" in r.error

    def test_execute_permission_deny_mode_blocks_write(self):
        reg = self._make_registry(mode="deny")
        r = reg.execute("write_file", {"path": "a.py", "content": "x"}, sandbox=None)
        assert bool(r) is False

    def test_execute_auto_allows_write(self):
        reg = self._make_registry(mode="auto")
        r = reg.execute("write_file", {"path": "a.py", "content": "x"}, sandbox=None)
        assert bool(r) is True

    def test_unregister(self):
        reg = ToolRegistry()
        reg.register(_EchoTool())
        reg.unregister("echo")
        assert reg.has("echo") is False

    def test_register_unnamed_tool_raises(self):
        class NoName(Tool):
            name = ""
            description = "x"
            parameters = {}
            def execute(self, args, sandbox=None, extra=None):
                return ToolResult(content="ok")
        reg = ToolRegistry()
        with pytest.raises(ValueError):
            reg.register(NoName())

    def test_tool_exception_caught(self):
        """Tool exceptions are caught and returned as ToolResult.error."""
        class BoomTool(Tool):
            name = "boom"
            description = "Raises"
            parameters = {}
            tier = TIER_READONLY
            def execute(self, args, sandbox=None, extra=None):
                raise RuntimeError("explosion")
        reg = self._make_registry(mode="auto")
        reg.register(BoomTool())
        r = reg.execute("boom", {}, sandbox=None)
        assert bool(r) is False
        assert "RuntimeError" in r.error
        assert "explosion" in r.error
