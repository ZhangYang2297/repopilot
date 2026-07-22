from __future__ import annotations
import difflib
import fnmatch
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from repopilot.sandbox.base import (
    BINARY_EXTENSIONS,
    DEFAULT_IGNORE_DIRS,
    ExecResult,
    FileReadResult,
    GrepMatch,
    Sandbox,
)


class LocalSandbox(Sandbox):
    """Sandbox that operates directly on the local filesystem.
    Used for trusted repos / development. Path traversal protection enforced."""

    def setup(self) -> None:
        if not self.repo_path.exists():
            raise FileNotFoundError(f"Repo path does not exist: {self.repo_path}")

    def teardown(self) -> None:
        pass  # nothing to clean up locally

    # ── file ops ──────────────────────────────────
    def read_file(self, path: str, offset: int = 0, limit: int = 200) -> FileReadResult:
        p = self._safe_path(path)
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        total = len(all_lines)
        start = max(0, offset)
        end = min(total, start + limit)
        selected = all_lines[start:end]
        numbered = self._add_line_numbers("".join(selected), start_line=start + 1)
        return FileReadResult(
            path=str(p.relative_to(self.repo_path)),
            content=numbered,
            start_line=start + 1,
            total_lines=total,
            truncated=end < total,
        )

    def write_file(self, path: str, content: str) -> None:
        """Atomic write: write to .tmp then os.replace() (atomic on same filesystem)."""
        p = self._safe_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        import tempfile
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.tmp.", suffix="")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, str(p))  # atomic on POSIX and Windows (if same volume)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def edit_file(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        p = self._safe_path(path)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {path}")
        # Require context for unique matches (but allow replace_all with short strings)
        stripped = old_string.strip()
        if len(stripped) < 3:
            raise ValueError(
                f"old_string too short ({len(stripped)} non-whitespace chars). "
                f"Include at least 3 chars of surrounding context for reliable matching."
            )
        original = p.read_text(encoding="utf-8", errors="replace")
        occurrences = original.count(old_string)
        if occurrences == 0:
            close = difflib.get_close_matches(old_string, original.splitlines(), n=1, cutoff=0.6)
            hint = f"\nClosest match:\n{close[0]!r}" if close else ""
            raise ValueError(f"old_string not found in {path}.{hint}")
        if occurrences > 1 and not replace_all:
            raise ValueError(
                f"old_string appears {occurrences} times in {path}. "
                f"Include more surrounding context to make the match unique, or set replace_all=true."
            )
        count = -1 if replace_all else 1
        new_content = original.replace(old_string, new_string, count)
        # Atomic write: temp file then os.replace (atomic on same volume)
        import tempfile as _tf
        tmp_fd, tmp_path = _tf.mkstemp(dir=str(p.parent), prefix=f".{p.name}.edit.", suffix="")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(new_content)
            os.replace(tmp_path, str(p))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{p.relative_to(self.repo_path)}",
            tofile=f"b/{p.relative_to(self.repo_path)}",
        )
        return "".join(diff)


    # ── exec ──────────────────────────────────────
    def exec(self, command: str, timeout: int = 30, cwd: Optional[str] = None) -> ExecResult:
        workdir = str(self.repo_path)
        if cwd:
            workdir = str(self._safe_path(cwd))
        t0 = time.time()
        timed_out = False
        stdout = ""
        stderr = ""
        exit_code = 0
        try:
            # On Windows shell=True is needed for builtins; use cmd /c
            result = subprocess.run(
                command,
                shell=True,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            exit_code = result.returncode
        except subprocess.TimeoutExpired as e:
            timed_out = True
            stdout = (e.stdout or b"").decode("utf-8", errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            stderr = (e.stderr or b"").decode("utf-8", errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
            exit_code = -1
        return ExecResult(
            command=command,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timed_out=timed_out,
            duration_ms=int((time.time() - t0) * 1000),
        )

    # ── navigation ────────────────────────────────
    def glob(self, pattern: str) -> list[str]:
        results: list[str] = []
        norm = pattern.replace("\\", "/")
        # Handle recursive glob
        if norm.startswith("**/"):
            base = self.repo_path
            pat = norm[3:]
            recursive = True
        elif "/" in norm:
            parts = norm.split("/")
            base = self.repo_path.joinpath(*parts[:-1])
            pat = parts[-1]
            recursive = True
        else:
            base = self.repo_path
            pat = norm
            recursive = False
        base = base.resolve()
        if not base.exists():
            return []
        walker = base.rglob(pat) if recursive else base.glob(pat)
        for p in walker:
            if any(part in DEFAULT_IGNORE_DIRS for part in p.parts):
                continue
            if p.is_file():
                try:
                    results.append(str(p.relative_to(self.repo_path)).replace(os.sep, "/"))
                except ValueError:
                    continue
        return sorted(results)

    def grep(self, pattern: str, glob_filter: Optional[str] = None,
             ignore_case: bool = False) -> list[GrepMatch]:
        flags = 0
        if ignore_case:
            flags |= re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {e}")
        results: list[GrepMatch] = []
        # Walk repo and search files
        for root, dirs, files in os.walk(self.repo_path):
            # filter ignored dirs in-place
            dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORE_DIRS]
            for fname in files:
                if glob_filter and not fnmatch.fnmatch(fname, glob_filter):
                    continue
                fpath = Path(root) / fname
                try:
                    rel = str(fpath.relative_to(self.repo_path)).replace(os.sep, "/")
                except ValueError:
                    continue
                if fpath.suffix.lower() in BINARY_EXTENSIONS:
                    continue
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        for i, line in enumerate(f, 1):
                            if regex.search(line):
                                results.append(GrepMatch(
                                    file=rel,
                                    line_no=i,
                                    content=line.rstrip("\n")[:500],
                                ))
                except (OSError, UnicodeDecodeError):
                    continue
        return results

    def list_dir(self, path: str = ".", max_depth: int = 2) -> dict:
        base = self._safe_path(path)
        if not base.exists():
            return {}

        def _walk(p: Path, depth: int) -> dict:
            if depth > max_depth:
                return None
            out: dict = {}
            try:
                entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))
            except OSError:
                return out
            for entry in entries:
                if entry.name in DEFAULT_IGNORE_DIRS or entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    if depth < max_depth:
                        out[entry.name + "/"] = _walk(entry, depth + 1)
                    else:
                        out[entry.name + "/"] = None  # not expanded
                else:
                    out[entry.name] = None
            return out

        tree = _walk(base, 0)
        return tree

    def get_repo_tree(self, max_tokens: int = 4000) -> str:
        """Tree-sitter powered repo map with fallback to simple file listing."""
        try:
            from repopilot.code_index.repo_map import RepoMapBuilder
            return RepoMapBuilder.from_sandbox(self, max_tokens=max_tokens)
        except Exception:
            return self._fallback_repo_tree(max_tokens)

    def _fallback_repo_tree(self, max_tokens: int = 4000) -> str:
        """List ALL non-ignored files (any extension), similar to ``rg --files``.

        Previously restricted to a whitelist of source extensions which
        silently hid html/css/vue/txt/... files from the model.
        """
        try:
            from repopilot.code_index.ignore import iter_all_files
            all_files = [
                str(p.relative_to(self.repo_path)).replace("\\", "/")
                for p in iter_all_files(self.repo_path, max_files=500)
            ]
        except Exception:
            all_files = self.glob("**/*")
        all_files = sorted(set(all_files))
        lines = [f"# Repo tree: {self.repo_path.name}", ""]
        lines.append("Files:")
        total = 0
        for f in all_files[:200]:
            line = f"  {f}"
            if total + len(line) > max_tokens * 4:
                idx = all_files.index(f)
                lines.append(f"  ... (+{len(all_files) - idx} more)")
                break
            lines.append(line)
            total += len(line)
        return "\n".join(lines)

