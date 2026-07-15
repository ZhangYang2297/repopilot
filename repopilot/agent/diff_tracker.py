"""Diff tracker — records file changes during an agent session for /diff and /undo.

Tracks:
- edit_file: before/after content
- write_file (new): created files
- write_file (overwrite): before/after content

Used by REPL for /diff (show changes) and /undo (rollback last change).
"""
from __future__ import annotations
import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class FileChange:
    """A single file change event."""
    path: str
    action: str  # "edit" | "create" | "overwrite"
    before: str = ""  # content before change (empty for new files)
    after: str = ""   # content after change


@dataclass
class DiffTracker:
    """Tracks file changes within a session for /diff and /undo."""
    repo_root: str
    changes: list[FileChange] = field(default_factory=list)

    def record_edit(self, path: str, before: str, after: str) -> None:
        """Record an edit_file operation."""
        self.changes.append(FileChange(path=path, action="edit", before=before, after=after))

    def record_new(self, path: str, content: str) -> None:
        """Record a new file creation."""
        self.changes.append(FileChange(path=path, action="create", before="", after=content))

    def record_overwrite(self, path: str, before: str, after: str) -> None:
        """Record a write_file that overwrote an existing file."""
        self.changes.append(FileChange(path=path, action="overwrite", before=before, after=after))

    def get_diffs(self) -> list[str]:
        """Return unified diff strings for all tracked changes."""
        results = []
        for ch in self.changes:
            if ch.action == "create":
                diff_lines = [f"+++ b/{ch.path}", "@@ new file @@"]
                for line in ch.after.splitlines(keepends=True):
                    diff_lines.append(f"+{line.rstrip()}")
                results.append("\n".join(diff_lines))
            else:
                before_lines = ch.before.splitlines(keepends=True)
                after_lines = ch.after.splitlines(keepends=True)
                diff = difflib.unified_diff(
                    before_lines, after_lines,
                    fromfile=f"a/{ch.path}", tofile=f"b/{ch.path}",
                )
                results.append("".join(diff))
        return results

    def get_changed_files(self) -> list[str]:
        """Return list of paths that were modified."""
        seen = []
        for ch in self.changes:
            if ch.path not in seen:
                seen.append(ch.path)
        return seen

    def undo_last(self, repo_root: Optional[str] = None) -> Optional[str]:
        """Undo the last recorded change. Returns path of undone file or None."""
        if not self.changes:
            return None
        ch = self.changes.pop()
        root = Path(repo_root or self.repo_root)
        fpath = root / ch.path
        if ch.action == "create":
            if fpath.exists():
                fpath.unlink()
        elif ch.action in ("edit", "overwrite"):
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(ch.before, encoding="utf-8")
        return ch.path

    def undo_all(self) -> list[str]:
        """Undo all tracked changes in reverse order."""
        undone = []
        while self.changes:
            p = self.undo_last()
            if p:
                undone.append(p)
        return undone

    def clear(self) -> None:
        self.changes.clear()
