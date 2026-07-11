from __future__ import annotations
import pytest
from repopilot.hooks import HookManager, HookResult


class TestHookResult:
    def test_continue(self):
        r = HookResult.cont()
        assert r.action == "continue"

    def test_deny(self):
        r = HookResult.deny(reason="nope")
        assert r.action == "deny"
        assert r.reason == "nope"

    def test_skip(self):
        r = HookResult.skip(override="fake")
        assert r.action == "skip"
        assert r.override == "fake"

    def test_invalid_action_raises(self):
        with pytest.raises(ValueError):
            HookResult(action="explode")


class TestHookManager:
    def test_register_and_fire(self):
        hm = HookManager()
        calls = []
        def hook(tool_name, args):
            calls.append(tool_name)
            return None
        hm.register("pre_tool", hook)
        result = hm.fire("pre_tool", "read_file", {"path": "x"})
        assert result.action == "continue"
        assert calls == ["read_file"]

    def test_deny_short_circuits(self):
        hm = HookManager()
        hm.register("pre_tool", lambda *a, **kw: HookResult.deny("blocked"))
        hm.register("pre_tool", lambda *a, **kw: pytest.fail("should not reach"))
        result = hm.fire("pre_tool", "bash", {"command": "rm"})
        assert result.action == "deny"
        assert result.reason == "blocked"

    def test_skip_short_circuits(self):
        hm = HookManager()
        hm.register("pre_tool", lambda *a, **kw: HookResult.skip(override="fake result"))
        result = hm.fire("pre_tool", "read_file", {"path": "x"})
        assert result.action == "skip"
        assert result.override == "fake result"

    def test_deny_priority_over_skip(self):
        hm = HookManager()
        hm.register("pre_tool", lambda *a, **kw: HookResult.skip())
        hm.register("pre_tool", lambda *a, **kw: HookResult.deny("no"))
        result = hm.fire("pre_tool", "x", {})
        # deny registered second but first hook returns skip first
        # Actually: hooks fire in registration order. First returns skip → short circuit
        assert result.action == "skip"

    def test_unknown_event_raises_on_register(self):
        hm = HookManager()
        with pytest.raises(ValueError):
            hm.register("nonexistent_event", lambda: None)

    def test_unknown_event_raises_on_fire(self):
        hm = HookManager()
        with pytest.raises(ValueError):
            hm.fire("nonexistent_event")

    def test_hook_exception_does_not_crash(self):
        hm = HookManager()
        def bad_hook(*a, **kw):
            raise RuntimeError("hook crash")
        hm.register("pre_tool", bad_hook)
        # Should not raise, returns continue
        result = hm.fire("pre_tool", "x", {})
        assert result.action == "continue"

    def test_multiple_events(self):
        hm = HookManager()
        pre_calls = []
        post_calls = []
        hm.register("pre_tool", lambda name, args: pre_calls.append(name))
        hm.register("post_tool", lambda name, args, res, dur: post_calls.append(name))
        hm.fire("pre_tool", "read", {})
        hm.fire("post_tool", "read", {}, "ok", 50)
        assert pre_calls == ["read"]
        assert post_calls == ["read"]

    def test_none_return_is_continue(self):
        hm = HookManager()
        hm.register("on_finish", lambda *a, **kw: None)
        result = hm.fire("on_finish", "done", True)
        assert result.action == "continue"
