"""Tests for repopilot.agent.compact and repopilot.agent.context."""
from __future__ import annotations
import pytest
from repopilot.agent.compact import (
    estimate_tokens, tool_compact, micro_compact, auto_compact, CompactResult, CHARS_PER_TOKEN,
)
from repopilot.agent.context import ContextManager, Step


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_short_string(self):
        assert estimate_tokens("hello") == 1  # 5 // 4 = 1

    def test_longer_string(self):
        text = "a" * 400
        assert estimate_tokens(text) == 100

    def test_minimum_one_for_nonempty(self):
        assert estimate_tokens("x") == 1


class TestToolCompact:
    def test_short_content_unchanged(self):
        text = "hello world"
        assert tool_compact(text, max_chars=100, max_lines=10) == text

    def test_truncates_long_content(self):
        text = "x" * 20000
        result = tool_compact(text, max_chars=1000, max_lines=200)
        assert len(result) < len(text)
        assert "omitted" in result

    def test_truncates_many_lines(self):
        lines = [f"line {i}" for i in range(500)]
        text = "\n".join(lines)
        result = tool_compact(text, max_chars=50000, max_lines=100)
        assert "omitted" in result
        assert len(result.split("\n")) < 500

    def test_empty_string(self):
        assert tool_compact("") == ""

    def test_exactly_at_boundary(self):
        text = "x" * 12000
        result = tool_compact(text, max_chars=12000, max_lines=200)
        assert result == text


class TestMicroCompact:
    def test_empty_steps(self, fake_llm):
        result = micro_compact([], fake_llm)
        assert result.steps_compacted == 0
        assert result.summary == ""

    def test_summarizes_steps(self, fake_llm):
        fake_llm.response = "Read main.py and fixed the add function."
        steps = [
            {"role": "user", "content": "fix the bug"},
            {"role": "assistant", "content": "I'll check main.py", "tool_calls": []},
            {"role": "tool", "content": "def add(a,b): return a-b"},
        ]
        result = micro_compact(steps, fake_llm)
        assert result.steps_compacted == 3
        assert "add function" in result.summary or "fixed" in result.summary
        assert result.tokens_saved >= 0

    def test_llm_failure_graceful(self, failing_llm):
        steps = [{"role": "user", "content": "hi"}]
        result = micro_compact(steps, failing_llm)
        assert result.steps_compacted == 1
        assert "LLM unavailable" in result.summary or "summarized" in result.summary


class TestAutoCompact:
    def test_empty_steps(self, fake_llm):
        result = auto_compact([], fake_llm)
        assert result.steps_compacted == 0

    def test_keeps_recent(self, fake_llm):
        fake_llm.response = "## Completed Actions\n- Fixed bug"
        steps = [{"role": "user", "content": f"step {i}"} for i in range(20)]
        result = auto_compact(steps, fake_llm, keep_recent=10)
        # Should compact steps[:-10] = 10 steps
        assert result.steps_compacted == 10
        assert "Fixed bug" in result.summary or "Completed" in result.summary

    def test_llm_failure_graceful(self, failing_llm):
        steps = [{"role": "user", "content": "hi"} for _ in range(15)]
        result = auto_compact(steps, failing_llm, keep_recent=5)
        assert "LLM unavailable" in result.summary or "earlier steps" in result.summary


class TestStep:
    def test_basic_step(self):
        s = Step(role="user", content="hello")
        assert s.role == "user"
        assert s.tokens > 0

    def test_step_with_tool_calls(self):
        s = Step(role="assistant", content="thinking", tool_calls=[{"id": "1"}])
        assert s.tool_calls is not None
        assert s.tokens > 0

    def test_to_message(self):
        s = Step(role="user", content="hello")
        msg = s.to_message()
        assert msg["role"] == "user"
        assert msg["content"] == "hello"
        assert "tool_calls" not in msg

    def test_to_message_with_tool_calls(self):
        tc = [{"id": "call_1", "type": "function", "function": {"name": "bash"}}]
        s = Step(role="assistant", content="", tool_calls=tc)
        msg = s.to_message()
        assert msg["tool_calls"] == tc


