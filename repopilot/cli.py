from __future__ import annotations
import typer
from rich.console import Console

app = typer.Typer(
    name="repopilot",
    help="RepoPilot - Local-first code agent.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


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
    console.print("[yellow]TODO:[/yellow] agent loop not yet implemented")
    console.print(f"  task={task!r}")
    console.print(f"  repo={repo} model={model!r} sandbox={sandbox!r}")
    console.print(f"  approval_mode={approval_mode} max_steps={max_steps}")


if __name__ == "__main__":
    app()
