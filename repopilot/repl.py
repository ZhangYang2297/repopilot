"""Interactive REPL mode — the primary user experience (like Claude Code / Codex CLI).

Usage:
  repopilot              # enter REPL, using cwd as repo root
  repopilot -r /path     # enter REPL for specific directory
  repopilot "task"       # one-shot task execution (no REPL)
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.text import Text

from repopilot.config import get_settings, reset_settings_for_tests, Settings
from repopilot.llm.service import build_llm_from_settings
from repopilot.sandbox import LocalSandbox, DockerSandbox
from repopilot.permission.engine import PermissionEngine
from repopilot.hooks.manager import HookManager
from repopilot.hooks.builtin import install_builtin_hooks
from repopilot.agent.cost import CostTracker
from repopilot.session.store import SessionStore
from repopilot.agent.loop import run_agent, StreamEvent, RunResult


def _print_help(console: Console) -> None:
    """Print slash command help."""
    help_text = """
**Available slash commands:**

| Command | Description |
|---------|-------------|
| `/exit` or `/quit` | Exit RepoPilot (Ctrl+D also works) |
| `/help` | Show this help message |
| `/model [name]` | Show or switch model |
| `/approval [mode]` | Show or set approval mode (auto/confirm/edit-only/deny) |
| `/compact` | Manually trigger context compaction |
| `/clear` | Clear conversation history (starts fresh) |
| `/cd <path>` | Switch working directory |
| `/cost` | Show token usage and cost for this session |
| `/status` | Show current configuration |

