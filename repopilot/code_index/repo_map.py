"""Repo Map builder — generates a compact code structure overview.

The repo map lists source files grouped by directory, showing class/function
signatures and docstrings extracted by tree-sitter.  Output is bounded by a
token budget so it can be injected into the LLM system prompt without blowing
the context window.
"""
from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING

from repopilot.code_index.ignore import iter_source_files, is_ignored, SOURCE_EXTENSIONS
from repopilot.code_index.symbol_index import index_file, format_file_symbols, FileSymbols

if TYPE_CHECKING:
    from repopilot.sandbox.base import Sandbox

# Rough char-to-token ratio for English/code: ~4 chars per token
CHARS_PER_TOKEN = 4


class RepoMapBuilder:
    """Build a code-structure map of the repository within a token budget."""

    def __init__(self, repo_root: Path, max_tokens: int = 4000):
        self.repo_root = Path(repo_root).resolve()
        self.max_tokens = max_tokens
        self._cache: dict[str, FileSymbols] = {}

    def build(self) -> str:
        """Scan repo and return a formatted repo map string."""
        max_chars = self.max_tokens * CHARS_PER_TOKEN

        # Collect all source files, prioritized by size (small files first)
        files: list[Path] = []
        for p in iter_source_files(self.repo_root):
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size > 100_000:  # skip files >100KB
                continue
            files.append((size, p))
        files.sort(key=lambda x: x[0])

        sections: list[str] = []
        total_chars = len("# Repo Map\n\n")

        for _size, path in files:
            rel = str(path.relative_to(self.repo_root)).replace("\\", "/")
            fs = index_file(path, self.repo_root)
            if fs:
                section = format_file_symbols(fs)
            else:
                # Non-indexed file: just list path with line count
                try:
                    lines = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
                except Exception:
                    lines = 0
                section = f"{rel} ({lines} lines)"

            section_chars = len(section) + 1  # +1 for newline
            if total_chars + section_chars > max_chars:
                remaining = len(files) - len(sections)
                sections.append(f"\n... ({remaining} more files omitted due to token budget)")
                break
            sections.append(section)
            total_chars += section_chars

        header = f"# Repo Map: {self.repo_root.name}\n"
        return header + "\n".join(sections)

    def update(self, path: str) -> None:
        """Invalidate cache for a single file (after edits)."""
        norm = path.replace("\\", "/")
        self._cache.pop(norm, None)

    @classmethod
    def from_sandbox(cls, sandbox: "Sandbox", max_tokens: int = 4000) -> str:
        """Convenience: build map from a Sandbox instance."""
        return cls(sandbox.repo_path, max_tokens=max_tokens).build()
