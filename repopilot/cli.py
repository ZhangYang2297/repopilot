from __future__ import annotations
import os

# Prevent LiteLLM from fetching model cost map on import (causes 10s+ timeout)
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "1")

import typer
from rich.console import Console
from rich.table import Table
from pathlib import Path

from repopilot.config import get_settings, reset_settings_for_tests, Settings

# Fix Windows console encoding for Rich/Markdown output (bullet chars, etc.)
import sys as _sys
import io as _io
if _sys.platform == "win32":
    try:
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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
    help="RepoPilot - Local-first code agent. / \u672c\u5730\u4f18\u5148\u7684 AI \u4ee3\u7801\u52a9\u624b\u3002",
    add_completion=False,
    no_args_is_help=False,
)
console = Console()
config_app = typer.Typer(help="Manage configuration.", add_completion=False)
app.add_typer(config_app, name="config")


_TRANSLATIONS = {
    "welcome_title": {"en": "Welcome to RepoPilot", "zh": "\u6b22\u8fce\u4f7f\u7528 RepoPilot"},
    "first_run_intro": {"en": "Let\u2019s set up your LLM provider step by step.\n", "zh": "\u6765\u9010\u6b65\u914d\u7f6e\u4f60\u7684 LLM \u63d0\u4f9b\u5546\u3002\n"},
    "select_language": {"en": "Select language", "zh": "\u9009\u62e9\u8bed\u8a00"},
    "enter_language": {"en": "Language (en/zh)", "zh": "\u8bed\u8a00 (en/zh)"},
    "config_url": {"en": "Step 1/3 \u2014 Base URL", "zh": "\u6b65\u9aa4 1/3 \u2014 \u63a5\u53e3\u5730\u5740"},
    "config_url_prompt": {"en": "Base URL (press Enter for default OpenAI, or enter e.g. https://ark.cn-beijing.volces.com/api/v3)", "zh": "\u63a5\u53e3\u5730\u5740\uff08\u76f4\u63a5\u56de\u8f66\u4f7f\u7528\u9ed8\u8ba4 OpenAI\uff0c\u6216\u8f93\u5165\u4f8b\u5982 https://ark.cn-beijing.volces.com/api/v3\uff09"},
    "config_url_help": {"en": "You can find this in your LLM provider dashboard. For Volcengine ARK, it is https://ark.cn-beijing.volces.com/api/v3", "zh": "\u53ef\u4ee5\u5728\u4f60\u7684 LLM \u63d0\u4f9b\u5546\u63a7\u5236\u53f0\u627e\u5230\u3002\u706b\u5c71\u5f15\u64ce ARK \u7684\u5730\u5740\u662f https://ark.cn-beijing.volces.com/api/v3"},
    "config_key": {"en": "Step 2/3 \u2014 API Key", "zh": "\u6b65\u9aa4 2/3 \u2014 API \u5bc6\u94a5"},
    "config_key_prompt": {"en": "API key (sk-...)", "zh": "API \u5bc6\u94a5\uff08sk-...\uff09"},
    "config_key_help": {"en": "Your API key from the LLM provider. It usually starts with sk-.", "zh": "\u4ece LLM \u63d0\u4f9b\u5546\u83b7\u53d6\u7684 API \u5bc6\u94a5\uff0c\u901a\u5e38\u4ee5 sk- \u5f00\u5934\u3002"},
    "config_model": {"en": "Step 3/3 \u2014 Model", "zh": "\u6b65\u9aa4 3/3 \u2014 \u6a21\u578b"},
    "config_model_prompt": {"en": "Model (provider/model, e.g. openai/doubao-seed-evolving for Volcengine ARK, or openai/gpt-4o)", "zh": "\u6a21\u578b\uff08provider/model \u683c\u5f0f\uff0c\u4f8b\u5982 openai/doubao-seed-evolving \u6216 openai/gpt-4o\uff09"},
    "config_model_help": {"en": "Recommended: doubao-seed-evolving (5M tokens), doubao-seed-2-1-pro-260628, deepseek-v3-2-251201", "zh": "\u63a8\u8350\uff1adoubao-seed-evolving\uff085M \u4e0a\u4e0b\u6587\uff09\u3001doubao-seed-2-1-pro-260628\u3001deepseek-v3-2-251201"},
    "test_connection": {"en": "Test connection?", "zh": "\u662f\u5426\u6d4b\u8bd5\u8fde\u63a5\uff1f"},
    "test_connection_prompt": {"en": "Test connection now? (Y/n)", "zh": "\u7acb\u5373\u6d4b\u8bd5\u8fde\u63a5\uff1f(Y/n)"},
    "test_skipping": {"en": "Skipping connection test. You can test later by running: repopilot", "zh": "\u8df3\u8fc7\u8fde\u63a5\u6d4b\u8bd5\u3002\u4e4b\u540e\u53ef\u4ee5\u901a\u8fc7 repopilot config init \u91cd\u65b0\u914d\u7f6e"},
    "test_success": {"en": "Connection successful!", "zh": "\u8fde\u63a5\u6210\u529f\uff01"},
    "test_failed": {"en": "Connection failed: {err}", "zh": "\u8fde\u63a5\u5931\u8d25\uff1a{err}"},
    "config_saved": {"en": "Configuration saved to {path}", "zh": "\u914d\u7f6e\u5df2\u4fdd\u5b58\u5230 {path}"},
    "config_summary": {"en": "Summary", "zh": "\u914d\u7f6e\u6458\u8981"},
    "config_tip": {"en": "Tip: Use `repopilot model <name>` to set different models for each tier", "zh": "\u63d0\u793a\uff1a\u4f7f\u7528 repopilot model <name> \u4e3a\u4e0d\u540c\u6863\u6b21\u8bbe\u7f6e\u4e0d\u540c\u6a21\u578b"},
    "invalid_choice": {"en": "Invalid choice. Please enter en or zh.", "zh": "\u65e0\u6548\u9009\u62e9\uff0c\u8bf7\u8f93\u5165 en \u6216 zh\u3002"},
    "config_skip": {"en": "To reconfigure later, run: repopilot config init", "zh": "\u4e4b\u540e\u53ef\u91cd\u65b0\u8fd0\u884c repopilot config init \u91cd\u65b0\u914d\u7f6e"},
}