Or just type your task in natural language and press Enter.
Press Ctrl+C to interrupt the current task.
"""
    console.print(Markdown(help_text))


def run_repl(
    repo_path: Path,
    approval_mode: str = "auto",
    sandbox_type: str = "local",
    model_override: str = "",
    verbose: bool = False,
) -> None:
    """Run the interactive REPL session."""
    console = Console()
    settings = get_settings()

    # Ensure configured
    if not settings.is_configured():
        console.print("[yellow]Not configured yet. Running first-run wizard...[/yellow]\n")
        _run_wizard(console)
        settings = get_settings()

    repo_path = repo_path.resolve()
    if not repo_path.exists():
        console.print(f"[red]Directory does not exist: {repo_path}[/red]")
        return

    # Build LLM
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
        llm = build_llm_from_settings(get_settings())

    # Session store
    session_store = SessionStore(sessions_dir=settings.sessions_dir)
    session = session_store.create(title="REPL session", cwd=str(repo_path), model=settings.model)

    # Print welcome
    console.print()
    console.rule("[bold green]RepoPilot[/bold green]")
    console.print(f"  Working directory: [cyan]{repo_path}[/cyan]")
    console.print(f"  Model: [cyan]{settings.model}[/cyan]")
    console.print(f"  Sandbox: [cyan]{sandbox_type}[/cyan]")
    console.print(f"  Approval: [cyan]{approval_mode}[/cyan]")
    console.print(f"  Session: [dim]{session.id[:8]}[/dim]")
    console.print()
    console.print("Type your task or [dim]/help[/dim] for commands. Press Ctrl+C to interrupt, Ctrl+D to exit.")
    console.print()

    conversation_history: list[dict] = []
    current_approval = approval_mode

    def make_sandbox():
        if sandbox_type == "docker":
            return DockerSandbox(
                repo_path,
                mem_limit=settings.docker_mem_limit,
                network_mode="bridge" if settings.docker_network else "none",
            )
        return LocalSandbox(repo_path)

    def run_single_task(task: str) -> Optional[RunResult]:
        nonlocal current_approval
        pe = PermissionEngine(mode=current_approval, network_enabled=True)
        hooks = HookManager()
        cost_tracker = CostTracker()
        install_builtin_hooks(hooks, cost_tracker=cost_tracker)

        try:
            with make_sandbox() as sb:
                t0 = time.time()
                result = run_agent(
                    task=task,
                    repo_path=repo_path,
                    llm=llm,
                    sandbox=sb,
                    permission_engine=pe,
                    hooks=hooks,
                    session_store=session_store,
                    max_steps=settings.max_steps,
                    budget_tokens=settings.budget_tokens,
                    verbose=verbose,
                )
                elapsed = time.time() - t0
                return result
        except KeyboardInterrupt:
            console.print("\n[yellow]Task interrupted.[/yellow]")
            return None
        except Exception as e:
            console.print(f"\n[red]Error: {type(e).__name__}: {e}[/red]")
            return None

    # REPL loop
    while True:
        try:
            # Get user input
            try:
                user_input = Prompt.ask(
                    "[bold green]repopilot[/bold green]",
                    default="",
                    show_default=False,
                )
            except EOFError:
                # Ctrl+D
                console.print("\n[dim]Goodbye.[/dim]")
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            # Slash commands
            if user_input.startswith("/"):
                parts = user_input.split(None, 1)
                cmd = parts[0].lower()
                arg = parts[1] if len(parts) > 1 else ""

                if cmd in ("/exit", "/quit", "/q"):
                    console.print("[dim]Goodbye.[/dim]")
                    break
                elif cmd == "/help":
                    _print_help(console)
                elif cmd == "/model":
                    if arg:
                        settings.model = arg
                        settings.fast_model = arg
                        settings.strong_model = arg
                        settings.save()
                        reset_settings_for_tests()
                        llm = build_llm_from_settings(get_settings())
                        console.print(f"[green]Model set to {arg}[/green]")
                    else:
                        console.print(f"Current model: [cyan]{settings.model}[/cyan]")
                        console.print("Usage: /model provider/model-name")
                elif cmd == "/approval":
                    valid = ("auto", "confirm", "edit-only", "deny")
                    if arg in valid:
                        current_approval = arg
                        console.print(f"[green]Approval mode set to {arg}[/green]")
                    else:
                        console.print(f"Current approval mode: [cyan]{current_approval}[/cyan]")
                        console.print(f"Valid modes: {', '.join(valid)}")
                elif cmd == "/compact":
                    console.print("[dim]Compaction will happen automatically when context fills up.[/dim]")
                elif cmd == "/clear":
                    conversation_history.clear()
                    session = session_store.create(title="REPL session (fresh)", cwd=str(repo_path), model=settings.model)
                    console.print("[green]Conversation cleared.[/green]")
                elif cmd == "/cd":
                    if not arg:
                        console.print(f"Current directory: [cyan]{repo_path}[/cyan]")
                    else:
                        new_path = (repo_path / arg).resolve() if not Path(arg).is_absolute() else Path(arg).resolve()
                        if not new_path.exists():
                            console.print(f"[red]Directory not found: {new_path}[/red]")
                        elif not new_path.is_dir():
                            console.print(f"[red]Not a directory: {new_path}[/red]")
                        else:
                            repo_path = new_path
                            conversation_history.clear()
                            session = session_store.create(title="REPL session", cwd=str(repo_path), model=settings.model)
                            console.print(f"[green]Switched to {new_path}[/green]")
                elif cmd == "/cost":
                    if session:
                        s = session_store.get(session.id)
                        if s:
                            console.print(f"Session tokens: [cyan]{s.tokens_used}[/cyan]")
                    console.print(f"Session ID: [dim]{session.id[:8]}[/dim]")
                elif cmd == "/status":
                    console.print(f"  Directory: [cyan]{repo_path}[/cyan]")
                    console.print(f"  Model: [cyan]{settings.model}[/cyan]")
                    console.print(f"  Sandbox: [cyan]{sandbox_type}[/cyan]")
                    console.print(f"  Approval: [cyan]{current_approval}[/cyan]")
                    console.print(f"  Max steps: [cyan]{settings.max_steps}[/cyan]")
                    console.print(f"  Budget: [cyan]{settings.budget_tokens}[/cyan] tokens")
                else:
                    console.print(f"[red]Unknown command: {cmd}[/red]. Type /help for available commands.")
                continue

            # Regular task — run agent
            console.print()
            with console.status(f"[dim]Thinking...[/dim]", spinner="dots"):
                result = run_single_task(user_input)

            if result is None:
                continue

            # Print result
            console.print()
            console.rule("[bold green]Result[/bold green]")
            if result.summary:
                try:
                    console.print(Markdown(result.summary))
                except Exception:
                    console.print(result.summary)
            console.print()
            status_color = {"completed": "green", "max_steps": "yellow", "cancelled": "yellow", "error": "red"}.get(result.status, "white")
            console.print(f"[bold {status_color}]Status:[/bold {status_color}] {result.status}  "
                          f"[bold]Steps:[/bold] {result.steps}  "
                          f"[bold]Time:[/bold] {result.duration_ms/1000:.1f}s")
            console.print()
            conversation_history.append({"task": user_input, "result": result.summary})

        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted. Type /exit to quit.[/yellow]")
            continue


def _run_wizard(console: Console) -> None:
    """First-run configuration wizard."""
    import typer
    from repopilot.config import Settings as S

    console.print()
    console.rule("[bold yellow]Welcome to RepoPilot[/bold yellow]")
    console.print(
        "Let's configure your LLM provider. You need an OpenAI-compatible API key.\n"
    )
    model = typer.prompt(
        "Model (provider/model, e.g. openai/doubao-seed-evolving for Volcengine ARK)"
    ).strip()
    api_key = typer.prompt("API key (sk-...)", hide_input=True).strip()
    base_url = typer.prompt(
        "Base URL (press Enter for OpenAI default, or enter your endpoint)",
        default="", show_default=False,
    ).strip()

    new = S.load(model=model, api_key=api_key, base_url=base_url or "")
    new.save()
    reset_settings_for_tests()
    console.print(f"\n[green]Configuration saved to {new.config_file}[/green]\n")
