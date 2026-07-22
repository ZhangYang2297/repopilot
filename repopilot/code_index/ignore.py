"""File ignore patterns — combines .gitignore with built-in defaults."""
from __future__ import annotations
from pathlib import Path
from typing import Iterable

try:
    import pathspec
    HAS_PATHSPEC = True
except ImportError:
    HAS_PATHSPEC = False


BUILTIN_IGNORE = [
    ".git/",
    "__pycache__/",
    "node_modules/",
    ".venv/",
    "venv/",
    "env/",
    "dist/",
    "build/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".tox/",
    ".idea/",
    ".vscode/",
    ".eggs/",
    "*.egg-info/",
    "*.min.js",
    "*.min.css",
    "*.pyc",
    "*.pyo",
    ".DS_Store",
    "Thumbs.db",
    "*.so",
    "*.dylib",
    "*.dll",
    "*.exe",
    "*.bin",
    "*.pkl",
    "*.model",
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.ico", "*.svg",
    "*.pdf", "*.zip", "*.tar", "*.gz", "*.bz2", "*.7z", "*.rar",
    "*.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile.lock",
]

# Source code file extensions we know how to index
SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".go", ".rs", ".java", ".kt", ".rb", ".php",
    ".c", ".h", ".cpp", ".cc", ".hpp", ".cxx",
    ".cs", ".swift", ".scala",
    ".sh", ".bash", ".zsh", ".fish",
    ".md", ".rst", ".txt",
    ".toml", ".yaml", ".yml", ".json",
}


def _load_gitignore(repo_root: Path) -> "pathspec.PathSpec | None":
    """Load .gitignore if pathspec is available and file exists."""
    if not HAS_PATHSPEC:
        return None
    gi = repo_root / ".gitignore"
    if not gi.exists():
        return None
    try:
        lines = gi.read_text(encoding="utf-8", errors="replace").splitlines()
        return pathspec.PathSpec.from_lines("gitwildmatch", lines)
    except Exception:
        return None


def is_ignored(rel_path: str, repo_root: Path) -> bool:
    """Return True if the file should be skipped during repo indexing."""
    rel = rel_path.replace("\\", "/")
    # Check builtin patterns
    for pat in BUILTIN_IGNORE:
        if pat.endswith("/"):
            if rel.startswith(pat) or "/" + pat in rel:
                return True
        elif pat.startswith("*."):
            if rel.endswith(pat[1:]):
                return True
        elif rel == pat:
            return True
    # Check extension
    ext = Path(rel).suffix.lower()
    if ext and ext not in SOURCE_EXTENSIONS:
        # Allow files with no extension (Makefile, Dockerfile, etc.) if small
        if ext:  # unknown extension, skip
            return True
    # Check .gitignore
    spec = _load_gitignore(repo_root)
    if spec and spec.match_file(rel):
        return True
    return False


def iter_source_files(repo_root: Path, max_files: int = 500) -> Iterable[Path]:
    """Yield source file paths under repo_root that are not ignored."""
    count = 0
    root = Path(repo_root).resolve()
    for p in sorted(root.rglob("*")):
        if count >= max_files:
            break
        if not p.is_file():
            continue
        try:
            rel = str(p.relative_to(root))
        except ValueError:
            continue
        if is_ignored(rel, root):
            continue
        yield p
        count += 1



def iter_all_files(repo_root: Path, max_files: int = 1000):
    """Yield ALL non-ignored file paths (any extension).

    Ignores by directory rules (BUILTIN_IGNORE + .gitignore) but does NOT
    filter by SOURCE_EXTENSIONS.  Used for listing repo contents to the
    model, similar to ``rg --files`` output.
    """
    count = 0
    root = Path(repo_root).resolve()
    spec = _load_gitignore(root)
    for p in sorted(root.rglob("*")):
        if count >= max_files:
            break
        if not p.is_file():
            continue
        try:
            rel = str(p.relative_to(root)).replace("\\", "/")
        except ValueError:
            continue
        # Directory / builtin pattern ignores (skip common junk)
        skip = False
        for pat in BUILTIN_IGNORE:
            if pat.endswith("/"):
                if rel.startswith(pat) or "/" + pat in rel:
                    skip = True; break
            elif pat.startswith("*.") and rel.endswith(pat[1:]):
                skip = True; break
        if skip:
            continue
        if spec and spec.match_file(rel):
            continue
        yield p
        count += 1
