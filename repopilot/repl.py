"""Interactive REPL - persistent multi-turn conversation (Claude Code / Codex CLI style)."""
from __future__ import annotations

import json
import sys
import time
from collections import deque
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.prompt import Prompt, Confirm
from rich.syntax import Syntax

from repopilot.config import get_settings, reset_settings_for_tests, Settings
from repopilot.llm.service import build_llm_from_settings, LLMService, Tier
from repopilot.sandbox import LocalSandbox, DockerSandbox
from repopilot.permission.engine import PermissionEngine
from repopilot.hooks.manager import HookManager
from repopilot.hooks.builtin import install_builtin_hooks
from repopilot.agent.cost import CostTracker
from repopilot.session.store import SessionStore
from repopilot.agent.context import ContextManager
from repopilot.agent.parser import parse_response
from repopilot.agent.loop import _load_system_prompt, _register_default_tools
from repopilot.agent.compact import tool_compact
from repopilot.agent.engine import AgentLoopCore
from repopilot.agent.diff_tracker import DiffTracker
from repopilot.agent.tool_display import format_tool_line, format_result_suffix
from repopilot.agent.loop_guard import LoopGuard
from repopilot.agent.finish_verify import run_verify
from repopilot.tools.base import ApprovalRequired
from repopilot.tools.result import ToolResult
from repopilot.memory import load_memory, create_global_memory, append_to_project_memory, append_to_global_memory

class ReplInput:
    """Shared input source for main prompts and approval prompts."""

    def __init__(self, is_tty: bool):
        self._is_tty = is_tty
        self._queue: deque[str] = deque()
        if not is_tty:
            try:
                raw = sys.stdin.read()
                self._queue.extend(
                    line.strip() for line in raw.split("\n") if line.strip()
                )
            except Exception:
                pass

    @classmethod
    def from_lines(cls, lines: list[str]) -> "ReplInput":
        source = cls.__new__(cls)
        source._is_tty = False
        source._queue = deque(lines)
        return source

    def ask_user(self) -> Optional[str]:
        if self._is_tty:
            if not hasattr(self, "_console"):
                self._console = Console()
            # Rule line separates each turn visually (Codex-CLI style).
            # Prompt label restored to "repopilot:" so the input row is
            # obviously distinct from assistant output.
            self._console.rule(style="dim")
            return self._console.input("[bold green]repopilot:[/bold green] ")
        return self._queue.popleft() if self._queue else None

    def ask_approval(self) -> str:
        if self._is_tty:
            return Prompt.ask(
                "[bold]Allow?[/bold] [green]y[/green]=yes  [red]n[/red]=no  [yellow]a[/yellow]=always allow  [magenta]d[/magenta]=deny mode",
                choices=["y", "n", "a", "d"],
                default="n",
                show_choices=False,
            )
        return self._queue.popleft() if self._queue else "n"

HELP_TEXT = """
**Slash commands:**

| Command | Description |
|---------|-------------|
| `/exit`, `/quit` | Exit RepoPilot (Ctrl+C/Ctrl+D also supported) |
| `/help` | Show this help |
| `/model [name]` | Show or switch model |
| `/approval [mode]` | Show/set approval mode (auto/confirm/edit-only/deny) |
| `/compact` | Trigger context compaction now |
| `/clear` | Start a fresh conversation |
| `/cd [path]` | Show or switch working directory |
| `/memory [note]` | Show memory files or append a note (add --global for global) |
| `/resume [id]` | Resume a previous session (default: most recent) |
| `/sessions` | List recent sessions |
| `/cost` | Show token usage and cost |
| `/status` | Show current configuration |
| `/diff` | Show file changes made during this session |
| `/undo` | Revert the last file change (add `turn` to revert the whole last turn) |
"""


# Tools whose writes we should snapshot for /diff and /undo.
_WRITE_TOOLS = {"write_file", "edit_file"}


