"""Tests for OutputParser and Agent Loop (mock LLM, no real API calls)."""
from __future__ import annotations
import os
import tempfile
import pytest
from pathlib import Path

from repopilot.agent.parser import parse_response, ParsedResponse


class TestParseResponse:
    def test_plain_text(self):
        r = parse_response("Hello, I can help.")
        assert r.is_text
        assert r.content == "Hello, I can help."
        assert r.tool_calls == []

    def test_native_tool_calls(self):
        tool_calls = [
            {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "main.py"}'}},
        ]
        r = parse_response("Let me read that file.", tool_calls=tool_calls)
        assert r.is_tool_call
        assert len(r.tool_calls) == 1
        assert r.tool_calls[0]["name"] == "read_file"
        assert r.tool_calls[0]["arguments"]["path"] == "main.py"

    def test_native_finish_tool(self):
        tool_calls = [
            {"id": "c1", "function": {"name": "finish", "arguments": '{"summary": "Done fixing."}'}},
        ]
        r = parse_response("", tool_calls=tool_calls)
        assert r.is_finish
        assert "Done fixing" in r.content

    def test_xml_tool_calls(self):
        content = '''I will look at the file.
<tool_call>
<name>grep</name>
<arguments>{"pattern": "def add"}</arguments>
</tool_call>'''
        r = parse_response(content)
        assert r.is_tool_call
        assert r.tool_calls[0]["name"] == "grep"
        assert r.tool_calls[0]["arguments"]["pattern"] == "def add"

    def test_xml_finish(self):
        content = '''Task complete.
<finish>
<summary>Fixed the add function.</summary>
</finish>'''
        r = parse_response(content)
        assert r.is_finish
        assert "Fixed" in r.content

    def test_empty_content(self):
        r = parse_response("")
        assert r.is_text
        assert r.content == ""

    def test_invalid_json_args(self):
        tool_calls = [
            {"id": "c1", "function": {"name": "bash", "arguments": "not valid json"}},
        ]
        r = parse_response("", tool_calls=tool_calls)
        assert r.is_tool_call
        assert "_raw" in r.tool_calls[0]["arguments"]

    def test_multiple_tool_calls(self):
        tool_calls = [
            {"id": "c1", "function": {"name": "read_file", "arguments": '{"path":"a.py"}'}},
            {"id": "c2", "function": {"name": "read_file", "arguments": '{"path":"b.py"}'}},
        ]
        r = parse_response("", tool_calls=tool_calls)
        assert r.is_tool_call
        assert len(r.tool_calls) == 2

    def test_dict_args_passthrough(self):
        tool_calls = [
            {"id": "c1", "function": {"name": "bash", "arguments": {"command": "ls"}}},
        ]
        r = parse_response("", tool_calls=tool_calls)
        assert r.tool_calls[0]["arguments"] == {"command": "ls"}

    def test_content_without_tool_calls_is_text(self):
        r = parse_response("The answer is 42.")
        assert r.is_text
        assert not r.is_tool_call
        assert not r.is_finish