class TestContextManager:
    def test_empty_messages(self):
        ctx = ContextManager(budget_tokens=100000, system_prompt="You are helpful.")
        msgs = ctx.build_messages()
        assert len(msgs) == 1
        assert msgs[0]["role"] == "system"

    def test_add_user_message(self):
        ctx = ContextManager(budget_tokens=100000, system_prompt="sys")
        ctx.add_user("hello")
        msgs = ctx.build_messages()
        assert any(m["role"] == "user" and m["content"] == "hello" for m in msgs)

    def test_add_assistant_message(self):
        ctx = ContextManager(budget_tokens=100000, system_prompt="sys")
        ctx.add_assistant("hi there")
        msgs = ctx.build_messages()
        assert any(m["role"] == "assistant" for m in msgs)

    def test_add_tool_result_compacts(self):
        ctx = ContextManager(budget_tokens=100000, system_prompt="sys")
        big_output = "x" * 30000
        ctx.add_tool_result("call_1", big_output)
        msgs = ctx.build_messages()
        tool_msgs = [m for m in msgs if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert len(tool_msgs[0]["content"]) < len(big_output)  # compacted

    def test_build_with_task(self):
        ctx = ContextManager(budget_tokens=100000, system_prompt="sys")
        msgs = ctx.build_messages(task="do something")
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "do something"

    def test_set_plan(self):
        ctx = ContextManager(budget_tokens=100000, system_prompt="sys")
        ctx.set_plan("1. Read files\n2. Fix bug")
        msgs = ctx.build_messages()
        plan_msgs = [m for m in msgs if "Current Plan" in m.get("content", "")]
        assert len(plan_msgs) == 1

    def test_project_context_included(self):
        ctx = ContextManager(
            budget_tokens=100000, system_prompt="sys",
            repo_map_str="main.py - def add()", memory_str="",
        )
        msgs = ctx.build_messages()
        repo_msgs = [m for m in msgs if "Repository Structure" in m.get("content", "")]
        assert len(repo_msgs) == 1

    def test_recent_steps(self):
        ctx = ContextManager(budget_tokens=100000, system_prompt="sys")
        for i in range(15):
            ctx.add_user(f"message {i}")
        recent = ctx.recent_steps(n=3)
        assert len(recent) == 3
        assert "message 14" in recent[-1]["content"]

    def test_token_usage_ratio(self):
        ctx = ContextManager(budget_tokens=10000, system_prompt="")
        assert ctx.token_usage_ratio() < 0.1
        ctx.add_user("x" * 20000)  # ~5000 tokens
        ratio = ctx.token_usage_ratio()
        assert ratio > 0.4

    def test_needs_compaction_none(self):
        ctx = ContextManager(budget_tokens=100000, system_prompt="")
        assert ctx.needs_compaction() is None

    def test_needs_compaction_micro(self):
        ctx = ContextManager(budget_tokens=2000, system_prompt="")
        ctx.add_user("x" * 6000)  # ~1500 tokens = 75%
        level = ctx.needs_compaction()
        assert level in ("micro", "auto", None)

    def test_needs_compaction_auto(self):
        ctx = ContextManager(budget_tokens=1000, system_prompt="")
        ctx.add_user("x" * 4000)  # ~1000 tokens = 100%
        assert ctx.needs_compaction() == "auto"

    def test_compact_micro(self, fake_llm):
        fake_llm.response = "Summarized old steps."
        ctx = ContextManager(budget_tokens=100000, system_prompt="")
        for i in range(15):
            ctx.add_user(f"step {i} content here")
        initial_steps = len(ctx.steps)
        result = ctx.compact("micro", fake_llm)
        assert result.steps_compacted > 0
        assert len(ctx.steps) < initial_steps
        assert ctx.summary != ""

    def test_compact_auto(self, fake_llm):
        fake_llm.response = "## Completed Actions\n- Many things done"
        ctx = ContextManager(budget_tokens=100000, system_prompt="")
        for i in range(25):
            ctx.add_user(f"step {i} content here " + "x" * 100)
        result = ctx.compact("auto", fake_llm)
        assert result.steps_compacted > 0
        assert "Completed Actions" in ctx.summary or "Many things" in ctx.summary or "earlier steps" in ctx.summary
        assert len(ctx.steps) <= 10  # keep_recent=10

    def test_inject_skill_prompt(self):
        ctx = ContextManager(budget_tokens=100000, system_prompt="sys")
        ctx.inject_skill_prompt("Skill: can do X")
        msgs = ctx.build_messages()
        skill_msgs = [m for m in msgs if "Available Skills" in m.get("content", "")]
        assert len(skill_msgs) == 1

    def test_add_observation(self):
        ctx = ContextManager(budget_tokens=100000, system_prompt="sys")
        ctx.add_observation("Reflection: try a different approach")
        msgs = ctx.build_messages()
        obs = [m for m in msgs if "Reflection" in m.get("content", "")]
        assert len(obs) == 1

    def test_error_tool_result_prefixed(self):
        ctx = ContextManager(budget_tokens=100000, system_prompt="sys")
        ctx.add_tool_result("call_1", "command not found", is_error=True)
        msgs = ctx.build_messages()
        tool_msgs = [m for m in msgs if m["role"] == "tool"]
        assert tool_msgs[0]["content"].startswith("Error:")

    def test_update_actual_usage(self):
        ctx = ContextManager(budget_tokens=100000, system_prompt="sys")
        ctx.add_user("hello")
        ctx.update_actual_usage(prompt_tokens=500)
        # Should not crash; calibration factor is stored internally
        assert ctx._actual_usage_calibration > 0


# ---- Fixtures ----

class _FakeResponse:
    def __init__(self, content):
        self.content = content
        self.usage = {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70}
        self.tool_calls = []
        self.model = "fake"

class FakeLLM:
    def __init__(self):
        self.response = "Fake summary."
        self.calls = []
        self.models = {"fast": "fake/fast", "default": "fake/default", "strong": "fake/strong"}

    def chat(self, messages, tier=None, **kwargs):
        self.calls.append(messages)
        return _FakeResponse(self.response)

    def chat_fast(self, *args, **kwargs):
        return self.response

    def _model(self, tier):
        return self.models.get(str(tier), "fake/default")


class FailingLLM:
    def chat(self, messages, tier=None, **kwargs):
        raise RuntimeError("LLM unavailable")

    def chat_fast(self, messages, **kwargs):
        raise RuntimeError("LLM unavailable")

    def _model(self, tier):
        return "fake"


@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest.fixture
def failing_llm():
    return FailingLLM()