class ReplSession:
    def __init__(self, repo_path: Path, llm: LLMService, sandbox_type: str,
                 approval_mode: str, settings: Settings, session_store: SessionStore,
                 session_id: str, console: Console, verbose: bool = False):
        self.repo_path = repo_path
        self.llm = llm
        self.sandbox_type = sandbox_type
        self.approval_mode = approval_mode
        self.settings = settings
        self.session_store = session_store
        self.session_id = session_id
        self.console = console
        self.verbose = verbose
        self.steps = 0
        self.diff_tracker = DiffTracker(str(repo_path))
        self.total_tokens = 0
        self.cost_tracker = CostTracker()
        self._streamed_answer = False
        self._build_context()

    def _make_permission_engine(self) -> PermissionEngine:
        return PermissionEngine(mode=self.approval_mode)

    def _make_sandbox(self):
        if self.sandbox_type == "docker":
            return DockerSandbox(
                self.repo_path,
                mem_limit=self.settings.docker_mem_limit,
                network_mode="bridge" if self.settings.docker_network else "none",
            )
        return LocalSandbox(self.repo_path)

    def _build_context(self) -> None:
        repo_map_str = ""
        tmp_sb = self._make_sandbox()
        try:
            tmp_sb.setup()
            repo_map_str = tmp_sb.get_repo_tree(max_tokens=4000)
        except Exception:
            pass
        finally:
            try:
                tmp_sb.teardown()
            except Exception:
                pass

        system_prompt = _load_system_prompt(
            sandbox_type=self.sandbox_type,
            approval_mode=self.approval_mode,
            config_path=str(self.settings.config_file),
            global_memory_path=str(self.settings.home_dir / "REPOPILOT.md"),
        )
        create_global_memory(self.settings.home_dir)
        memory_str = load_memory(self.repo_path, home_dir=self.settings.home_dir)
        self.ctx = ContextManager(
            budget_tokens=self.settings.budget_tokens,
            system_prompt=system_prompt,
            repo_map_str=repo_map_str,
            memory_str=memory_str,
        )
        from repopilot.tools.registry import ToolRegistry
        self.registry = ToolRegistry(permission_engine=self._make_permission_engine())
        _register_default_tools(self.registry)
        self.tool_schemas = self.registry.schemas()

        self.hooks = HookManager()
        install_builtin_hooks(self.hooks, cost_tracker=self.cost_tracker)

    # ── file change tracking helpers ─────────────────────────
    def _snapshot_before(self, tool_name: str, tool_args: dict):
        if tool_name not in _WRITE_TOOLS:
            return (None, False)
        rel = tool_args.get("path", "") or ""
        if not rel:
            return (None, False)
        fpath = (self.repo_path / rel).resolve()
        try:
            fpath.relative_to(self.repo_path.resolve())
        except ValueError:
            return (None, False)
        if fpath.exists() and fpath.is_file():
            try:
                return (fpath.read_text(encoding="utf-8", errors="replace"), True)
            except Exception:
                return (None, True)
        return (None, False)

    def _record_tool_change(self, tool_name, tool_args, result, before_content, existed_before) -> None:
        if tool_name not in _WRITE_TOOLS:
            return
        if getattr(result, "error", None):
            return
        rel = tool_args.get("path", "") or ""
        if not rel:
            return
        fpath = (self.repo_path / rel).resolve()
        try:
            after = fpath.read_text(encoding="utf-8", errors="replace") if fpath.exists() else ""
        except Exception:
            after = ""
        if tool_name == "write_file":
            if existed_before:
                self.diff_tracker.record_overwrite(rel, before_content or "", after)
            else:
                self.diff_tracker.record_new(rel, after)
        else:
            self.diff_tracker.record_edit(rel, before_content or "", after)

    # ── interactive approval ─────────────────────────────────
    def _interactive_approve(self, tool_name: str, args: dict, reason: str) -> bool:
        summary = self._summarize_call(tool_name, args)
        self.console.print(f"\n[yellow]Approval required[/yellow] [dim]({reason})[/dim]")
        self.console.print(f"  [cyan]{tool_name}[/cyan]  [dim]{summary}[/dim]")
        try:
            input_source = getattr(self, "input_source", None)
            if input_source is None:
                input_source = ReplInput(is_tty=True)
            choice = input_source.ask_approval()
        except (KeyboardInterrupt, EOFError):
            return False
        choice = (choice or "n").lower()
        pe = self.registry._permission
        if choice == "y":
            return True
        if choice == "a":
            if pe is not None:
                pe.remember_always(tool_name, args)
            return True
        if choice == "d":
            self.approval_mode = "deny"
            if pe is not None:
                pe.mode = "deny"
            self.console.print("[magenta]Switched to deny mode.[/magenta]")
            return False
        return False

    @staticmethod
    def _summarize_call(tool_name: str, args: dict) -> str:
        parts = []
        for k in ("path", "command", "old_string", "content"):
            if k in args and args[k] is not None:
                v = str(args[k]).replace("\n", " ")
                parts.append(f"{k}={v[:60]}{'...' if len(v) > 60 else ''}")
                if k in ("path", "command"):
                    break
        return " ".join(parts)

    # ── slash commands ───────────────────────────────────────
    def do_diff(self) -> None:
        diffs = self.diff_tracker.get_diffs()
        if not diffs:
            self.console.print("[dim]No file changes recorded in this session.[/dim]")
            return
        files = self.diff_tracker.get_changed_files()
        self.console.print(f"[bold]Changed files ({len(files)}):[/bold] " + ", ".join(f"[cyan]{p}[/cyan]" for p in files))
        for d in diffs:
            if not d.strip():
                continue
            try:
                self.console.print(Syntax(d, "diff", theme="ansi_dark", line_numbers=False))
            except Exception:
                self.console.print(d)

    def do_undo(self, mode: str = "") -> None:
        if not self.diff_tracker.changes:
            self.console.print("[dim]Nothing to undo.[/dim]")
            return
        mode_norm = (mode or "").strip().lower()
        if mode_norm in ("turn", "t", "txn", "transaction"):
            txn = self.diff_tracker.undo_last_transaction()
            if txn is None:
                self.console.print("[dim]Nothing to undo.[/dim]")
                return
            files = sorted({ch.path for ch in txn.changes})
            self.console.print(
                f"[green]Reverted transaction[/green] "
                f"({len(files)} file{'s' if len(files) != 1 else ''}): "
                + ", ".join(f"[cyan]{f}[/cyan]" for f in files)
            )
            return
        path = self.diff_tracker.undo_last()
        if path:
            self.console.print(f"[green]Reverted:[/green] [cyan]{path}[/cyan]")
        else:
            self.console.print("[dim]Nothing to undo.[/dim]")


    def _maybe_verify(self, sandbox):
        """Run the completion verification gate if enabled and diff is non-empty."""
        if not getattr(self.settings, "verify_before_finish", True):
            return None
        try:
            changed = self.diff_tracker.get_changed_files()
        except Exception:
            return None
        if not changed:
            return None
        try:
            return run_verify(changed, sandbox)
        except Exception as e:
            # Verifier crash must never block the user's task.
            self.console.print(f"[dim]verify skipped: {e}[/dim]")
            return None

    def run_turn(self, user_message: str) -> bool:
        """Public entry point.  Wraps the turn in a diff transaction so
        every file change made during the run can be reverted as one unit
        via ``/undo turn`` or ``diff_tracker.undo_last_transaction()``."""
        self.diff_tracker.begin_turn()
        try:
            return self._run_turn_inner(user_message)
        finally:
            self.diff_tracker.commit_turn()

    def _run_turn_inner(self, user_message: str) -> bool:
        _turn_t0 = time.perf_counter()
        self.ctx.add_user(user_message)
        if self.session_store:
            self.session_store.append_event(self.session_id, "user_msg", {"content": user_message})

        interrupt = False
        final_answer = ""
        self._streamed_answer = False
        loop_guard = LoopGuard()

        with self._make_sandbox() as sb:
            core = AgentLoopCore(
                llm=self.llm,
                sandbox=sb,
                registry=self.registry,
                verbose=self.verbose,
            )
            turn_steps = 0
            max_turn_steps = self.settings.max_steps

            while turn_steps < max_turn_steps:
                turn_steps += 1
                self.steps += 1

                compaction_level = self.ctx.needs_compaction()
                if compaction_level:
                    self.console.print("[dim]Compacting context...[/dim]")
                    try:
                        self.ctx.compact(compaction_level, self.llm)
                    except Exception:
                        pass

                messages = self.ctx.build_messages()
                response_text = ""
                tool_calls_raw = None
                usage = {}

                use_stream = hasattr(self.llm, "chat_stream") and callable(getattr(self.llm, "chat_stream", None))
                streamed_ok = False
                if use_stream:
                    try:
                        response_text, tool_calls_raw, usage = self._stream_llm(messages)
                        streamed_ok = True
                    except KeyboardInterrupt:
                        self.console.print("[yellow]Interrupted.[/yellow]")
                        interrupt = True
                        break
                    except Exception:
                        streamed_ok = False
                if not streamed_ok:
                    try:
                        with self.console.status("[dim]Thinking...[/dim]", spinner="dots"):
                            step_result = core.execute_step(
                                self.ctx, self.tool_schemas, temperature=0.2
                            )
                        response_text = step_result.text
                        tool_calls_raw = (
                            step_result.raw_response.tool_calls
                            if step_result.raw_response else None
                        )
                        usage = step_result.usage
                    except KeyboardInterrupt:
                        self.console.print("[yellow]Interrupted.[/yellow]")
                        interrupt = True
                        break
                    except Exception as e:
                        self.console.print(f"[red]LLM error: {e}[/red]")
                        if self.session_store:
                            self.session_store.append_event(self.session_id, "error", {"error": str(e)})
                        break

                self.total_tokens += (usage.get("total_tokens", 0) if usage else 0)
                if usage:
                    model_name = getattr(self.llm, "models", {}).get(Tier.DEFAULT, "")
                    self.cost_tracker.on_llm_call(usage, model_name)

                parsed = parse_response(content=response_text, tool_calls=tool_calls_raw)

                if self.session_store:
                    self.session_store.append_event(self.session_id, "assistant_msg", {
                        "content": response_text, "tool_calls": tool_calls_raw, "usage": usage,
                    })

                if parsed.is_finish:
                    final_answer = parsed.content or response_text
                    v = self._maybe_verify(sb)
                    if v and v.blocked:
                        # Refuse to finish; feed the failure back to the model.
                        note = v.summary()
                        self.console.print(f"[yellow]! verify blocked finish[/yellow]\n[yellow]{note}[/yellow]")
                        self.ctx.add_tool_result(
                            "verify", f"[verify_block] {note}", is_error=True,
                        )
                        turn_steps += 0  # continue the outer loop, do NOT break
                        continue
                    if v and v.checked_files:
                        final_answer = f"{final_answer}\n\n{v.summary()}"
                    self.ctx.add_assistant(final_answer)
                    break

                if parsed.is_tool_call and parsed.tool_calls:
                    self.ctx.add_assistant(
                        response_text,
                        tool_calls=[
                            {"id": tc["id"], "type": "function",
                             "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])}}
                            for tc in parsed.tool_calls
                        ],
                    )

                    for tc in parsed.tool_calls:
                        tool_name = tc["name"]
                        tool_args = tc["arguments"]
                        call_id = tc["id"]

                        if tool_name == "finish":
                            final_answer = tool_args.get("summary", response_text)
                            v = self._maybe_verify(sb)
                            if v and v.blocked:
                                note = v.summary()
                                self.console.print(f"[yellow]! verify blocked finish[/yellow]\n[yellow]{note}[/yellow]")
                                # Feed the error back to the model via tool_result
                                # so it keeps working and does not exit yet.
                                self.ctx.add_tool_result(call_id, f"[verify_block] {note}", is_error=True)
                                if self.session_store:
                                    self.session_store.append_event(self.session_id, "tool_result", {
                                        "tool": "finish", "call_id": call_id,
                                        "content": note, "error": note,
                                        "metadata": {"reason": "verify_block"},
                                        "duration_ms": 0,
                                    })
                                continue
                            if v and v.checked_files:
                                final_answer = f"{final_answer}\n\n{v.summary()}"
                            if self.session_store:
                                self.session_store.append_event(self.session_id, "finish", {"summary": final_answer})
                            self.ctx.add_assistant(final_answer)
                            return True

                        # Malformed tool_call arguments (JSON truncated / unparseable).
                        # Do NOT invoke the tool with a garbage payload; feed a
                        # synthetic tool_result so the model can retry with a
                        # smaller or corrected argument set.
                        if isinstance(tool_args, dict) and "_raw" in tool_args and len(tool_args) == 1:
                            self.console.print(
                                f"[yellow]! {tool_name}: tool_call arguments were incomplete "
                                f"(model output truncated). Asking model to retry with smaller output.[/yellow]"
                            )
                            err_msg = (
                                "Error: tool_call arguments were incomplete or malformed JSON "
                                "(likely output truncated by max_tokens). "
                                "Retry with a smaller payload — e.g. write shorter files, or split "
                                "the write into multiple write_file / edit_file calls."
                            )
                            self.ctx.add_tool_result(call_id, err_msg, is_error=True)
                            if self.session_store:
                                self.session_store.append_event(self.session_id, "tool_result", {
                                    "tool": tool_name, "call_id": call_id,
                                    "content": err_msg, "error": err_msg,
                                    "metadata": {"reason": "malformed_arguments"},
                                    "duration_ms": 0,
                                })
                            continue

                        sys.stdout.write("\n")
                        sys.stdout.flush()
                        self.console.print(format_tool_line(tool_name, tool_args))

                        _abort = loop_guard.record_call(tool_name, tool_args)
                        if _abort:
                            self.console.print(f"[yellow]! loop guard: {_abort}[/yellow]")
                            self.ctx.add_tool_result(call_id, f"[loop_guard_abort] {_abort}", is_error=True)
                            if self.session_store:
                                self.session_store.append_event(self.session_id, "tool_result", {
                                    "tool": tool_name, "call_id": call_id,
                                    "content": _abort, "error": _abort,
                                    "metadata": {"reason": "loop_guard"},
                                    "duration_ms": 0,
                                })
                            final_answer = (
                                "I have stopped early to avoid an infinite loop. "
                                + _abort
                            )
                            self.ctx.add_assistant(final_answer)
                            interrupt = True
                            break

                        if self.session_store:
                            self.session_store.append_event(self.session_id, "tool_call", {
                                "tool": tool_name, "args": tool_args, "call_id": call_id,
                            })

                        before_content, existed_before = self._snapshot_before(tool_name, tool_args)

                        tool_result = None
                        t0 = time.perf_counter()
                        try:
                            tool_result = core.execute_tool(
                                tool_name,
                                tool_args,
                                call_id,
                                approval_handler=lambda approval: self._interactive_approve(
                                    approval.tool_name, approval.args, approval.reason
                                ),
                            )
                        except KeyboardInterrupt:
                            interrupt = True
                            tool_result = ToolResult(error="Interrupted by user")
                        except Exception as e:
                            tool_result = ToolResult(error=f"{type(e).__name__}: {e}")
                        elapsed = time.perf_counter() - t0

                        if tool_result and not tool_result.error:
                            self._record_tool_change(
                                tool_name, tool_args, tool_result, before_content, existed_before
                            )

                        result_str = tool_result.content if not tool_result.error else f"Error: {tool_result.error}"
                        result_str = tool_compact(result_str)

                        if self.session_store:
                            self.session_store.append_event(self.session_id, "tool_result", {
                                "tool": tool_name, "call_id": call_id,
                                "content": tool_result.content,
                                "error": tool_result.error,
                                "metadata": tool_result.metadata,
                                "duration_ms": int(elapsed * 1000),
                            })

                        is_error = bool(tool_result.error)
                        # Ctrl-C during a tool must exit the turn cleanly, not
                        # trigger loop_guard or feed error back for another round.
                        _ec = getattr(tool_result, "error_code", None)
                        if _ec == "E_INTERRUPTED":
                            self.ctx.add_tool_result(call_id, result_str, is_error=True)
                            self.console.print(f"[yellow]Task interrupted by user.[/yellow]")
                            interrupt = True
                            break
                        _abort = loop_guard.record_result(_ec if is_error else None)
                        self.ctx.add_tool_result(call_id, result_str, is_error=is_error)

                        status_icon = "[red]x[/red]" if is_error else "[green]OK[/green]"
                        _suffix = format_result_suffix(tool_name, tool_args, getattr(tool_result, "metadata", None))
                        self.console.print(f"  [dim]└[/dim] {status_icon} [dim]({elapsed:.1f}s)[/dim]{_suffix}")
                        if is_error:
                            self.console.print(f"[red]  {result_str[:200]}[/red]")
                        elif self.verbose:
                            self.console.print(f"[dim]  {result_str[:200]}[/dim]")

                        if _abort:
                            self.console.print(f"[yellow]! loop guard: {_abort}[/yellow]")
                            final_answer = (
                                "I have stopped early to avoid burning tokens on repeated failures. "
                                + _abort
                            )
                            self.ctx.add_assistant(final_answer)
                            interrupt = True
                            break

                    if interrupt:
                        break
                    continue

                if response_text.strip():
                    final_answer = response_text.strip()
                    self.ctx.add_assistant(final_answer)
                break

        if final_answer:
            sys.stdout.write("\n")
            sys.stdout.flush()
            if not self._streamed_answer:
                try:
                    self.console.print(Markdown(final_answer))
                except Exception:
                    self.console.print(final_answer)
        self._streamed_answer = False
        _turn_elapsed = time.perf_counter() - _turn_t0
        self.console.print(f"[dim]Turn: {_turn_elapsed:.1f}s[/dim]")
        if interrupt:
            self.console.print("[yellow]Task interrupted.[/yellow]")
        return not interrupt

    def _stream_llm(self, messages: list):
        accumulated = ""
        tool_calls = []
        usage = {}
        got_tool_delta = False
        first_token = True
        self._streamed_answer = False
        thinking_status = None
        try:
            thinking_status = self.console.status("[dim]Thinking...[/dim]", spinner="dots")
            thinking_status.start()
            gen = self.llm.chat_stream(
                messages=messages,
                tools=self.tool_schemas,
                tier=Tier.DEFAULT,
                temperature=0.2,
            )
            for event in gen:
                etype = event.get("type") if isinstance(event, dict) else None
                if etype == "text_delta":
                    chunk = event.get("content", "")
                    accumulated += chunk
                    if not got_tool_delta and chunk:
                        if first_token:
                            if thinking_status is not None:
                                try:
                                    thinking_status.stop()
                                except Exception:
                                    pass
                                thinking_status = None
                            first_token = False
                        sys.stdout.write(chunk)
                        sys.stdout.flush()
                        self._streamed_answer = True
                elif etype == "tool_call_partial":
                    # Swap "Thinking..." to a per-tool progress spinner so the user
                    # sees that a long-running tool_call is being generated by the
                    # model (e.g. write_file with a huge content argument).
                    got_tool_delta = True
                    if thinking_status is not None:
                        try:
                            thinking_status.stop()
                        except Exception:
                            pass
                    tname = event.get("name", "tool")
                    thinking_status = self.console.status(
                        f"[cyan]> {tname}[/cyan] [dim]working...[/dim]",
                        spinner="dots",
                    )
                    thinking_status.start()
                elif etype == "tool_call":
                    if thinking_status is not None:
                        try:
                            thinking_status.stop()
                        except Exception:
                            pass
                        thinking_status = None
                    got_tool_delta = True
                    tool_calls.append({
                        "id": event.get("id", ""),
                        "name": event.get("name", ""),
                        "arguments": event.get("arguments", {}),
                    })
                elif etype == "done":
                    resp = event.get("response")
                    if resp is not None:
                        usage = getattr(resp, "usage", {}) or {}
                        if not accumulated:
                            accumulated = getattr(resp, "content", "") or ""
                    break
        finally:
            if thinking_status is not None:
                try:
                    thinking_status.stop()
                except Exception:
                    pass
        if self._streamed_answer:
            sys.stdout.write("\n")
            sys.stdout.flush()
        if tool_calls:
            self._streamed_answer = False
        return accumulated, (tool_calls if tool_calls else None), usage

    def do_compact(self) -> None:
        try:
            result = self.ctx.compact("auto", self.llm)
            n = result.get("steps_compacted", "?") if isinstance(result, dict) else "?"
            self.console.print(f"[green]Compacted ({n} steps summarized).[/green]")
        except Exception as e:
            self.console.print(f"[red]Compaction failed: {e}[/red]")

    def do_clear(self) -> None:
        self._build_context()
        self.steps = 0
        self.diff_tracker = DiffTracker(str(self.repo_path))
        self.total_tokens = 0
        self.cost_tracker = CostTracker()


def run_repl(
    repo_path: Path,
    approval_mode: str = "auto",
    sandbox_type: str = "local",
    model_override: str = "",
    verbose: bool = False,
) -> None:
    console = Console()

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    settings = get_settings()
    if not settings.is_configured():
        from repopilot.cli import _ensure_configured
        _ensure_configured()
        settings = get_settings()

    repo_path = repo_path.resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        console.print(f"[red]Directory not found: {repo_path}[/red]")
        return

    try:
        llm = build_llm_from_settings(settings)
    except Exception as e:
        console.print(f"[red]Failed to initialize LLM: {e}[/red]")
        return
    if model_override:
        settings.model = model_override
        settings.fast_model = model_override
        settings.strong_model = model_override
        reset_settings_for_tests()
        settings = get_settings()
        llm = build_llm_from_settings(settings)

    session_store = SessionStore(sessions_dir=settings.sessions_dir)
    session = session_store.create(title=f"REPL: {repo_path.name}", cwd=str(repo_path), model=settings.model)

    from repopilot import __version__ as _rp_version
    console.print()
    console.rule(f"[bold green]RepoPilot[/bold green] [dim]v{_rp_version}[/dim]")
    console.print(f"  [dim]cwd     [/dim] [cyan]{repo_path}[/cyan]")
    console.print(f"  [dim]model   [/dim] [cyan]{settings.model}[/cyan]")
    console.print(f"  [dim]sandbox [/dim] [cyan]{sandbox_type}[/cyan]   [dim]approval[/dim] [cyan]{approval_mode}[/cyan]")
    console.print()
    console.print(
        "  [dim]/help  /diff  /undo  /cd  /cost  /clear  /exit"
        "     (Ctrl+C to interrupt)[/dim]"
    )
    console.print()

    # Eagerly warm up the LLM backend (litellm + openai + tiktoken +
    # tokenizers) so the *first* user message does not stall for ~9s.
    # The proxy in llm.service defers this by default; we force-load it
    # here with a visible spinner so the user knows we are working.
    try:
        from repopilot.llm.service import litellm as _proxy
        if getattr(_proxy, "_mod", None) is None:  # not yet imported
            with console.status(
                "[dim]Loading LLM backend (litellm)...[/dim]",
                spinner="dots",
            ):
                _proxy._load()
    except Exception as _e:  # pragma: no cover - never block startup on this
        console.print(f"[yellow]LLM warmup skipped: {_e}[/yellow]")

    repl = ReplSession(
        repo_path=repo_path, llm=llm, sandbox_type=sandbox_type,
        approval_mode=approval_mode, settings=settings,
        session_store=session_store, session_id=session.id,
        console=console, verbose=verbose,
    )

    input_source = ReplInput(is_tty=sys.stdin.isatty())
    input_source._console = console
    repl.input_source = input_source

    while True:
        try:
            user_input = input_source.ask_user()
            if user_input is None:
                console.print("\n[dim]Goodbye.[/dim]")
                break
        except EOFError:
            console.print("\n[dim]Goodbye.[/dim]")
            break
        except KeyboardInterrupt:
            console.print("\n[dim]Ctrl+C - type /exit to quit.[/dim]")
            continue

        user_input = user_input.strip()
        if not user_input:
            continue

        if user_input.startswith("/"):
            parts = user_input.split(None, 1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""

            if cmd in ("/exit", "/quit", "/q"):
                console.print("[dim]Goodbye.[/dim]")
                break
            elif cmd == "/help":
                console.print(Markdown(HELP_TEXT))
            elif cmd == "/model":
                if arg:
                    if "/" not in arg:
                        console.print("[red]Model must be provider/model format (e.g. openai/gpt-4o)[/red]")
                    else:
                        settings.model = arg
                        settings.fast_model = arg
                        settings.strong_model = arg
                        reset_settings_for_tests()
                        settings = get_settings()
                        repl.llm = build_llm_from_settings(settings)
                        console.print(f"[green]Model -> {arg}[/green]")
                else:
                    console.print(f"Model: [cyan]{settings.model}[/cyan]")
            elif cmd == "/approval":
                valid = ("auto", "confirm", "edit-only", "deny")
                if arg in valid:
                    repl.approval_mode = arg
                    repl.registry._permission.mode = arg
                    console.print(f"[green]Approval -> {arg}[/green]")
                else:
                    console.print(f"Approval: [cyan]{repl.approval_mode}[/cyan]  (valid: {', '.join(valid)})")
            elif cmd == "/compact":
                repl.do_compact()
            elif cmd == "/clear":
                repl.do_clear()
                console.print("[green]Fresh conversation started.[/green]")
            elif cmd == "/cd":
                if arg:
                    p = Path(arg).expanduser().resolve()
                    if p.exists() and p.is_dir():
                        old_path = repl.repo_path
                        repl.repo_path = p
                        repl.diff_tracker = DiffTracker(str(p))
                        console.print(f"[green]cd -> {p}[/green]")
                        if session_store:
                            # Update session cwd, keep conversation history
                            session = session_store.get(repl.session_id)
                            if session:
                                session_store.append_event(repl.session_id, "system", {"cd": str(p), "from": str(old_path)})
                    else:
                        console.print(f"[red]Not a directory: {p}[/red]")
                else:
                    console.print(f"cwd: [cyan]{repl.repo_path}[/cyan]")
            elif cmd == "/diff":
                repl.do_diff()
            elif cmd == "/undo":
                repl.do_undo(arg)
            elif cmd == "/cost":
                ct = repl.cost_tracker
                console.print(ct.format_summary())
            elif cmd == "/status":
                console.print(f"  Directory: [cyan]{repl.repo_path}[/cyan]")
                console.print(f"  Model:     [cyan]{settings.model}[/cyan]")
                console.print(f"  Sandbox:   [cyan]{repl.sandbox_type}[/cyan]")
                console.print(f"  Approval:  [cyan]{repl.approval_mode}[/cyan]")
                console.print(f"  Steps:     [cyan]{repl.steps}[/cyan]")
                console.print(f"  Tokens:    [cyan]{repl.total_tokens}[/cyan]")
            elif cmd == "/memory":
                global_mem = settings.home_dir / "REPOPILOT.md"
                project_mem = repl.repo_path / "REPOPILOT.md"
                if arg:
                    if arg.startswith("--global "):
                        note = arg[len("--global "):].strip()
                        append_to_global_memory(settings.home_dir, note)
                        console.print("[green]Appended to global memory.[/green]")
                    else:
                        append_to_project_memory(repl.repo_path, arg.strip())
                        console.print("[green]Appended to project memory.[/green]")
                    repl._build_context()
                    session = session_store.create(title=f"REPL: {repo_path.name}", cwd=str(repo_path), model=settings.model)
                    repl.session_id = session.id
                else:
                    console.print("[bold]Memory files:[/bold]")
                    if global_mem.exists():
                        console.print("  Global (~/.repopilot/REPOPILOT.md): [green]exists[/green]")
                        gm = global_mem.read_text(encoding="utf-8")
                        console.print(Markdown(gm[:1000] + ("..." if len(gm) > 1000 else "")))
                    else:
                        console.print("  Global: [dim]not created yet[/dim]")
                    if project_mem.exists():
                        console.print("  Project (./REPOPILOT.md): [green]exists[/green]")
                        pm = project_mem.read_text(encoding="utf-8")
                        console.print(Markdown(pm[:1000] + ("..." if len(pm) > 1000 else "")))
                    else:
                        console.print("  Project: [dim]not created yet (use /memory <text> to add)[/dim]")
            elif cmd == "/sessions":
                recent = session_store.list(limit=10)
                if not recent:
                    console.print("[dim]No previous sessions.[/dim]")
                else:
                    console.print("[bold]Recent sessions:[/bold]")
                    for s in recent[:10]:
                        cwd = getattr(s, "cwd", "?") or "?"
                        title = getattr(s, "title", "untitled") or "untitled"
                        sid = (getattr(s, "id", "?") or "?")[:8]
                        console.print(f"  [{sid}] {title} - {cwd}")
                    console.print("[dim]Use /resume <id> to resume[/dim]")
            elif cmd == "/resume":
                if not arg:
                    recent = session_store.list(limit=5)
                    if recent:
                        arg = recent[0].id
                        console.print(f"[dim]Resuming most recent session: {arg[:8]}[/dim]")
                    else:
                        console.print("[red]No previous session to resume.[/red]")
                        continue
                try:
                    events = session_store.read_events(arg)
                    if not events:
                        console.print(f"[red]Session {arg[:8]} not found or empty.[/red]")
                        continue
                    repl.do_clear()
                    for ev in events:
                        ev_type = ev.get("type", "")
                        data = ev.get("payload", {})
                        if ev_type == "user_msg":
                            content = data.get("content", "")
                            if content:
                                repl.ctx.add_user(content)
                        elif ev_type == "assistant_msg":
                            content = data.get("content", "")
                            tool_calls = data.get("tool_calls")
                            if content or tool_calls:
                                msg_tool_calls = None
                                if tool_calls:
                                    msg_tool_calls = []
                                    for tc in tool_calls:
                                        if isinstance(tc, dict) and "function" in tc:
                                            msg_tool_calls.append({
                                                "id": tc.get("id", ""),
                                                "type": "function",
                                                "function": {"name": tc["function"].get("name", ""), "arguments": tc["function"].get("arguments", "{}")}
                                            })
                                repl.ctx.add_assistant(content, tool_calls=msg_tool_calls)
                        elif ev_type == "tool_result":
                            call_id = data.get("call_id", "")
                            content = data.get("content", "")
                            error = data.get("error", "")
                            if call_id:
                                result_str = content if not error else f"Error: {error}"
                                repl.ctx.add_tool_result(call_id, result_str, is_error=bool(error))
                    repl.session_id = arg
                    console.print(f"[green]Resumed session {arg[:8]}. Context restored.[/green]")
                except Exception as e:
                    console.print(f"[red]Failed to resume session: {e}[/red]")
            else:
                console.print(f"[red]Unknown command: {cmd}[/red]. Type /help.")
            continue

        try:
            repl.run_turn(user_input)
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow]")
        except Exception as e:
            console.print(f"[red]Error: {type(e).__name__}: {e}[/red]")
