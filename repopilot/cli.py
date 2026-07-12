from __future__ import annotations
import typer
from rich.console import Console
from rich.table import Table
from pathlib import Path

from repopilot.config import get_settings, reset_settings_for_tests, Settings

# Recommended models (tested on Volcengine ARK). Users can set any LiteLLM-compatible model.
RECOMMENDED_MODELS = {
    "fast": [
        ("doubao-seed-1-6-flash-250828", "Fastest/cheapest - plan/reflect"),
    ],
    "default": [
        ("doubao-seed-evolving", "RECOMMENDED - 5M tokens, best for code"),
        ("doubao-seed-2-1-turbo-260628", "2M tokens, balanced"),
        ("doubao-seed-2-0-mini-260428", "200K tokens, fast"),
        ("doubao-seed-2-0-code-preview-260215", "Code-specialized"),
        ("glm-4-7-251222", "Zhipu GLM-4.7"),
    ],
    "strong": [
        ("doubao-seed-2-1-pro-260628", "Best quality"),
        ("deepseek-v3-2-251201", "DeepSeek V3.2"),
        ("glm-5-2-260617", "Zhipu GLM-5"),
    ],
}

app = typer.Typer(
    name="repopilot",
    help="RepoPilot - Local-first code agent.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()
config_app = typer.Typer(help="Manage configuration.", add_completion=False)
app.add_typer(config_app, name="config")


def _ensure_configured() -> None:
    """First-run wizard if no model is configured."""
    s = get_settings()
    if s.is_configured():
        return
    console.print()
    console.rule("[bold yellow]Welcome to RepoPilot[/bold yellow]")
    console.print(
        "It looks like this is your first run. Let's configure your LLM provider.\n"
        "You'll need an OpenAI-compatible API key (sk-...) and a base URL.\n"
    )
    model = typer.prompt(
        "Model (provider/model, e.g. openai/gpt-4o-mini, openai/qwen2.5-coder-32b-instruct)"
    ).strip()
    api_key = typer.prompt("API key (sk-...)", hide_input=True).strip()
    base_url = typer.prompt(
        "Base URL (press Enter for default OpenAI)",
        default="", show_default=False,
    ).strip()
    fast = typer.prompt(
        "Fast/cheap model for planning/reflection (Enter to use same as model)",
        default="", show_default=False,
    ).strip()
    strong = typer.prompt(
        "Strong model for final answers (Enter to use same as model)",
        default="", show_default=False,
    ).strip()

    new = Settings.load(
        model=model,
        api_key=api_key,
        base_url=base_url or "",
        fast_model=fast or model,
        strong_model=strong or model,
    )
    new.save()
    reset_settings_for_tests()  # so next get_settings() reloads
    console.print(f"\n[green]Configuration saved to {new.config_file}[/green]")
    console.print(f"  model       = {new.model}")
    console.print(f"  fast_model  = {new.fast_model}")
    console.print(f"  strong_model= {new.strong_model}")
    if new.base_url:
        console.print(f"  base_url    = {new.base_url}")
    console.print()


@app.callback(invoke_without_command=True)
def _main(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        _ensure_configured()


@app.command("models")
def list_models() -> None:
    """List recommended models for Volcengine ARK."""
    from rich.table import Table
    console.print("[bold]RepoPilot recommended models (Volcengine ARK)[/bold]\n")
    for tier, items in RECOMMENDED_MODELS.items():
        t = Table(title=f"{tier.upper()} tier", show_header=True, header_style="bold cyan")
        t.add_column("Model")
        t.add_column("Description")
        for name, desc in items:
            t.add_row(name, desc)
        console.print(t)
    console.print("\n[dim]Switch model with:  repopilot model <name>[/dim]")
    console.print("[dim]Or edit directly:   repopilot config set model openai/<model-name>[/dim]")
    console.print("[dim]Current model:[/dim]", get_settings().model)


@app.command("model")
def set_model(
    name: str = typer.Argument(..., help="Model name (without openai/ prefix)"),
    tier: str = typer.Option("default", "--tier", "-t", help="fast|default|strong"),
) -> None:
    """Set the model for a tier (fast/default/strong)."""
    if tier not in ("fast", "default", "strong"):
        console.print("[red]Tier must be fast, default, or strong[/red]")
        raise typer.Exit(1)
    s = get_settings()
    full_name = name if "/" in name else f"openai/{name}"
    # Validate format
    if "/" not in full_name:
        console.print("[red]Model must be in provider/model format, e.g. openai/doubao-seed-evolving[/red]")
        raise typer.Exit(1)
    key = f"{tier}_model" if tier != "default" else "model"
    s_c = Settings.load(**{key: full_name})
    s_c.save()
    reset_settings_for_tests()
    console.print(f"[green]Set {tier} model = {full_name}[/green]")


@app.command()
def version() -> None:
    """Show version."""
    from repopilot import __version__
    console.print(f"[bold green]RepoPilot[/bold green] v{__version__}")


@app.command()
def chat(
    task: str = typer.Argument(..., help="Task to perform"),
    repo: str = typer.Option(".", "--repo", "-r", help="Path to target repo"),
    model: str = typer.Option("", "--model", "-m", help="Override model"),
    sandbox: str = typer.Option("", "--sandbox", help="docker or local"),
    approval_mode: str = typer.Option(
        "confirm", "--approval-mode", help="auto|confirm|edit-only|deny"
    ),
    max_steps: int = typer.Option(50, "--max-steps", help="Max agent steps"),
    budget_tokens: int = typer.Option(200_000, "--budget-tokens", help="Input token budget"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run agent on a task in a repo."""
    _ensure_configured()
    import time
    from rich.panel import Panel
    from rich.live import Live
    from rich.markdown import Markdown
    from rich.syntax import Syntax
    from rich.text import Text

    from repopilot.config import get_settings as _gs
    from repopilot.llm.service import build_llm_from_settings
    from repopilot.sandbox import LocalSandbox, DockerSandbox
    from repopilot.permission.engine import PermissionEngine
    from repopilot.hooks.manager import HookManager
    from repopilot.hooks.builtin import install_builtin_hooks
    from repopilot.agent.cost import CostTracker
    from repopilot.session.store import SessionStore
    from repopilot.agent.loop import run_agent, StreamEvent

    s = _gs()
    chosen_model = model or s.model
    sandbox_type = sandbox or s.sandbox_type
    repo_path = Path(repo).resolve()

    if not repo_path.exists():
        console.print(f"[red]Repo path does not exist: {repo_path}[/red]")
        raise typer.Exit(1)

    # ── Build LLM ───────────────────────────────
    if model:
        # Override model in settings
        from repopilot.config import Settings as _S
        s = _S.load(model=model)
    try:
        llm = build_llm_from_settings(s)
    except Exception as e:
        console.print(f"[red]Failed to initialize LLM: {e}[/red]")
        raise typer.Exit(1)

    # ── Build Sandbox ────────────────────────────
    use_docker = sandbox_type == "docker"
    try:
        if use_docker:
            sb = DockerSandbox(
                repo_path,
                mem_limit=s.docker_mem_limit,
                network_mode="bridge" if s.docker_network else "none",
            )
        else:
            sb = LocalSandbox(repo_path)
    except Exception as e:
        console.print(f"[red]Failed to create sandbox: {e}[/red]")
        raise typer.Exit(1)

    # ── Permission Engine ───────────────────────
    if approval_mode not in PermissionEngine.VALID_MODES:
        console.print(f"[red]Invalid approval mode: {approval_mode}[/red]")
        raise typer.Exit(1)
    pe = PermissionEngine(mode=approval_mode, network_enabled=True)

    # ── Hooks ───────────────────────────────────
    hooks = HookManager()
    cost_tracker = CostTracker()
    install_builtin_hooks(hooks, cost_tracker=cost_tracker)

    # ── Session Store ───────────────────────────
    session_store = SessionStore(sessions_dir=s.sessions_dir)

    # ── Streaming UI state ──────────────────────
    import threading
    state = {"status": "starting", "current_tool": "", "last_result": "", "answer": ""}

    def on_event(evt: StreamEvent):
        if evt.type == "thinking":
            state["status"] = f"thinking (step {evt.data.get('step', '?')})"
        elif evt.type == "tool_call":
            name = evt.data.get("name", "?")
            args = evt.data.get("args", {})
            arg_str = ", ".join(f"{k}={str(v)[:40]}" for k, v in args.items())
            state["current_tool"] = f"{name}({arg_str})"
            state["status"] = "executing tool"
        elif evt.type == "tool_result":
            state["last_result"] = evt.data.get("result", "")[:300]
        elif evt.type == "text":
            if evt.data.get("role") == "assistant":
                state["answer"] += evt.data.get("content", "")
        elif evt.type == "finish":
            state["status"] = "finished"
        elif evt.type == "error":
            state["status"] = f"error: {evt.data.get('message', '')[:100]}"
        elif evt.type == "compact":
            state["status"] = f"compacting context ({evt.data.get('level', '')})"

    # ── Header panel ────────────────────────────
    console.print(Panel(
        f"[bold]Task:[/bold] {task}\n"
        f"[bold]Repo:[/bold] {repo_path}\n"
        f"[bold]Model:[/bold] {s.model}\n"
        f"[bold]Sandbox:[/bold] {sandbox_type}\n"
        f"[bold]Approval:[/bold] {approval_mode}",
        title="RepoPilot", border_style="cyan",
    ))

    # ── Run agent ───────────────────────────────
    t0 = time.time()
    try:
        with sb:
            result = run_agent(
                task=task,
                repo_path=repo_path,
                llm=llm,
                sandbox=sb,
                permission_engine=pe,
                hooks=hooks,
                session_store=session_store,
                max_steps=max_steps,
                budget_tokens=budget_tokens,
                stream_callback=on_event,
                verbose=verbose,
            )
    except Exception as e:
        console.print(f"[red]Agent failed: {e}[/red]")
        raise typer.Exit(1)

    elapsed = time.time() - t0

    # ── Output result ───────────────────────────
    console.print()
    console.rule("[bold green]Result[/bold green]")
    if result.summary:
        console.print(Markdown(result.summary))
    console.print()

    # Status
    status_color = {
        "completed": "green", "max_steps": "yellow",
        "cancelled": "yellow", "error": "red",
    }.get(result.status, "white")
    console.print(f"[bold {status_color}]Status:[/bold {status_color}] {result.status}")
    console.print(f"[bold]Steps:[/bold] {result.steps}")
    console.print(f"[bold]Duration:[/bold] {elapsed:.1f}s")
    console.print(cost_tracker.format_summary())
    if result.session_id:
        console.print(f"[dim]Session: {result.session_id}[/dim]")
    if result.error:
        console.print(f"[red]Error: {result.error}[/red]")
    if result.trajectory and verbose:
        console.print()
        console.rule("[bold]Trajectory[/bold]")
        for step in result.trajectory:
            console.print(f"  Step {step['step']}: [cyan]{step['tool']}[/cyan] {list(step['args'].keys())}")
            if step.get("error"):
                console.print(f"    [red]Error: {step['error']}[/red]")


@config_app.command("show")
def config_show() -> None:
    """Show current configuration."""
    s = get_settings()
    t = Table(title="RepoPilot Configuration", show_header=True, header_style="bold")
    t.add_column("Key"); t.add_column("Value")
    mask = lambda v: ("*" * 8 + v[-4:]) if v and len(v) > 8 else ("(set)" if v else "(not set)")
    rows = [
        ("model", s.model or "(not set)"),
        ("fast_model", s.fast_model or "(same as model)"),
        ("strong_model", s.strong_model or "(same as model)"),
        ("api_key", mask(s.api_key)),
        ("base_url", s.base_url or "(default OpenAI)"),
        ("sandbox_type", s.sandbox_type),
        ("approval_mode", s.approval_mode),
        ("max_steps", str(s.max_steps)),
        ("budget_tokens", str(s.budget_tokens)),
        ("config_file", str(s.config_file)),
        ("home_dir", str(s.home_dir)),
    ]
    for k, v in rows:
        t.add_row(k, v)
    console.print(t)


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Config key (model, api_key, base_url, sandbox_type, approval_mode, ...)"),
    value: str = typer.Argument(..., help="New value"),
) -> None:
    """Set a config value and persist to config.toml."""
    s = get_settings()
    allowed = {"model","fast_model","strong_model","api_key","base_url",
               "sandbox_type","approval_mode","max_steps","budget_tokens",
               "tool_timeout","docker_image","docker_network","docker_mem_limit",
               "stream","cost_tracking"}
    if key not in allowed:
        console.print(f"[red]Unknown key: {key}[/red]")
        console.print(f"Valid keys: {', '.join(sorted(allowed))}")
        raise typer.Exit(1)
    # Coerce types
    if key in ("max_steps","budget_tokens","tool_timeout"):
        try:
            value_int = int(value)
        except ValueError:
            console.print(f"[red]{key} must be an integer[/red]"); raise typer.Exit(1)
        setattr(s, key, value_int)
    elif key in ("stream","cost_tracking"):
        setattr(s, key, value.lower() in ("1","true","yes","on"))
    else:
        setattr(s, key, value)
    # Validate
    try:
        s.__post_init__()
    except ValueError as e:
        console.print(f"[red]Invalid value: {e}[/red]"); raise typer.Exit(1)
    s.save()
    reset_settings_for_tests()
    console.print(f"[green]Set {key} = {value}[/green]")


@config_app.command("init")
def config_init() -> None:
    """Re-run the first-run configuration wizard."""
    s = Settings()
    s.ensure_dirs()
    # delete old config so wizard runs
    if s.config_file.exists():
        s.config_file.unlink()
    reset_settings_for_tests()
    _ensure_configured()


if __name__ == "__main__":
    app()
