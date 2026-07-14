"""Interactive REPL - persistent multi-turn conversation (Claude Code / Codex CLI style)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.prompt import Prompt, Confirm

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
from repopilot.tools.base import ApprovalRequired
from repopilot.memory import load_memory, create_global_memory, append_to_project_memory, append_to_global_memory

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
"""


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
        self.total_tokens = 0
        self.cost_tracker = CostTracker()
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

        system_prompt = _load_system_prompt()
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

    def run_turn(self, user_message: str) -> bool:
        self.ctx.add_user(user_message)
        if self.session_store:
            self.session_store.append_event(self.session_id, "user_msg", {"content": user_message})

        interrupt = False
        final_answer = ""

        with self._make_sandbox() as sb:
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

                try:
                    with self.console.status("[dim]Thinking...[/dim]", spinner="dots"):
                        resp = self.llm.chat(
                            messages=messages,
                            tools=self.tool_schemas,
                            tier=Tier.DEFAULT,
                            temperature=0.2,
                        )
                    response_text = resp.content or ""
                    tool_calls_raw = resp.tool_calls
                    usage = resp.usage or {}
                    self.total_tokens += usage.get("total_tokens", 0)
                    self.cost_tracker.on_llm_call(usage, resp.model)
                except KeyboardInterrupt:
                    self.console.print("[yellow]Interrupted.[/yellow]")
                    interrupt = True
                    break
                except Exception as e:
                    self.console.print(f"[red]LLM error: {e}[/red]")
                    if self.session_store:
                        self.session_store.append_event(self.session_id, "error", {"error": str(e)})
                    break

                parsed = parse_response(content=response_text, tool_calls=tool_calls_raw)

                if self.session_store:
                    self.session_store.append_event(self.session_id, "assistant_msg", {
                        "content": response_text, "tool_calls": tool_calls_raw, "usage": usage,
                    })

                if parsed.is_finish:
                    final_answer = parsed.content or response_text
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
                            if self.session_store:
                                self.session_store.append_event(self.session_id, "finish", {"summary": final_answer})
                            self.ctx.add_assistant(final_answer)
                            return True

                        arg_str = ", ".join(f"{k}={str(v)[:40]}" for k, v in tool_args.items())
                        self.console.print(f"[dim]> {tool_name}({arg_str})[/dim]")

                        if self.session_store:
                            self.session_store.append_event(self.session_id, "tool_call", {
                                "tool": tool_name, "args": tool_args, "call_id": call_id,
                            })

                        try:
                            tool_result = self.registry.execute(tool_name, tool_args, sb)
                        except KeyboardInterrupt:
                            interrupt = True
                            break
                        except Exception as e:
                            from repopilot.tools.result import ToolResult
                            tool_result = ToolResult(error=f"{type(e).__name__}: {e}")

                        result_str = tool_result.content if not tool_result.error else f"Error: {tool_result.error}"
                        result_str = tool_compact(result_str)

                        if self.session_store:
                            self.session_store.append_event(self.session_id, "tool_result", {
                                "tool": tool_name, "call_id": call_id,
                                "content": tool_result.content if not tool_result.error else "",
                                "error": tool_result.error or "",
                            })

                        is_error = bool(tool_result.error)
                        self.ctx.add_tool_result(call_id, result_str, is_error=is_error)

                        if is_error:
                            self.console.print(f"[red]  {result_str[:200]}[/red]")
                        elif self.verbose:
                            self.console.print(f"[dim]  {result_str[:200]}[/dim]")

                    if interrupt:
                        break
                    continue

                if response_text.strip():
                    final_answer = response_text.strip()
                    self.ctx.add_assistant(final_answer)
                break

        self.console.print()
        if final_answer:
            try:
                self.console.print(Markdown(final_answer))
            except Exception:
                self.console.print(final_answer)
        self.console.print()
        if interrupt:
            self.console.print("[yellow]Task interrupted.[/yellow]")
        return not interrupt

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

    console.print()
    console.rule("[bold green]RepoPilot[/bold green]")
    console.print(f"  Directory: [cyan]{repo_path}[/cyan]")
    console.print(f"  Model:     [cyan]{settings.model}[/cyan]")
    console.print(f"  Sandbox:   [cyan]{sandbox_type}[/cyan]")
    console.print(f"  Approval:  [cyan]{approval_mode}[/cyan]")
    console.print()
    console.print("[dim]Type /help for commands, /exit to quit. Press Ctrl+C to interrupt.[/dim]")
    console.print()

    repl = ReplSession(
        repo_path=repo_path, llm=llm, sandbox_type=sandbox_type,
        approval_mode=approval_mode, settings=settings,
        session_store=session_store, session_id=session.id,
        console=console, verbose=verbose,
    )

    import queue
    _stdin_lines: list[str] = []
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            _stdin_lines = [l.strip() for l in raw.split("\n") if l.strip()]
        except Exception:
            _stdin_lines = []
    _stdin_idx = 0

    while True:
        try:
            if sys.stdin.isatty():
                user_input = Prompt.ask("[bold green]repopilot[/bold green]", default="", show_default=False)
            else:
                if _stdin_idx >= len(_stdin_lines):
                    console.print("\n[dim]Goodbye.[/dim]")
                    break
                user_input = _stdin_lines[_stdin_idx]
                _stdin_idx += 1
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
                        try:
                            new_llm = build_llm_from_settings(settings)
                            repl.llm = new_llm
                            console.print(f"[green]Model -> {arg}[/green]")
                        except Exception as e:
                            console.print(f"[red]Failed to switch model: {e}[/red]")
                else:
                    console.print(f"Model: [cyan]{settings.model}[/cyan]")
            elif cmd == "/approval":
                valid = ("auto", "confirm", "edit-only", "deny")
                if arg in valid:
                    repl.approval_mode = arg
                    repl.registry.set_permission_engine(repl._make_permission_engine())
                    console.print(f"[green]Approval -> {arg}[/green]")
                else:
                    console.print(f"Approval: [cyan]{repl.approval_mode}[/cyan]  (valid: {', '.join(valid)})")
            elif cmd == "/compact":
                repl.do_compact()
            elif cmd == "/clear":
                repl.do_clear()
                session = session_store.create(title=f"REPL: {repo_path.name}", cwd=str(repo_path), model=settings.model)
                repl.session_id = session.id
                console.print("[green]Conversation cleared.[/green]")
            elif cmd == "/cd":
                if not arg:
                    console.print(f"Directory: [cyan]{repo_path}[/cyan]")
                else:
                    new_path = (repo_path / arg).resolve() if not Path(arg).is_absolute() else Path(arg).resolve()
                    if not new_path.exists() or not new_path.is_dir():
                        console.print(f"[red]Not a directory: {new_path}[/red]")
                    else:
                        repo_path = new_path
                        repl.repo_path = new_path
                        repl.do_clear()
                        session = session_store.create(title=f"REPL: {repo_path.name}", cwd=str(repo_path), model=settings.model)
                        repl.session_id = session.id
                        console.print(f"[green]Directory -> {new_path}[/green]")
            elif cmd == "/cost":
                ct = repl.cost_tracker
                lines = [f"Steps: {repl.steps}  Tokens: {repl.total_tokens:,}  Cost: ${ct.total_cost_usd:.4f}"]
                if ct._per_model:
                    for mname, info in ct._per_model.items():
                        lines.append(f"  {mname}: {info['prompt']+info['completion']:,} tokens, ${info['cost']:.4f}")
                console.print("\n".join(lines))
            elif cmd == "/status":
                console.print(f"  Directory: [cyan]{repo_path}[/cyan]")
                console.print(f"  Model:     [cyan]{settings.model}[/cyan]")
                console.print(f"  Sandbox:   [cyan]{sandbox_type}[/cyan]")
                console.print(f"  Approval:  [cyan]{repl.approval_mode}[/cyan]")
                console.print(f"  Steps:     [cyan]{repl.steps}[/cyan]")
                console.print(f"  Tokens:    [cyan]{repl.total_tokens:,}[/cyan]")
            elif cmd == "/memory":
                global_mem = settings.home_dir / "REPOPILOT.md"
                project_mem = repo_path / "REPOPILOT.md"
                if arg:
                    note = arg.strip()
                    if note.startswith("--global"):
                        note = note[len("--global"):].strip()
                        append_to_global_memory(settings.home_dir, note)
                        console.print(f"[green]Added to global memory.[/green]")
                    else:
                        append_to_project_memory(repo_path, note)
                        console.print(f"[green]Added to project memory.[/green]")
                    repl.do_clear()
                    session = session_store.create(title=f"REPL: {repo_path.name}", cwd=str(repo_path), model=settings.model)
                    repl.session_id = session.id
                else:
                    console.print("[bold]Memory files:[/bold]")
                    if global_mem.exists():
                        console.print(f"  Global (~/.repopilot/REPOPILOT.md): [green]exists[/green]")
                        gm = global_mem.read_text(encoding="utf-8")
                        console.print(Markdown(gm[:1000] + ("..." if len(gm) > 1000 else "")))
                    else:
                        console.print("  Global: [dim]not created yet[/dim]")
                    if project_mem.exists():
                        console.print(f"  Project (./REPOPILOT.md): [green]exists[/green]")
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

