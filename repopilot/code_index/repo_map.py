"""Repo Map builder — generates a compact code structure overview.

The repo map lists source files grouped by directory, showing class/function
signatures and docstrings extracted by tree-sitter.  Output is bounded by a
token budget so it can be injected into the LLM system prompt without blowing
the context window.
"""
from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING

from repopilot.code_index.ignore import iter_source_files, iter_all_files, is_ignored, SOURCE_EXTENSIONS
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
        body = header + "\n".join(sections)

        # Also list non-source files (html/css/txt/json/etc.) so the model
        # sees the *full* repo contents, not just code files.
        try:
            source_paths = {str(pp.relative_to(self.repo_root)).replace("\\", "/")
                            for _sz, pp in files}
            others: list[str] = []
            for p2 in iter_all_files(self.repo_root, max_files=500):
                rel = str(p2.relative_to(self.repo_root)).replace("\\", "/")
                if rel in source_paths:
                    continue
                others.append(rel)
            if others:
                remaining_chars = max_chars - len(body)
                other_lines = ["", "## Other files"]
                used = sum(len(x) + 3 for x in other_lines)
                for rel in others:
                    line = f"  {rel}"
                    if used + len(line) + 1 > remaining_chars:
                        other_lines.append(f"  ... (+{len(others) - (len(other_lines) - 2)} more)")
                        break
                    other_lines.append(line)
                    used += len(line) + 1
                body += "\n" + "\n".join(other_lines)
        except Exception:
            pass

        return body

    def update(self, path: str) -> None:
        """Invalidate cache for a single file (after edits)."""
        norm = path.replace("\\", "/")
        self._cache.pop(norm, None)

    @classmethod
    def from_sandbox(cls, sandbox: "Sandbox", max_tokens: int = 4000) -> str:
        """Convenience: build map from a Sandbox instance."""
        return cls(sandbox.repo_path, max_tokens=max_tokens).build()
