from __future__ import annotations
import json
import pytest
from unittest.mock import MagicMock, patch
from repopilot.llm.service import LLMService, LLMResponse, Tier, _is_retryable
from repopilot.llm.circuit_breaker import CircuitOpenError
import openai


def _make_completion(content="Hello", tool_calls=None, usage=None):
    """Build a fake litellm-compatible response object."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = None
    if tool_calls:
        tc_list = []
        for name, args in tool_calls:
            fn = MagicMock()
            fn.name = name
            fn.arguments = json.dumps(args)
            tc = MagicMock()
            tc.id = "call_1"
            tc.function = fn
            tc_list.append(tc)
        msg.tool_calls = tc_list
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    u = MagicMock()
    u.prompt_tokens = (usage or {}).get("prompt_tokens", 100)
    u.completion_tokens = (usage or {}).get("completion_tokens", 50)
    u.total_tokens = u.prompt_tokens + u.completion_tokens
    resp.usage = u
    return resp


def _make_llm():
    return LLMService(
        model="openai/fake-default",
        fast_model="openai/fake-fast",
        strong_model="openai/fake-strong",
        max_retries=2,
        backoff_base=0.001,  # tiny for tests
    )


@patch("repopilot.llm.service.litellm.completion")
def test_chat_returns_parsed_response(mock_c):
    mock_c.return_value = _make_completion("Hi there")
    svc = _make_llm()
    r = svc.chat([{"role": "user", "content": "hi"}], tier=Tier.DEFAULT)
    assert isinstance(r, LLMResponse)
    assert r.content == "Hi there"
    assert r.model == "openai/fake-default"
    assert r.usage["prompt_tokens"] == 100


@patch("repopilot.llm.service.litellm.completion")
def test_chat_fast_uses_fast_model(mock_c):
    mock_c.return_value = _make_completion("fast answer")
    svc = _make_llm()
    svc.chat([{"role": "user", "content": "hi"}], tier=Tier.FAST)
    assert mock_c.call_args.kwargs["model"] == "openai/fake-fast"


@patch("repopilot.llm.service.litellm.completion")
def test_chat_strong_uses_strong_model(mock_c):
    mock_c.return_value = _make_completion("strong answer")
    svc = _make_llm()
    svc.chat([{"role": "user", "content": "hi"}], tier=Tier.STRONG)
    assert mock_c.call_args.kwargs["model"] == "openai/fake-strong"


@patch("repopilot.llm.service.litellm.completion")
def test_tool_calls_parsed(mock_c):
    mock_c.return_value = _make_completion(
        content="",
        tool_calls=[("bash", {"command": "ls"})]
    )
    svc = _make_llm()
    r = svc.chat([{"role": "user", "content": "list files"}],
                 tools=[{"type": "function", "function": {"name": "bash"}}])
    assert len(r.tool_calls) == 1
    assert r.tool_calls[0]["name"] == "bash"
    assert r.tool_calls[0]["arguments"] == {"command": "ls"}


@patch("repopilot.llm.service.litellm.completion")
def test_retry_on_retryable_error(mock_c):
    import openai
    # First call fails with rate limit, second succeeds
    mock_c.side_effect = [
        openai.RateLimitError("rate limited", response=MagicMock(status_code=429), body=None),
        _make_completion("recovered"),
    ]
    svc = _make_llm()
    r = svc.chat([{"role": "user", "content": "hi"}])
    assert r.content == "recovered"
    assert mock_c.call_count == 2


@patch("repopilot.llm.service.litellm.completion")
def test_no_retry_on_auth_error(mock_c):
    import openai
    mock_c.side_effect = openai.AuthenticationError(
        "bad key", response=MagicMock(status_code=401), body=None
    )
    svc = _make_llm()
    with pytest.raises(openai.AuthenticationError):
        svc.chat([{"role": "user", "content": "hi"}])
    assert mock_c.call_count == 1  # no retry


@patch("repopilot.llm.service.litellm.completion")
def test_circuit_breaker_opens_after_failures(mock_c):
    import openai
    mock_c.side_effect = openai.APIConnectionError(request=None)
    svc = _make_llm()
    # max_retries=2 means 3 attempts, all fail -> failure recorded once per top-level call
    for _ in range(5):
        try:
            svc.chat([{"role": "user", "content": "hi"}])
        except openai.APIConnectionError:
            pass
    # After many failures circuit should be open
    assert svc.cb.state == "open"
    with pytest.raises(CircuitOpenError):
        svc.chat([{"role": "user", "content": "hi"}])


@patch("repopilot.llm.service.litellm.completion")
def test_convenience_methods(mock_c):
    mock_c.return_value = _make_completion("ok")
    svc = _make_llm()
    assert svc.chat_fast("sys", "usr") == "ok"
    assert svc.chat_strong("sys", "usr") == "ok"
    assert svc.chat_default("sys", "usr") == "ok"


@patch("repopilot.llm.service.litellm.completion")
def test_timeout_passed_correctly(mock_c):
    mock_c.return_value = _make_completion("ok")
    svc = _make_llm()
    svc.chat([{"role": "user", "content": "hi"}], tier=Tier.FAST)
    assert mock_c.call_args.kwargs["timeout"] == 30
    svc.chat([{"role": "user", "content": "hi"}], tier=Tier.STRONG)
    assert mock_c.call_args.kwargs["timeout"] == 300


def test_stream_not_implemented():
    svc = _make_llm()
    with pytest.raises(NotImplementedError):
        svc.chat([{"role": "user", "content": "hi"}], stream=True)


def test_is_retryable_classification():
    import openai
    fake_req = MagicMock()
    assert _is_retryable(openai.APITimeoutError("timeout")) is True
    assert _is_retryable(openai.APIConnectionError(request=fake_req)) is True
    assert _is_retryable(openai.RateLimitError("r", response=MagicMock(status_code=429), body=None)) is True
    assert _is_retryable(openai.AuthenticationError("a", response=MagicMock(status_code=401), body=None)) is False
    assert _is_retryable(openai.BadRequestError("b", response=MagicMock(status_code=400), body=None)) is False
