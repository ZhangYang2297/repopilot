from __future__ import annotations
import enum
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import litellm

from repopilot.llm.circuit_breaker import CircuitBreaker, CircuitOpenError

litellm.drop_params = True
litellm.set_verbose = False
# Suppress litellm info logs during normal operation
logging.getLogger("LiteLLM").setLevel(logging.WARNING)

_logger = logging.getLogger("repopilot.llm")


class Tier(str, enum.Enum):
    FAST = "fast"
    DEFAULT = "default"
    STRONG = "strong"


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    model: str = ""


# Exception classification
def _is_retryable(exc: Exception) -> bool:
    """Decide if an LLM error is worth retrying."""
    import openai
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError)):
        return True
    if isinstance(exc, openai.BadRequestError):
        # context length exceeded is not retryable
        return False
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError,
                        openai.NotFoundError)):
        return False
    if isinstance(exc, openai.APIStatusError):
        status = getattr(exc, "status_code", 500)
        return status == 429 or status >= 500
    # litellm exceptions may wrap httpx errors
    if "timeout" in type(exc).__name__.lower():
        return True
    return False


class LLMService:
    """Unified LLM client with 3-tier routing, retries, jitter, circuit breaker."""

    TIER_TIMEOUTS = {Tier.FAST: 30, Tier.DEFAULT: 120, Tier.STRONG: 300}

    def __init__(
        self,
        model: str,
        fast_model: str,
        strong_model: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        max_retries: int = 2,
        backoff_base: float = 0.5,
        backoff_cap: float = 8.0,
    ):
        self.models = {
            Tier.DEFAULT: model,
            Tier.FAST: fast_model,
            Tier.STRONG: strong_model,
        }
        self.api_key = api_key
        self.base_url = base_url
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.cb = CircuitBreaker()

    def _model(self, tier: Tier) -> str:
        return self.models.get(tier, self.models[Tier.DEFAULT])

    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.3,
        tier: Tier = Tier.DEFAULT,
        stream: bool = False,
    ) -> LLMResponse:
        """Synchronous chat (non-streaming). Streaming support added later."""
        if stream:
            raise NotImplementedError("async streaming implemented in Task 14 via achat")

        if not self.cb.allow_request():
            raise CircuitOpenError(f"Circuit open for model {self._model(tier)}")

        kwargs: dict[str, Any] = dict(
            model=self._model(tier),
            messages=messages,
            temperature=temperature,
            timeout=self.TIER_TIMEOUTS[tier],
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.base_url:
            kwargs["api_base"] = self.base_url

        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                _logger.debug("LLM call model=%s attempt=%d", kwargs["model"], attempt)
                resp = litellm.completion(**kwargs)
                self.cb.record_success()
                return self._parse_response(resp, kwargs["model"])
            except Exception as e:
                last_exc = e
                if not _is_retryable(e):
                    self.cb.record_failure()
                    raise
                if attempt == self.max_retries:
                    self.cb.record_failure()
                    raise
                delay = min(self.backoff_cap, self.backoff_base * (2 ** attempt))
                delay = delay * (0.5 + random.random() * 0.5)  # full jitter
                _logger.warning("Retryable LLM error (%s), retry in %.1fs: %s",
                                type(e).__name__, delay, str(e)[:120])
                time.sleep(delay)
        raise last_exc  # type: ignore[misc]

        raise last_exc  # type: ignore[misc]

    def chat_stream(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.3,
        tier: Tier = Tier.DEFAULT,
    ):
        """Streaming chat: yields event dicts.

        Yields dicts with keys:
          - {"type": "text_delta", "content": str}  — incremental text
          - {"type": "tool_call", "id": str, "name": str, "arguments": dict}  — complete tool call
          - {"type": "done", "response": LLMResponse, "usage": dict}  — end of stream

        Usage:
            for event in llm.chat_stream(messages, tools=schemas):
                if event["type"] == "text_delta": ...
        """
        if not self.cb.allow_request():
            raise CircuitOpenError(f"Circuit open for model {self._model(tier)}")

        kwargs: dict[str, Any] = dict(
            model=self._model(tier),
            messages=messages,
            temperature=temperature,
            timeout=self.TIER_TIMEOUTS[tier],
            stream=True,
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.base_url:
            kwargs["api_base"] = self.base_url

        request_started_at = time.perf_counter()
        first_event_emitted = False

        try:
            stream = litellm.completion(**kwargs)
        except Exception as e:
            if not _is_retryable(e):
                self.cb.record_failure()
                raise
            # One retry for stream (simpler than non-stream)
            _logger.warning("Stream error (%s), retrying once: %s", type(e).__name__, str(e)[:120])
            time.sleep(1)
            try:
                stream = litellm.completion(**kwargs)
            except Exception:
                self.cb.record_failure()
                raise

        accumulated_text = ""
        tool_calls: dict[int, dict] = {}
        final_usage: dict = {}

        try:
            for chunk in stream:
                # Extract usage from final chunk
                if getattr(chunk, "usage", None):
                    u = chunk.usage
                    final_usage = {
                        "prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
                        "completion_tokens": getattr(u, "completion_tokens", 0) or 0,
                        "total_tokens": getattr(u, "total_tokens", 0) or 0,
                    }

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                # Text delta
                if getattr(delta, "content", None):
                    text = delta.content
                    accumulated_text += text
                    event = {"type": "text_delta", "content": text}
                    if not first_event_emitted:
                        event["ttft_ms"] = max(
                            1, round((time.perf_counter() - request_started_at) * 1000)
                        )
                        first_event_emitted = True
                    yield event

                # Tool call deltas
                if getattr(delta, "tool_calls", None):
                    for tc_delta in delta.tool_calls:
                        idx = getattr(tc_delta, "index", 0)
                        if idx not in tool_calls:
                            tool_calls[idx] = {
                                "id": getattr(tc_delta, "id", f"call_{idx}"),
                                "name": "",
                                "arguments_raw": "",
                            }
                        fn = getattr(tc_delta, "function", None)
                        if fn:
                            if getattr(fn, "name", None):
                                tool_calls[idx]["name"] = fn.name
                            if getattr(fn, "arguments", None):
                                tool_calls[idx]["arguments_raw"] += fn.arguments
        except Exception as e:
            self.cb.record_failure()
            raise

        self.cb.record_success()

        # Emit complete tool calls
        parsed_tool_calls = []
        for idx in sorted(tool_calls.keys()):
            tc = tool_calls[idx]
            args_str = tc["arguments_raw"]
            try:
                args = json.loads(args_str) if args_str.strip() else {}
            except json.JSONDecodeError:
                args = {"_raw": args_str}
            parsed_tc = {
                "id": tc["id"],
                "name": tc["name"],
                "arguments": args,
            }
            parsed_tool_calls.append(parsed_tc)
            event = {"type": "tool_call", **parsed_tc}
            if not first_event_emitted:
                event["ttft_ms"] = max(
                    1, round((time.perf_counter() - request_started_at) * 1000)
                )
                first_event_emitted = True
            yield event

        # Build final LLMResponse
        response = LLMResponse(
            content=accumulated_text,
            tool_calls=[{"id": tc["id"], "name": tc["name"], "arguments": tc["arguments"]} for tc in parsed_tool_calls],
            usage=final_usage,
            model=self._model(tier),
        )
        yield {
            "type": "done",
            "response": response,
            "usage": final_usage,
            "total_duration_ms": max(
                1, round((time.perf_counter() - request_started_at) * 1000)
            ),
        }

    # Convenience methods
    def chat_messages(self, messages: list[dict], tier: Tier = Tier.DEFAULT, **kw) -> LLMResponse:
        """Chat with a full messages list."""
        return self.chat(messages, tier=tier, **kw)

    def chat_fast(self, *args, **kw) -> str:
        """Fast-tier chat. Accepts either (messages: list) or (system: str, user: str)."""
        if len(args) == 1 and isinstance(args[0], list):
            return self.chat(args[0], tier=Tier.FAST, **kw).content
        system = args[0] if len(args) >= 1 else kw.pop("system", "")
        user = args[1] if len(args) >= 2 else kw.pop("user", "")
        return self.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            tier=Tier.FAST, **kw,
        ).content

    def chat_strong(self, system: str, user: str, **kw) -> str:
        return self.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            tier=Tier.STRONG, **kw,
        ).content

    def chat_default(self, system: str, user: str, **kw) -> str:
        return self.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            tier=Tier.DEFAULT, **kw,
        ).content

    def _parse_response(self, resp, model: str) -> LLMResponse:
        choice = resp.choices[0].message
        tool_calls: list[dict] = []
        if getattr(choice, "tool_calls", None):
            for tc in choice.tool_calls:
                fn = tc.function
                try:
                    args = json.loads(fn.arguments or "{}")
                except json.JSONDecodeError:
                    args = {"_raw": fn.arguments}
                tool_calls.append({
                    "id": getattr(tc, "id", ""),
                    "name": fn.name,
                    "arguments": args,
                })
        usage: dict = {}
        if getattr(resp, "usage", None):
            u = resp.usage
            usage = {
                "prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(u, "completion_tokens", 0) or 0,
                "total_tokens": getattr(u, "total_tokens", 0) or 0,
            }
        return LLMResponse(
            content=choice.content or "",
            tool_calls=tool_calls,
            usage=usage,
            model=model,
        )


def build_llm_from_settings(settings) -> LLMService:
    """Create an LLMService from Settings.

    API key resolution: settings.api_key (config.toml) wins; fall back to env
    OPENAI_API_KEY / DASHSCOPE_API_KEY / ARK_API_KEY only if no key configured.
    """
    import os as _os
    api_key = settings.api_key or None
    base_url = settings.base_url or None
    if not api_key:
        for ek in ("OPENAI_API_KEY", "DASHSCOPE_API_KEY", "ARK_API_KEY", "ANTHROPIC_API_KEY"):
            v = _os.environ.get(ek)
            if v:
                api_key = v
                break
    return LLMService(
        model=settings.model,
        fast_model=settings.fast_model,
        strong_model=settings.strong_model,
        api_key=api_key,
        base_url=base_url,
        max_retries=1,
    )

