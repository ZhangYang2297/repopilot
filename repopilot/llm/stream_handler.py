from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown


@dataclass
class StreamEvent:
    """A single event from a streaming LLM response."""
    type: str  # "text_delta" | "tool_call" | "done" | "thinking"
    content: str = ""
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None


class RichStreamHandler:
    """Renders streaming LLM output to the terminal via Rich."""

    def __init__(self, console: Optional[Console] = None, show_thinking: bool = False):
        self.console = console or Console()
        self.show_thinking = show_thinking
        self._buffer = ""
        self._live: Optional[Live] = None

    def start(self) -> None:
        self._buffer = ""
        self._live = Live("", console=self.console, refresh_per_second=10, transient=False)
        self._live.start()

    def on_event(self, ev: StreamEvent) -> None:
        if self._live is None:
            self.start()
        assert self._live is not None
        if ev.type == "text_delta":
            self._buffer += ev.content
            self._live.update(Markdown(self._buffer))
        elif ev.type == "thinking" and self.show_thinking:
            self.console.print(f"[dim italic]{ev.content}[/dim italic]")
        elif ev.type == "tool_call":
            self.console.print(f"  [cyan]→ {ev.tool_name}[/cyan]")
            self._buffer = ""
            self._live.update("")
        elif ev.type == "done":
            self._live.stop()
            self._live = None
            if self._buffer:
                self.console.print()

    def stop(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None
