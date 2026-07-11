from __future__ import annotations
from repopilot.agent.cost import CostTracker, _lookup_price


class TestPriceLookup:
    def test_known_model(self):
        inp, out = _lookup_price("openai/doubao-seed-evolving")
        assert inp > 0
        assert out > 0

    def test_unknown_model_returns_zero(self):
        inp, out = _lookup_price("nonexistent-model-xyz")
        assert inp == 0.0
        assert out == 0.0


class TestCostTracker:
    def test_initial_state(self):
        ct = CostTracker()
        s = ct.summary()
        assert s["llm_calls"] == 0
        assert s["total_tokens"] == 0
        assert s["tool_calls"] == 0
        assert s["estimated_cost_usd"] == 0.0

    def test_single_llm_call(self):
        ct = CostTracker()
        ct.on_llm_call({"prompt_tokens": 1000, "completion_tokens": 500},
                        "openai/doubao-seed-evolving")
        s = ct.summary()
        assert s["llm_calls"] == 1
        assert s["prompt_tokens"] == 1000
        assert s["completion_tokens"] == 500
        assert s["total_tokens"] == 1500
        assert s["estimated_cost_usd"] > 0

    def test_accumulates(self):
        ct = CostTracker()
        ct.on_llm_call({"prompt_tokens": 1000, "completion_tokens": 1000},
                        "openai/doubao-seed-evolving")
        ct.on_llm_call({"prompt_tokens": 2000, "completion_tokens": 500},
                        "openai/doubao-seed-evolving")
        s = ct.summary()
        assert s["llm_calls"] == 2
        assert s["prompt_tokens"] == 3000
        assert s["completion_tokens"] == 1500

    def test_tool_call_tracking(self):
        ct = CostTracker()
        ct.on_tool_call("read_file", 150)
        ct.on_tool_call("bash", 2000)
        s = ct.summary()
        assert s["tool_calls"] == 2
        assert s["tool_duration_s"] == 2.15

    def test_per_model_breakdown(self):
        ct = CostTracker()
        ct.on_llm_call({"prompt_tokens": 100, "completion_tokens": 50},
                        "openai/doubao-seed-evolving")
        ct.on_llm_call({"prompt_tokens": 50, "completion_tokens": 20},
                        "openai/doubao-seed-1-6-flash-250828")
        s = ct.summary()
        assert "openai/doubao-seed-evolving" in s["per_model"]
        assert "openai/doubao-seed-1-6-flash-250828" in s["per_model"]

    def test_reset(self):
        ct = CostTracker()
        ct.on_llm_call({"prompt_tokens": 1000, "completion_tokens": 500},
                        "openai/doubao-seed-evolving")
        ct.reset()
        s = ct.summary()
        assert s["llm_calls"] == 0
        assert s["total_tokens"] == 0
        assert s["estimated_cost_usd"] == 0.0

    def test_format_summary(self):
        ct = CostTracker()
        ct.on_tool_call("read", 100)
        text = ct.format_summary()
        assert "LLM calls" in text
        assert "Tools: 1 calls" in text

    def test_missing_usage_keys(self):
        ct = CostTracker()
        ct.on_llm_call({}, "unknown-model")
        s = ct.summary()
        assert s["total_tokens"] == 0
        assert s["llm_calls"] == 1
