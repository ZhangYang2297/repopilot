"""Logging configuration for RepoPilot.

Logs go to:
  - stderr (console) at INFO level, human-friendly format
  - ~/.repopilot/logs/repopilot.log at DEBUG level, JSON lines (for debugging/trajectory replay)

Uses structlog for structured logging. Falls back to stdlib logging if structlog
is not configured.
"""
from __future__ import annotations
import logging
import sys
from pathlib import Path


def setup_logging(home_dir: Path, level: str = "INFO") -> None:
    """Configure root logger and structlog pipeline.

    Safe to call multiple times; handlers are not duplicated.
    """
    log_dir = home_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "repopilot.log"

    root = logging.getLogger("repopilot")
    root.setLevel(logging.DEBUG)
    # Avoid duplicate handlers on re-init
    root.handlers.clear()

    # Console handler (stderr, INFO+)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console_fmt = logging.Formatter("[%(levelname)s] %(name)s: %(message)s")
    console.setFormatter(console_fmt)
    root.addHandler(console)

    # File handler (~/.repopilot/logs/repopilot.log, DEBUG, JSON lines)
    file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    json_fmt = logging.Formatter(
        '{"ts":"%(asctime)s","level":"%(levelname)s","name":"%(name)s","msg":%(message)s}'
    )
    file_handler.setFormatter(json_fmt)
    root.addHandler(file_handler)


def get_logger(name: str = "repopilot") -> logging.Logger:
    """Return a namespaced logger under the repopilot root."""
    return logging.getLogger(f"repopilot.{name}" if name != "repopilot" else "repopilot")
