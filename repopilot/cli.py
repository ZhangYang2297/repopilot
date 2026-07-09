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
    console.print("[yellow]TODO:[/yellow] agent loop not yet implemented")
    s = get_settings()
    console.print(f"  task={task!r}")
    console.print(f"  repo={repo} model={model or s.model} sandbox={sandbox or s.sandbox_type!r}")
    console.print(f"  approval_mode={approval_mode} max_steps={max_steps}")


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
