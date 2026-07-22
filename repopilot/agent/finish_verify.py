"""Completion verification gate (Phase 2B-1 Task 4).

When the model calls the ``finish`` tool we run a *quick* reality check on
files changed during this turn, so it cannot silently declare a broken
task done.

Design:
  * Pure function ``run_verify(changed_files, sandbox) -> VerifyResult``.
  * No LLM calls, no user input.
  * Bounded time: each check has an individual timeout (5s), aggregate
    cap 20s.  Anything slower degrades to a warning, not a block.
  * Only opt-in blocking for the two cheapest, highest-signal checks:
      - ``.py`` files → ``python -m py_compile`` per file
      - ``.json`` files → ``json.load`` per file
    Everything else is skipped (leave heavier test-runs to Phase 2B-3
    "auto test selection").

Return value:
  * ``passed=True, blocked=False, notes=[...]`` — verify OK / skipped
  * ``passed=False, blocked=True, notes=[...]`` — one or more hard fails,
    caller MUST re-inject the notes into the LLM context and refuse to
    finish this turn.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from repopilot.sandbox.base import Sandbox


VERIFY_STEP_TIMEOUT_S = 5
VERIFY_TOTAL_TIMEOUT_S = 20


@dataclass
class VerifyResult:
    passed: bool = True
    blocked: bool = False
    notes: list[str] = field(default_factory=list)
    checked_files: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.blocked:
            return "[verify] failed:\n" + "\n".join(f"  - {n}" for n in self.notes)
        if not self.checked_files:
            return "[verify] skipped (no verifiable files changed)"
        return f"[verify] passed ({len(self.checked_files)} files: " \
               f"{', '.join(self.checked_files[:5])}" \
               f"{'…' if len(self.checked_files) > 5 else ''})"


def run_verify(
    changed_files: Iterable[str],
    sandbox: "Sandbox",
    *,
    step_timeout_s: int = VERIFY_STEP_TIMEOUT_S,
    total_timeout_s: int = VERIFY_TOTAL_TIMEOUT_S,
) -> VerifyResult:
    """Run cheap syntax checks on changed .py / .json files.

    Only files that still exist under ``sandbox.repo_path`` are checked;
    deleted files are skipped.
    """
    result = VerifyResult()
    repo = Path(getattr(sandbox, "repo_path", "."))
    started = time.perf_counter()

    py_files: list[str] = []
    json_files: list[str] = []
    for rel in changed_files:
        p = repo / rel
        if not p.is_file():
            continue
        low = rel.lower()
        if low.endswith(".py"):
            py_files.append(rel)
        elif low.endswith(".json"):
            json_files.append(rel)

    if not py_files and not json_files:
        return result  # skip cleanly

    # ── .py: py_compile ─────────────────────────────────────────────
    for rel in py_files:
        if time.perf_counter() - started > total_timeout_s:
            result.notes.append(f"[verify] total budget exceeded before {rel}; skipping remainder")
            break
        try:
            proc = subprocess.run(
                ["python", "-m", "py_compile", str((repo / rel).resolve())],
                capture_output=True, text=True, timeout=step_timeout_s,
            )
        except subprocess.TimeoutExpired:
            result.notes.append(f"{rel}: py_compile timed out (>{step_timeout_s}s) — treated as warning")
            continue
        except Exception as e:
            result.notes.append(f"{rel}: py_compile could not run ({e}) — treated as warning")
            continue
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or "").strip().replace("\r", "")
            # keep first 3 lines of traceback
            head = "\n    ".join(msg.splitlines()[:3])
            result.notes.append(f"{rel}: py_compile FAILED\n    {head}")
            result.blocked = True
            result.passed = False
        else:
            result.checked_files.append(rel)

    # ── .json: json.load ───────────────────────────────────────────
    for rel in json_files:
        if time.perf_counter() - started > total_timeout_s:
            result.notes.append(f"[verify] total budget exceeded before {rel}; skipping remainder")
            break
        try:
            with open(repo / rel, "r", encoding="utf-8") as f:
                json.load(f)
        except json.JSONDecodeError as e:
            result.notes.append(f"{rel}: JSON invalid at line {e.lineno} col {e.colno}: {e.msg}")
            result.blocked = True
            result.passed = False
        except Exception as e:
            result.notes.append(f"{rel}: could not read ({e}) — treated as warning")
            continue
        else:
            result.checked_files.append(rel)

    if result.blocked:
        result.notes.insert(0, "Fix these before calling finish again.")

    return result
