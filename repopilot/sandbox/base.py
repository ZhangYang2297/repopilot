from __future__ import annotations
import abc
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional


@dataclass
class ExecResult:
    """Result of executing a shell command."""
    command: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False
    interrupted: bool = False
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def truncated(self, head: int = 500, tail: int = 1500) -> str:
        """Format result for LLM consumption, with long output head+tail truncated."""
        out = self.stdout
        if self.stderr:
            out = out + ("\n[stderr]\n" if out else "[stderr]\n") + self.stderr
        out = out.rstrip()
        if len(out) <= head + tail + 50:
            prefix = f"$ {self.command}\n" if self.command else ""
            return f"{prefix}{out}\n[exit_code={self.exit_code}]"
        keep_head = out[:head]
        keep_tail = out[-tail:]
        skipped = len(out) - head - tail
        prefix = f"$ {self.command}\n" if self.command else ""
        return (f"{prefix}{keep_head}\n...[truncated {skipped} chars]...\n{keep_tail}\n"
                f"[exit_code={self.exit_code}{', timed_out' if self.timed_out else ''}]")


@dataclass
class GrepMatch:
    file: str
    line_no: int
    content: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line_no}:{self.content}"


@dataclass
class FileReadResult:
    path: str
    content: str           # content WITH line numbers
    start_line: int = 1
    total_lines: int = 0
    truncated: bool = False


# Binary file extensions to skip in grep/read
BINARY_EXTENSIONS = frozenset({
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".bin",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".mp4", ".wav", ".avi", ".mkv", ".mov",
    ".db", ".sqlite", ".sqlite3",
    ".class", ".jar", ".war",
    ".o", ".obj", ".a", ".lib", ".pyd",
})

# Patterns for directories to skip in grep/glob/list_dir
DEFAULT_IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    "dist", "build", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".tox", ".idea", ".vscode", "*.egg-info", ".eggs",
}


# Sensitive-file rules.  Each rule is a callable(relative_posix_path) -> bool.
# We keep the rules as data so tests can enumerate them and the audit log
# can name which rule fired.
def _match_sensitive(rel: "Path") -> str | None:
    """Return the name of the matching sensitive-file rule, or None."""
    parts = tuple(part.lower() for part in rel.parts)
    name = parts[-1] if parts else ""

    # SSH: anything under a `.ssh` directory, or private-key filenames.
    if any(part == ".ssh" for part in parts):
        return "ssh-dir"
    if name in {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}:
        return "ssh-privkey"
    if name.endswith(".pem") or name.endswith(".key"):
        return "private-key-extension"

    # AWS / cloud credentials.
    if any(part == ".aws" for part in parts) and name in {"credentials", "config"}:
        return "aws-credentials"

    # dotenv files (allow only *.example / *.sample / *.template).
    if name == ".env" or (
        name.startswith(".env.")
        and not name.endswith((".example", ".sample", ".template"))
    ):
        return "dotenv"

    # Misc unix secrets.
    if name in {".netrc", ".pgpass"}:
        return "unix-secret"

    # Git internals that often contain remote-URL tokens.
    if len(parts) >= 2 and parts[-2] == ".git" and name in {"config", "credentials"}:
        return "git-internal-secret"

    return None


class Sandbox(abc.ABC):
    """Abstract sandbox interface. All tools talk to the repo through this."""

    def __init__(self, repo_path: Path):
        self.repo_path = Path(repo_path).resolve()

    # ── lifecycle ─────────────────────────────────
    @abc.abstractmethod
    def setup(self) -> None:
        """Initialize the sandbox (start container, etc.)."""

    @abc.abstractmethod
    def teardown(self) -> None:
        """Clean up (stop container, etc.)."""

    def __enter__(self):
        self.setup()
        return self

    def __exit__(self, *exc):
        self.teardown()

    # ── path safety ───────────────────────────────
    def _safe_path(self, user_path: str) -> Path:
        """Resolve a user-supplied path and ensure it is inside the repo
        AND not a well-known secret file (unless the user opted in via
        the REPOPILOT_ALLOW_SENSITIVE=1 environment variable)."""
        p = (self.repo_path / user_path).resolve()
        try:
            rel = p.relative_to(self.repo_path)
        except ValueError:
            raise PermissionError(
                f"Path escapes repo: {user_path} -> {p}\n"
                f"Repo root is {self.repo_path}"
            )
        hit = _match_sensitive(rel)
        if hit is not None:
            import os as _os
            if _os.environ.get("REPOPILOT_ALLOW_SENSITIVE") not in ("1", "true", "TRUE", "yes"):
                raise PermissionError(
                    f"Refusing to touch sensitive path {user_path!r} "
                    f"(matched rule: {hit}). Set REPOPILOT_ALLOW_SENSITIVE=1 "
                    f"to override."
                )
        return p

    # ── file ops ──────────────────────────────────
    @abc.abstractmethod
    def read_file(self, path: str, offset: int = 0, limit: int = 200) -> FileReadResult:
        """Read file with line numbers. offset is 0-indexed line offset, limit=max lines."""

    @abc.abstractmethod
    def write_file(self, path: str, content: str) -> None:
        """Overwrite file with content."""

    @abc.abstractmethod
    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        """Replace first occurrence of old_string with new_string. Returns unified diff.
        Raises ValueError if old_string is not found."""

    # ── execution ─────────────────────────────────
    @abc.abstractmethod
    def exec(self, command: str, timeout: int = 30, cwd: Optional[str] = None) -> ExecResult:
        """Run shell command, return ExecResult."""

    # ── navigation ────────────────────────────────
    @abc.abstractmethod
    def glob(self, pattern: str) -> list[str]:
        """List files matching glob pattern (relative to repo root)."""

    @abc.abstractmethod
    def grep(self, pattern: str, glob_filter: Optional[str] = None,
             ignore_case: bool = False) -> list[GrepMatch]:
        """Search for regex pattern in repo files."""

    @abc.abstractmethod
    def list_dir(self, path: str = ".", max_depth: int = 2) -> dict:
        """Return directory tree as a nested dict: {name: {children...}|None}."""

    @abc.abstractmethod
    def get_repo_tree(self, max_tokens: int = 4000) -> str:
        """Return a string representation of repo for Repo Map (placeholder in T4, T11 replaces with tree-sitter)."""

    # ── helpers for subclasses ────────────────────
    @staticmethod
    def _add_line_numbers(text: str, start_line: int = 1) -> str:
        lines = text.splitlines()
        width = max(3, len(str(len(lines) + start_line)))
        return "\n".join(f"{i:{width}}|{line}" for i, line in enumerate(lines, start_line))

