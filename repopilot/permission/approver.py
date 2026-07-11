"""Interactive and non-interactive approvers for permission decisions."""
from __future__ import annotations
import abc
from typing import TYPE_CHECKING

from repopilot.permission.engine import PermissionDecision

if TYPE_CHECKING:
    from rich.console import Console


class Approver(abc.ABC):
    """Abstract approver interface.

    When the engine returns "ask", the agent loop calls ``approver.ask()``
    to get a final decision from the user or policy.
    """

    @abc.abstractmethod
    def ask(self, tool_name: str, args: dict, reason: str = "") -> str:
        """Prompt for approval.

        Returns one of:
            "y"  – allow this call once
            "a"  – always allow this (tool, arg) pair this session
            "n"  – deny this call once, continue
            "d"  – deny and stop the whole task
            "e"  – edit the arguments before allowing
        """

    def notify_denied(self, tool_name: str, args: dict, reason: str) -> None:
        """Optional: notify user that a tool was auto-denied."""


class AutoApprover(Approver):
    """Non-interactive approver used in tests / CI / --approval-mode auto.

    Behavior:
      - allow decisions: return "y"
      - ask decisions:   return "y" (treat as allow in auto mode)
      - deny decisions:  return "n"
    """

    def __init__(self, default: str = "y"):
        self._default = default

    def ask(self, tool_name: str, args: dict, reason: str = "") -> str:
        return self._default

    def notify_denied(self, tool_name: str, args: dict, reason: str) -> None:
        pass  # silent in auto mode


class CLIApprover(Approver):
    """Rich-interactive approver for terminal use.

    Displays the pending tool call and prompts the user:
      [y]es [a]lways [n]o [d]eny-and-stop [e]dit
    """

    def __init__(self, console: "Console | None" = None):
        from rich.console import Console
        self.console = console or Console()

    def ask(self, tool_name: str, args: dict, reason: str = "") -> str:
        from rich.panel import Panel
        from rich.text import Text
        import shutil

        terminal_width = shutil.get_terminal_size((80, 20)).columns
        width = min(terminal_width - 4, 100)

        # Build display of arguments (truncated)
        arg_lines = []
        for k, v in args.items():
            vs = str(v).replace("\n", " ")
            if len(vs) > width - 20:
                vs = vs[:width - 23] + "..."
            arg_lines.append(f"  [cyan]{k}[/cyan] = {vs}")

        body = Text.from_markup(
            f"[bold yellow]⚠ Tool:[/bold yellow] {tool_name}\n"
            + ("\n".join(arg_lines) if arg_lines else "")
            + (f"\n\n[dim]{reason}[/dim]" if reason else "")
        )
        self.console.print(Panel(body, title="Approval Required", border_style="yellow"))

        while True:
            choice = self.console.input(
                "[bold green]Allow?[/bold green] "
                "[y]es [a]lways [n]o [d]eny-stop [e]dit > "
            ).strip().lower()
            if choice in ("y", "yes", ""):
                return "y"
            if choice in ("a", "always"):
                return "a"
            if choice in ("n", "no"):
                return "n"
            if choice in ("d", "deny"):
                return "d"
            if choice in ("e", "edit"):
                return "e"
            self.console.print("[red]Invalid choice, try again.[/red]")

    def notify_denied(self, tool_name: str, args: dict, reason: str) -> None:
        from rich.text import Text
        msg = Text.from_markup(f"[red]✗ Denied:[/red] {tool_name} — {reason}")
        self.console.print(msg)
