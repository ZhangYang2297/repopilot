"""Agent Loop — Plan-Act-ReAct cycle (pure ReAct, no separate planner/reflector).

Architecture (aligned with Claude Code / Codex CLI):
1. Build system prompt (with repo map, memory, injected skills)
2. User task → ContextManager
3. Loop:
   a. build_messages from ContextManager
   b. Call LLM (streaming optional)
   c. Parse response (tool_calls / text / finish)
   d. If tool_calls: execute each tool (permission check → sandbox → result)
   e. If text (no tool calls): this is the final answer → finish
   f. If finish tool: done
   g. Check token budget, trigger auto-compact if needed
   h. Check max_steps to prevent infinite loops
4. Return RunResult with trajectory, cost, steps
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from repopilot.agent.compact import tool_compact
from repopilot.agent.context import ContextManager
from repopilot.agent.parser import ParsedResponse, parse_response
from repopilot.hooks.manager import HookManager, HookResult
from repopilot.llm.service import LLMService, Tier
from repopilot.permission.engine import PermissionEngine
from repopilot.sandbox.base import Sandbox
from repopilot.session.store import SessionStore, Session
from repopilot.tools.base import AgentFinished, ApprovalRequired, Tool
from repopilot.tools.registry import ToolRegistry
from repopilot.tools.result import ToolResult
from repopilot.memory import load_memory as _load_memory


@dataclass
class RunResult:
    """Result of an agent run."""
    status: str  # "completed" | "error" | "max_steps" | "cancelled"
    summary: str = ""
    steps: int = 0
    trajectory: list[dict] = field(default_factory=list)
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    duration_ms: int = 0
    session_id: str = ""
    error: str = ""


@dataclass
class StreamEvent:
    """Event emitted during agent run for streaming UI updates."""
    type: str  # "thinking" | "tool_call" | "tool_result" | "text" | "finish" | "error" | "compact"
    data: dict = field(default_factory=dict)


# Type for stream callback
StreamCallback = Callable[[StreamEvent], None]


def _load_system_prompt() -> str:
    """Load the system prompt from the prompts directory."""
    import platform as _platform
    prompt_path = Path(__file__).parent / "prompts" / "system.md"
    if prompt_path.exists():
        text = prompt_path.read_text(encoding="utf-8")
        plat = _platform.system()  # "Windows", "Linux", "Darwin"
        return text.replace("{platform}", plat)
    return "You are a helpful coding assistant."


def _register_default_tools(registry: ToolRegistry) -> None:
    """Register all built-in tools."""
    from repopilot.tools.file_tools import ReadFileTool, WriteFileTool, EditFileTool
    from repopilot.tools.search_tools import GrepTool, GlobTool, ListDirTool, RepoTreeTool
    from repopilot.tools.exec_tools import BashTool, RunPythonTool
    from repopilot.tools.meta_tools import FinishTool

    for cls in [ReadFileTool, WriteFileTool, EditFileTool,
                GrepTool, GlobTool, ListDirTool, RepoTreeTool,
                BashTool, RunPythonTool, FinishTool]:
        registry.register(cls())


def run_agent(
    task: str,
    repo_path: Path,
    llm: LLMService,
    sandbox: Sandbox,
    config: Any = None,
    permission_engine: Optional[PermissionEngine] = None,
    session_store: Optional[SessionStore] = None,
    hooks: Optional[HookManager] = None,
    tools: Optional[list[Tool]] = None,
    system_prompt: Optional[str] = None,
    max_steps: int = 50,
    budget_tokens: int = 200000,
    stream_callback: Optional[StreamCallback] = None,
    verbose: bool = False,
) -> RunResult:
    """Run the ReAct agent loop on a task.

    Args:
        task: User's natural language task description.
        repo_path: Path to the repository.
        llm: Configured LLMService instance.
        sandbox: Sandbox (Local or Docker) for file/exec operations.
        config: Settings instance (optional, for model names etc).
        permission_engine: PermissionEngine instance (default: auto mode).
        session_store: SessionStore for persisting events (optional).
        hooks: HookManager for lifecycle events (optional).
        tools: Extra tools to register (in addition to defaults).
        system_prompt: Override system prompt.
        max_steps: Maximum agent steps before forced termination.
        budget_tokens: Token budget for context window.
        stream_callback: Callback for streaming UI events.
        verbose: Emit debug events.
    """
    t0 = time.time()

    # ── Setup defaults ──────────────────────────
    if permission_engine is None:
        permission_engine = PermissionEngine(mode="auto")
    if hooks is None:
        hooks = HookManager()
    sys_prompt = system_prompt or _load_system_prompt()

    # ── Tool registry ───────────────────────────
    registry = ToolRegistry(permission_engine=permission_engine)
    _register_default_tools(registry)
    if tools:
        for t in tools:
            registry.register(t)

    # ── Repo map (initial scan) ────────────────
    repo_map_str = sandbox.get_repo_tree(max_tokens=4000)

    # ── Context manager ─────────────────────────
    ctx = ContextManager(
        budget_tokens=budget_tokens,
        system_prompt=sys_prompt,
        repo_map_str=repo_map_str,
        memory_str=_load_memory(repo_path),
    )

    # ── Session ─────────────────────────────────
    session: Optional[Session] = None
    if session_store is not None:
        model_name = llm._model(Tier.DEFAULT) if hasattr(llm, "_model") else ""
        session = session_store.create(
            title=task[:80],
            cwd=str(repo_path),
            model=model_name,
        )

    def _emit(event_type: str, **data):
        if stream_callback:
            stream_callback(StreamEvent(type=event_type, data=data))

    def _record(event_type: str, payload: dict):
        if session and session_store:
            try:
                session_store.append_event(session.id, event_type, payload)
            except Exception:
                pass

    # ── Build tool schemas ──────────────────────
    tool_schemas = registry.schemas()

    # ── Inject task as first user message ──────
    ctx.add_user(task)
    _record("user_msg", {"content": task})
    _emit("text", content=task, role="user")

    steps = 0
    total_tokens = 0
    total_cost = 0.0
    trajectory: list[dict] = []
    status = "error"
    summary = ""
    error_msg = ""

    try:
        while steps < max_steps:
            steps += 1

            # ── Check compaction ────────────────
            compaction_level = ctx.needs_compaction()
            if compaction_level:
                _emit("compact", level=compaction_level)
                try:
                    from repopilot.agent.compact import micro_compact, auto_compact
                    if compaction_level == "auto":
                        result = auto_compact(
                            [s.to_message() for s in ctx.steps], llm, keep_recent=10
                        )
                    else:
                        result = micro_compact(
                            [s.to_message() for s in ctx.steps[:5]], llm
                        )
                    ctx.compact(compaction_level, llm)
                    _record("compact", {"level": compaction_level, "steps_compacted": result.steps_compacted})
                except Exception as e:
                    if verbose:
                        _emit("error", message=f"Compaction failed: {e}")

            # ── Pre-LLM hook ────────────────────
            messages = ctx.build_messages()
            hook_result = hooks.fire("pre_llm", messages=messages, tools=tool_schemas, tier=Tier.DEFAULT)
            if hook_result.action == "deny":
                raise RuntimeError(f"pre_llm hook denied: {hook_result.reason}")
            if hook_result.action == "skip" and hook_result.override is not None:
                # Use fake response from hook (for testing/mocking)
                llm_response = hook_result.override
            else:
                # ── Call LLM ───────────────────
                _emit("thinking", step=steps)
                try:
                    llm_response = llm.chat(
                        messages=messages,
                        tools=tool_schemas,
                        tier=Tier.DEFAULT,
                        temperature=0.2,
                    )
                except Exception as e:
                    error_msg = f"LLM error: {e}"
                    _emit("error", message=error_msg)
                    _record("error", {"error": str(e)})
                    status = "error"
                    break

            # ── Track usage ────────────────────
            usage = llm_response.usage
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens += usage.get("total_tokens", prompt_tokens + completion_tokens)
            if prompt_tokens:
                ctx.update_actual_usage(prompt_tokens)

            hooks.fire("post_llm", response=llm_response)

            # ── Parse response ────────────────
            parsed = parse_response(
                content=llm_response.content,
                tool_calls=llm_response.tool_calls,
                finish_reason="stop",
            )

            _record("assistant_msg", {
                "content": llm_response.content,
                "tool_calls": llm_response.tool_calls,
                "usage": usage,
            })

            # ── Handle tool calls ─────────────
            if parsed.is_tool_call:
                ctx.add_assistant(parsed.content, tool_calls=[
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": str(tc["arguments"])},
                    }
                    for tc in parsed.tool_calls
                ])

                for tc in parsed.tool_calls:
                    tool_name = tc["name"]
                    tool_args = tc["arguments"]
                    call_id = tc["id"]

                    _emit("tool_call", name=tool_name, args=tool_args, step=steps)
                    _record("tool_call", {"tool": tool_name, "args": tool_args, "call_id": call_id})

                    # ── Pre-tool hook ─────────
                    pre_result = hooks.fire("pre_tool", tool_name=tool_name, args=tool_args)
                    if pre_result.action == "deny":
                        tool_result = ToolResult(error=f"Hook denied: {pre_result.reason}")
                    elif pre_result.action == "skip" and pre_result.override is not None:
                        tool_result = pre_result.override
                    else:
                        # ── Execute tool ──────
                        try:
                            tool_result = registry.execute(tool_name, tool_args, sandbox)
                        except ApprovalRequired as ar:
                            tool_result = ToolResult(error=f"Approval required for {ar.tool_name}: {ar.reason}. Use --approval-mode auto.")
                        except AgentFinished as af:
                            # Finish tool raised AgentFinished
                            summary = af.summary
                            status = "completed"
                            _record("finish", {"summary": summary, "tests_passed": af.tests_passed})
                            _emit("finish", summary=summary, steps=steps)
                            hooks.fire("on_finish", summary=summary, tests_passed=af.tests_passed)
                            break
                        except Exception as e:
                            tool_result = ToolResult(error=f"{type(e).__name__}: {e}")

                    # ── Post-tool hook ────────
                    post_result = hooks.fire("post_tool",
                                            tool_name=tool_name, args=tool_args,
                                            result=tool_result, duration_ms=0)
                    if post_result.override is not None:
                        tool_result = post_result.override

                    result_str = tool_result.content if not tool_result.error else f"Error: {tool_result.error}"
                    _emit("tool_result", name=tool_name, result=result_str[:2000])
                    _record("tool_result", {
                        "tool": tool_name,
                        "call_id": call_id,
                        "content": tool_result.content,
                        "error": tool_result.error,
                        "metadata": tool_result.metadata,
                    })

                    # Add tool result to context
                    is_error = bool(tool_result.error)
                    ctx.add_tool_result(call_id, result_str, is_error=is_error)
                    trajectory.append({
                        "step": steps,
                        "tool": tool_name,
                        "args": tool_args,
                        "result": result_str[:5000],
                        "error": tool_result.error,
                    })

                else:
                    # Inner for-loop completed without break → continue to next step
                    continue
                # Broke out of for loop due to finish → break while loop
                break

            elif parsed.is_finish:
                # Finish detected via tool or XML tag
                summary = parsed.content or "Task completed."
                status = "completed"
                _record("finish", {"summary": summary})
                _emit("finish", summary=summary, steps=steps)
                hooks.fire("on_finish", summary=summary, tests_passed=True)
                ctx.add_assistant(summary)
                break

            else:
                # ── Plain text response (no tool calls) → final answer ──
                answer = parsed.content.strip()
                if answer:
                    ctx.add_assistant(answer)
                    summary = answer
                    _emit("text", content=answer, role="assistant")
                status = "completed"
                _record("assistant_msg", {"content": answer})
                hooks.fire("on_finish", summary=summary or "Done", tests_passed=True)
                _emit("finish", summary=summary or "Done", steps=steps)
                break

        else:
            # Max steps reached
            status = "max_steps"
            summary = f"Reached maximum steps ({max_steps}) without completing."
            error_msg = summary
            _emit("error", message=summary)
            _record("error", {"error": summary})

    except KeyboardInterrupt:
        status = "cancelled"
        summary = "Cancelled by user."
        _emit("error", message=summary)
    except Exception as e:
        status = "error"
        error_msg = f"{type(e).__name__}: {e}"
        summary = error_msg
        _emit("error", message=error_msg)
        _record("error", {"error": error_msg})
        hooks.fire("on_error", exception=e)

    duration_ms = int((time.time() - t0) * 1000)

    return RunResult(
        status=status,
        summary=summary,
        steps=steps,
        trajectory=trajectory,
        total_tokens=total_tokens,
        total_cost_usd=total_cost,
        duration_ms=duration_ms,
        session_id=session.id if session else "",
        error=error_msg,
    )