def _t(key: str, lang: str = "en") -> str:
    return _TRANSLATIONS.get(key, {}).get(lang, _TRANSLATIONS.get(key, {}).get("en", key))


def _ensure_configured() -> None:
    """First-run bilingual wizard if no model is configured."""
    s = get_settings()
    if s.is_configured():
        return
    console.print()
    console.rule("[bold yellow]" + _t("welcome_title") + "[/bold yellow]")
    console.print(_t("first_run_intro"))

    # Step 0: Language selection
    lang = "en"
    lang_input = typer.prompt(_t("select_language") + " (en/zh)", default="en").strip().lower()
    if lang_input in ("en", "zh"):
        lang = lang_input
    else:
        console.print(_t("invalid_choice"))
    console.print()

    # Step 1: Base URL
    console.rule(f"[bold]{_t('config_url', lang)}[/bold]")
    console.print(f"[dim]{_t('config_url_help', lang)}[/dim]")
    base_url = typer.prompt(
        _t("config_url_prompt", lang),
        default="", show_default=False,
    ).strip()

    # Step 2: API Key
    console.rule(f"[bold]{_t('config_key', lang)}[/bold]")
    console.print(f"[dim]{_t('config_key_help', lang)}[/dim]")
    api_key = typer.prompt(_t("config_key_prompt", lang), hide_input=False).strip()

    # Step 3: Model
    console.rule(f"[bold]{_t('config_model', lang)}[/bold]")
    console.print(f"[dim]{_t('config_model_help', lang)}[/dim]")
    model = typer.prompt(_t("config_model_prompt", lang)).strip()
    if "/" not in model:
        model = f"openai/{model}"

    # Save configuration
    new = Settings.load(
        model=model,
        api_key=api_key,
        base_url=base_url or "",
        language=lang,
    )
    new.save()
    reset_settings_for_tests()

    console.print()
    console.rule(f"[bold green]{_t('config_saved', lang).format(path=new.config_file)}[/bold green]")
    console.print(f"  [bold]{_t('config_summary', lang)}:[/bold]")
    console.print(f"    model    = {new.model}")
    if new.base_url:
        console.print(f"    base_url = {new.base_url}")
    console.print(f"    language = {new.language}")

    # Optional: test connection
    console.print()
    test_choice = typer.prompt(_t("test_connection_prompt", lang), default="y").strip().lower()
    if test_choice in ("y", "yes", ""):
        try:
            import httpx
            test_url = base_url or "https://api.openai.com/v1"
            with httpx.Client(timeout=10) as client:
                resp = client.get(f"{test_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {api_key}"})
            if resp.status_code < 500:
                console.print(f"[green]\u2713 {_t('test_success', lang)}[/green]")
            else:
                console.print(f"[red]\u2717 {_t('test_failed', lang).format(err=resp.status_code)}[/red]")
        except Exception as e:
            console.print(f"[yellow]\u2717 {_t('test_failed', lang).format(err=e)}[/yellow]")
            console.print(f"[dim]{_t('config_skip', lang)}[/dim]")
    else:
        console.print(f"[dim]{_t('test_skipping', lang)}[/dim]")

    console.print()
    console.print(f"[dim]{_t('config_tip', lang)}[/dim]")
    console.print()


@app.callback(invoke_without_command=True)
def _main(ctx: typer.Context,
          repo: str = typer.Option(".", "--repo", "-r", help="Path to target repo"),
          sandbox: str = typer.Option("", "--sandbox", help="docker or local (default: from config)"),
          approval_mode: str = typer.Option("", "--approval-mode", help="auto|confirm|edit-only|deny (default: from config)"),
          model: str = typer.Option("", "--model", "-m", help="Override model"),
          verbose: bool = typer.Option(False, "--verbose", "-v"),
          version: bool = typer.Option(False, "--version", help="Show version and exit")):
    if version:
        from repopilot import __version__
        console.print(f"repopilot {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        _ensure_configured()
        from repopilot.repl import run_repl
        import os
        repo_path = Path(repo).resolve()
        run_repl(
            repo_path=repo_path,
            approval_mode=approval_mode or get_settings().approval_mode,
            sandbox_type=sandbox or get_settings().sandbox_type,
            model_override=model,
            verbose=verbose,
        )

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
    sandbox: str = typer.Option("", "--sandbox", help="docker or local (default: from config)"),
      approval_mode: str = typer.Option(
          "", "--approval-mode", help="auto|confirm|edit-only|deny (default: from config)"
      ),
    max_steps: int = typer.Option(50, "--max-steps", help="Max agent steps"),
    budget_tokens: int = typer.Option(200_000, "--budget-tokens", help="Input token budget"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run agent on a single task (non-interactive). Same as: repopilot -r . "task"."""
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
    approval_mode = approval_mode or s.approval_mode
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
        try:
            console.print(Markdown(result.summary))
        except Exception:
            console.print(result.summary)
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