class TestAgentLoop:
    """Integration tests for the agent loop using a fake LLM."""

    def _make_repo(self, tmp_path: Path) -> Path:
        (tmp_path / "main.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (tmp_path / "test_math.py").write_text("from main import add\ndef test_add():\n    assert add(2,3) == 5\n", encoding="utf-8")
        (tmp_path / "README.md").write_text("# Test\n", encoding="utf-8")
        return tmp_path

    def _make_fake_llm(self, responses: list):
        """Create a fake LLM that returns predetermined responses.
        Each response is a tuple of (content, tool_calls) or a string (content only)."""
        from repopilot.llm.service import LLMResponse

        class FakeLLM:
            def __init__(self, resps):
                self._responses = list(resps)
                self._index = 0
                self.calls = []
                self.models = {"fast": "fake/fast", "default": "fake/default", "strong": "fake/strong"}

            def chat(self, messages, tools=None, temperature=0.3, tier=None, stream=False):
                self.calls.append({"messages": len(messages), "tools": bool(tools)})
                if self._index >= len(self._responses):
                    return LLMResponse(content="I'm done.", tool_calls=[], usage={"total_tokens": 50, "prompt_tokens": 40, "completion_tokens": 10}, model="fake/default")
                resp = self._responses[self._index]
                self._index += 1
                if isinstance(resp, str):
                    return LLMResponse(content=resp, tool_calls=[], usage={"total_tokens": 50, "prompt_tokens": 40, "completion_tokens": 10}, model="fake/default")
                content, tcs = resp
                return LLMResponse(content=content, tool_calls=tcs, usage={"total_tokens": 100, "prompt_tokens": 80, "completion_tokens": 20}, model="fake/default")

            def _model(self, tier):
                return self.models.get(str(tier), "fake/default")

            def chat_fast(self, *args, **kw):
                return "Summary of earlier steps."

            def chat_strong(self, system, user, **kw):
                return "Strong answer."

            def chat_default(self, system, user, **kw):
                return "Default answer."

        return FakeLLM(responses)

    def test_loop_finishes_with_text(self, tmp_path):
        """Agent returns plain text (no tool calls) → completes immediately."""
        from repopilot.agent.loop import run_agent
        from repopilot.sandbox.local_sandbox import LocalSandbox
        from repopilot.permission.engine import PermissionEngine

        repo = self._make_repo(tmp_path)
        llm = self._make_fake_llm([
            "The add function returns a+b. That's correct.",
        ])
        with LocalSandbox(repo) as sb:
            result = run_agent(
                task="What does add return?",
                repo_path=repo,
                llm=llm,
                sandbox=sb,
                permission_engine=PermissionEngine(mode="auto"),
                max_steps=5,
            )
        assert result.status == "completed"
        assert result.steps == 1

    def test_loop_executes_tool_then_finishes(self, tmp_path):
        """Agent calls read_file then gives final answer."""
        from repopilot.agent.loop import run_agent
        from repopilot.sandbox.local_sandbox import LocalSandbox
        from repopilot.permission.engine import PermissionEngine

        repo = self._make_repo(tmp_path)
        read_tc = [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "main.py"}'}}]
        llm = self._make_fake_llm([
            ("Let me read main.py.", read_tc),
            "The add function returns a+b.",  # final text answer
        ])
        events = []
        with LocalSandbox(repo) as sb:
            result = run_agent(
                task="What does add do?",
                repo_path=repo,
                llm=llm,
                sandbox=sb,
                permission_engine=PermissionEngine(mode="auto"),
                max_steps=10,
                stream_callback=lambda e: events.append(e.type),
            )
        assert result.status == "completed"
        assert result.steps == 2
        assert "tool_call" in events
        assert "tool_result" in events
        assert result.trajectory, "should have at least one tool call in trajectory"
        assert result.trajectory[0]["tool"] == "read_file"

    def test_loop_calls_finish_tool(self, tmp_path):
        """Agent explicitly calls finish tool."""
        from repopilot.agent.loop import run_agent
        from repopilot.sandbox.local_sandbox import LocalSandbox
        from repopilot.permission.engine import PermissionEngine

        repo = self._make_repo(tmp_path)
        finish_tc = [{"id": "c1", "function": {"name": "finish", "arguments": '{"summary": "All done.", "tests_passed": true}'}}]
        llm = self._make_fake_llm([
            ("Task complete.", finish_tc),
        ])
        with LocalSandbox(repo) as sb:
            result = run_agent(
                task="Do nothing",
                repo_path=repo,
                llm=llm,
                sandbox=sb,
                permission_engine=PermissionEngine(mode="auto"),
                max_steps=5,
            )
        assert result.status == "completed"
        assert "All done" in result.summary

    def test_loop_max_steps(self, tmp_path):
        """Agent loops infinitely (always returns tool calls) → hits max_steps."""
        from repopilot.agent.loop import run_agent
        from repopilot.sandbox.local_sandbox import LocalSandbox
        from repopilot.permission.engine import PermissionEngine

        repo = self._make_repo(tmp_path)
        # Always returns a tool call, never finishes
        read_tc = [{"id": "c1", "function": {"name": "read_file", "arguments": '{"path": "main.py"}'}}]
        responses = [("Reading again...", read_tc)] * 10
        llm = self._make_fake_llm(responses)
        with LocalSandbox(repo) as sb:
            result = run_agent(
                task="Loop forever",
                repo_path=repo,
                llm=llm,
                sandbox=sb,
                permission_engine=PermissionEngine(mode="auto"),
                max_steps=3,
            )
        assert result.status == "max_steps"
        assert result.steps == 3

    def test_loop_permission_denied_returns_error(self, tmp_path):
        """Dangerous command is denied even in auto mode."""
        from repopilot.agent.loop import run_agent
        from repopilot.sandbox.local_sandbox import LocalSandbox
        from repopilot.permission.engine import PermissionEngine

        repo = self._make_repo(tmp_path)
        bad_tc = [{"id": "c1", "function": {"name": "bash", "arguments": '{"command": "rm -rf /"}'}}]
        llm = self._make_fake_llm([
            ("Trying dangerous command", bad_tc),
            "I cannot do that.",
        ])
        with LocalSandbox(repo) as sb:
            result = run_agent(
                task="Destroy everything",
                repo_path=repo,
                llm=llm,
                sandbox=sb,
                permission_engine=PermissionEngine(mode="auto"),
                max_steps=5,
            )
        # Should complete (gets error from tool, then gives answer)
        assert result.status == "completed"
        # The denied command should appear in trajectory
        assert any("denied" in (step.get("result","")+step.get("error","")).lower() or "error" in step for step in result.trajectory)

    def test_loop_session_store_records_events(self, tmp_path):
        """Session store receives events during the loop."""
        from repopilot.agent.loop import run_agent
        from repopilot.sandbox.local_sandbox import LocalSandbox
        from repopilot.permission.engine import PermissionEngine
        from repopilot.session.store import SessionStore

        repo = self._make_repo(tmp_path)
        llm = self._make_fake_llm(["Just answering."])
        with tempfile.TemporaryDirectory() as td:
            store = SessionStore(sessions_dir=os.path.join(td, "sess"), db_path=os.path.join(td, "db.sqlite"))
            with LocalSandbox(repo) as sb:
                result = run_agent(
                    task="Hi",
                    repo_path=repo,
                    llm=llm,
                    sandbox=sb,
                    permission_engine=PermissionEngine(mode="auto"),
                    session_store=store,
                    max_steps=3,
                )
            assert result.session_id
            events = store.read_events(result.session_id)
            types = [e["type"] for e in events]
            assert "user_msg" in types
            assert "assistant_msg" in types

    def test_loop_multiple_tools_in_sequence(self, tmp_path):
        """Agent calls grep then read_file then answers."""
        from repopilot.agent.loop import run_agent
        from repopilot.sandbox.local_sandbox import LocalSandbox
        from repopilot.permission.engine import PermissionEngine

        repo = self._make_repo(tmp_path)
        grep_tc = [{"id": "c1", "function": {"name": "grep", "arguments": '{"pattern": "def add"}'}}]
        read_tc = [{"id": "c2", "function": {"name": "read_file", "arguments": '{"path": "main.py"}'}}]
        llm = self._make_fake_llm([
            ("Searching", grep_tc),
            ("Found it, reading", read_tc),
            "add returns a+b.",
        ])
        with LocalSandbox(repo) as sb:
            result = run_agent(
                task="Find add",
                repo_path=repo,
                llm=llm,
                sandbox=sb,
                permission_engine=PermissionEngine(mode="auto"),
                max_steps=10,
            )
        assert result.status == "completed"
        assert result.steps == 3
        assert len(result.trajectory) == 2
        assert result.trajectory[0]["tool"] == "grep"
        assert result.trajectory[1]["tool"] == "read_file"
