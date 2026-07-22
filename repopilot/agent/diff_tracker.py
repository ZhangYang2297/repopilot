"""Diff tracker — records file changes during an agent session for /diff and /undo.

Changes are grouped into **transactions** (one per agent turn):

    dt.begin_turn()           # start recording
    dt.record_edit(...)       # ...
    dt.commit_turn()          # publish as an atomic batch
    dt.rollback_current()     # OR discard everything since begin_turn
    dt.undo_last_transaction()# revert the most-recent committed batch

Legacy single-shot API (``record_*``, ``undo_last``, ``undo_all``,
``changes``, ``get_diffs``, ``get_changed_files``, ``clear``) remains
fully supported: any ``record_*`` call without a matching ``begin_turn``
auto-opens a transaction, and ``changes`` returns a flat view over both
committed transactions and the currently-open one.
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
    before: str = ""
    after: str = ""


@dataclass
class Transaction:
    """A group of file changes produced during one agent turn."""
    changes: list[FileChange] = field(default_factory=list)
    committed: bool = False


@dataclass
class DiffTracker:
    """Tracks file changes within a session for /diff and /undo."""
    repo_root: str
    transactions: list[Transaction] = field(default_factory=list)
    current: Optional[Transaction] = None

    # ── transaction lifecycle ────────────────────────────────
    def begin_turn(self) -> Transaction:
        if self.current is None:
            self.current = Transaction()
        return self.current

    def commit_turn(self) -> Optional[Transaction]:
        txn = self.current
        self.current = None
        if txn is None:
            return None
        if not txn.changes:
            return None
        txn.committed = True
        self.transactions.append(txn)
        return txn

    def rollback_current(self) -> list[str]:
        """Revert every change in the currently-open transaction and drop it."""
        if self.current is None:
            return []
        undone = self._revert_changes(self.current.changes)
        self.current = None
        return undone

    def undo_last_transaction(self) -> Optional[Transaction]:
        """Revert the most-recent committed transaction (or, if there is
        an open transaction with changes, that one) and pop it from history."""
        # Prefer the open transaction only if it has content; otherwise
        # target the most recent committed one.
        if self.current is not None and self.current.changes:
            undone = self._revert_changes(self.current.changes)
            txn = self.current
            self.current = None
            return txn if undone else None
        if not self.transactions:
            return None
        txn = self.transactions.pop()
        self._revert_changes(txn.changes)
        return txn

    def _revert_changes(self, changes: list[FileChange]) -> list[str]:
        root = Path(self.repo_root)
        undone: list[str] = []
        # Reverse order so a create-then-edit sequence unwinds correctly.
        for ch in reversed(changes):
            fpath = root / ch.path
            try:
                if ch.action == "create":
                    if fpath.exists():
                        fpath.unlink()
                else:  # "edit" or "overwrite"
                    fpath.parent.mkdir(parents=True, exist_ok=True)
                    fpath.write_text(ch.before, encoding="utf-8")
                undone.append(ch.path)
            except OSError:
                continue
        return undone

    # ── recording (auto-opens a transaction) ─────────────────
    def _ensure_open(self) -> Transaction:
        if self.current is None:
            self.current = Transaction()
        return self.current

    def record_edit(self, path: str, before: str, after: str) -> None:
        self._ensure_open().changes.append(
            FileChange(path=path, action="edit", before=before, after=after)
        )

    def record_new(self, path: str, content: str) -> None:
        self._ensure_open().changes.append(
            FileChange(path=path, action="create", before="", after=content)
        )

    def record_overwrite(self, path: str, before: str, after: str) -> None:
        self._ensure_open().changes.append(
            FileChange(path=path, action="overwrite", before=before, after=after)
        )

    # ── flat views over committed + current ──────────────────
    @property
    def changes(self) -> list[FileChange]:
        out: list[FileChange] = []
        for txn in self.transactions:
            out.extend(txn.changes)
        if self.current is not None:
            out.extend(self.current.changes)
        return out

    def get_diffs(self) -> list[str]:
        results: list[str] = []
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
        seen: list[str] = []
        for ch in self.changes:
            if ch.path not in seen:
                seen.append(ch.path)
        return seen

    # ── single-step legacy API ───────────────────────────────
    def undo_last(self, repo_root: Optional[str] = None) -> Optional[str]:
        """Revert the most recent individual change (single file).

        Walks: current open txn → most-recent committed txn.  Pops the
        change from its transaction; if the transaction becomes empty it
        is discarded from history."""
        if repo_root:
            self.repo_root = repo_root
        # Prefer current
        if self.current is not None and self.current.changes:
            ch = self.current.changes.pop()
            self._revert_changes([ch])
            if not self.current.changes:
                self.current = None
            return ch.path
        # Then most recent committed
        while self.transactions:
            txn = self.transactions[-1]
            if txn.changes:
                ch = txn.changes.pop()
                self._revert_changes([ch])
                if not txn.changes:
                    self.transactions.pop()
                return ch.path
            self.transactions.pop()
        return None

    def undo_all(self) -> list[str]:
        undone: list[str] = []
        while True:
            p = self.undo_last()
            if p is None:
                break
            undone.append(p)
        return undone

    def clear(self) -> None:
        self.transactions.clear()
        self.current = None
