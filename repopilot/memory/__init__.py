"""Memory system — layered persistent memory (aligned with Claude Code / Codex).

Memory layers (from broadest to most specific):
  L1 Global:   ~/.repopilot/REPOPILOT.md     (user preferences, cross-project)
  L2 Project:  <repo_root>/REPOPILOT.md      (project conventions, build/test cmds)
  L2.5 Inherit: parent dirs up to repo root  (like .gitignore traversal)
  L3-L5 are session-internal (ContextManager handles compaction/recent/tool results)

Design principles:
  - Memory is PLAIN TEXT MARKDOWN — human-editable, git-committable.
  - Auto-discovered on startup (walk up from cwd like .gitignore).
  - Agent does NOT auto-write to memory files unless user explicitly asks.
  - Loaded memory_str is injected into system prompt L0.5 layer.
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional

MEMORY_FILENAME = "REPOPILOT.md"
GLOBAL_MEMORY_NAME = "REPOPILOT.md"  # in ~/.repopilot/


def _read_file_safe(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return ""


def discover_project_memory_files(repo_root: Path) -> list[Path]:
    """Walk UP from repo_root collecting REPOPILOT.md files.

    Like .gitignore: repo-root file applies to everything; subdirectory files
    are additive when working in that subtree.
    Returns paths from root outward (global first, then project root).
    """
    files = []
    root = Path(repo_root).resolve()
    # Check repo root
    candidate = root / MEMORY_FILENAME
    if candidate.exists():
        files.append(candidate)
    return files


def load_memory(
    repo_root: Path,
    home_dir: Optional[Path] = None,
    extra_memory_str: str = "",
) -> str:
    """Load and merge all memory layers into a single string for ContextManager.

    Returns empty string if no memory files exist.
    """
    sections: list[str] = []

    # 1. Global memory (~/.repopilot/REPOPILOT.md)
    if home_dir is not None:
        global_path = home_dir / GLOBAL_MEMORY_NAME
    else:
        global_path = Path.home() / ".repopilot" / GLOBAL_MEMORY_NAME
    global_text = _read_file_safe(global_path)
    if global_text:
        sections.append(f"# Global Memory (from ~/.repopilot/{GLOBAL_MEMORY_NAME})\n\n{global_text}")

    # 2. Project memory (./REPOPILOT.md)
    project_files = discover_project_memory_files(repo_root)
    for pf in project_files:
        proj_text = _read_file_safe(pf)
        if proj_text:
            rel = pf.relative_to(Path(repo_root).resolve()) if str(pf).startswith(str(Path(repo_root).resolve())) else pf
            sections.append(f"# Project Memory (from {rel})\n\n{proj_text}")

    # 3. Extra runtime memory (from /memory command or CLI flag)
    if extra_memory_str.strip():
        sections.append(f"# Session Notes\n\n{extra_memory_str.strip()}")

    if not sections:
        return ""

    header = (
        "The following memory files contain persistent instructions and context. "
        "Follow them unless the user explicitly overrides.\n\n"
    )
    return header + "\n\n---\n\n".join(sections)


def create_global_memory(home_dir: Path, content: str = "") -> Path:
    """Create the global REPOPILOT.md with a helpful template if it doesn't exist."""
    path = home_dir / GLOBAL_MEMORY_NAME
    if path.exists():
        return path
    template = """# RepoPilot Global Memory

This file contains your personal preferences that apply across all projects.
Edit this file directly, or use `/memory` in the REPL.

## Examples of what to put here:
- Preferred language for comments and docs
- Code style preferences (e.g., "use type hints always")
- Common commands you use frequently
- Personal conventions

## Current preferences
- Be concise in explanations
- Always run tests after making changes
"""
    path.write_text(content or template, encoding="utf-8")
    return path


def append_to_project_memory(repo_root: Path, note: str) -> Path:
    """Append a note to the project-level REPOPILOT.md (used by /memory --add)."""
    path = Path(repo_root).resolve() / MEMORY_FILENAME
    existing = _read_file_safe(path)
    if existing:
        new_content = existing.rstrip() + "\n\n" + note.strip() + "\n"
    else:
        new_content = "# RepoPilot Project Memory\n\n" + note.strip() + "\n"
    path.write_text(new_content, encoding="utf-8")
    return path


def append_to_global_memory(home_dir: Path, note: str) -> Path:
    """Append a note to the global REPOPILOT.md."""
    path = home_dir / GLOBAL_MEMORY_NAME
    create_global_memory(home_dir)
    existing = _read_file_safe(path)
    new_content = existing.rstrip() + "\n\n" + note.strip() + "\n"
    path.write_text(new_content, encoding="utf-8")
    return path
