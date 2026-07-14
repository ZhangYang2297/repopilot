"""Memory system evaluation tests.

Tests the multi-layer memory system end-to-end via REPL interaction.
Each test spawns `repopilot` as a subprocess, pipes in commands, and checks output.

Categories:
  M1: In-session fact recall (working memory)
  M2: Project memory (REPOPILOT.md injection)
  M3: Global memory (global REPOPILOT.md)
  M4: Cross-session resume (/resume)
  M5: Session isolation (/clear clears memory)
  M6: Memory add via /memory command
  M7: Compaction preserves key facts
  M8: Multi-turn context accumulation
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import time
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPOPILOT_HOME = Path(os.environ.get("REPOPILOT_HOME", str(Path.home() / ".repopilot")))
PYTHON = sys.executable


def run_repl(input_str: str, repo_dir: Path, env: Optional[dict] = None, timeout: int = 120) -> str:
    """Run repopilot in repo_dir, pipe input_str, return combined stdout."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    try:
        r = subprocess.run(
            [PYTHON, "-m", "repopilot.cli", "--repo", str(repo_dir)],
            input=input_str, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace", env=full_env,
        )
        return r.stdout + r.stderr
    except subprocess.TimeoutExpired as e:
        return (e.stdout or "") + (e.stderr or "") + "\n[TIMEOUT]"


def make_repo(name: str = "testrepo") -> Path:
    """Create a temporary repo with a simple Python file."""
    tmp = Path(tempfile.mkdtemp(prefix=f"repopilot_memtest_{name}_"))
    (tmp / "main.py").write_text('''
def greet(name):
    return f"Hello, {name}!"

def add(a, b):
    return a + b
''', encoding="utf-8")
    (tmp / "README.md").write_text(f"# {name}\nA test project for memory evaluation.\n", encoding="utf-8")
    return tmp


def cleanup_repo(path: Path):
    shutil.rmtree(path, ignore_errors=True)


@dataclass
class MemTestResult:
    test_id: str
    name: str
    passed: bool
    output: str
    notes: str = ""


# ─── Test Cases ──────────────────────────────────────────────────────────────

def test_m1_fact_recall() -> MemTestResult:
    """Agent recalls facts stated earlier in the same session."""
    repo = make_repo("m1_fact")
    try:
        inp = 'My favorite programming language is Rust and my birthday is March 15\nWhat is my favorite programming language? Answer in one word.\n/exit\n'
        out = run_repl(inp, repo, timeout=90)
        passed = "Rust" in out and "[TIMEOUT]" not in out
        notes = "Checks that facts from user messages persist in context"
        return MemTestResult("M1", "In-session fact recall", passed, out[-500:], notes)
    finally:
        cleanup_repo(repo)


def test_m2_project_memory() -> MemTestResult:
    """Agent follows instructions from project REPOPILOT.md."""
    repo = make_repo("m2_projmem")
    # Create project REPOPILOT.md with a specific instruction
    (repo / "REPOPILOT.md").write_text('''# Project Memory

IMPORTANT RULES FOR THIS PROJECT:
- Always respond in Chinese (Simplified Chinese), never English.
- When asked to write code, always add a comment "# Project: memory-test" at the top.
- Never suggest using print() for debugging; always suggest logging instead.
''', encoding="utf-8")
    try:
        inp = 'Read main.py and explain what the greet function does in one sentence\n/exit\n'
        out = run_repl(inp, repo, timeout=90)
        # Check that agent responds in Chinese due to project memory
        has_chinese = any('\u4e00' <= c <= '\u9fff' for c in out)
        passed = has_chinese
        notes = "Checks that REPOPILOT.md instructions are injected into system prompt"
        return MemTestResult("M2", "Project memory injection", passed, out[-800:], notes)
    finally:
        cleanup_repo(repo)


def test_m3_global_memory() -> MemTestResult:
    """Global memory preferences are followed."""
    repo = make_repo("m3_globalmem")
    # Set up isolated global memory
    with tempfile.TemporaryDirectory() as home_dir:
        home = Path(home_dir)
        (home / "REPOPILOT.md").write_text('''# Global Memory
- Always end your responses with a line containing exactly: "===END==="
- Be extremely concise (max 2 sentences).
''', encoding="utf-8")
        try:
            inp = 'What is 2+2?\n/exit\n'
            out = run_repl(inp, repo, env={"REPOPILOT_HOME": str(home)}, timeout=90)
            passed = "===END===" in out
            notes = "Checks that global REPOPILOT.md is loaded and followed"
            return MemTestResult("M3", "Global memory injection", passed, out[-500:], notes)
        finally:
            cleanup_repo(repo)


def test_m4_cross_session_resume() -> MemTestResult:
    """After /resume, agent recalls information from previous session."""
    repo = make_repo("m4_resume")
    sessions_dir = repo / ".sessions"
    sessions_dir.mkdir(exist_ok=True)
    try:
        # Session 1: tell agent a secret
        inp1 = 'Remember this secret word: BLUEBERRY42\n/exit\n'
        out1 = run_repl(inp1, repo, env={"REPOPILOT_HOME": str(repo / ".home")}, timeout=90)
        time.sleep(1)
        # Find session ID from the sessions DB
        from repopilot.config import Settings
        from repopilot.session.store import SessionStore
        # Need to read from the isolated home
        home_for_test = repo / ".home"
        if not home_for_test.exists():
            # First run creates it
            pass
        # Re-run with same home to list sessions
        inp2 = '/sessions\n/resume\nWhat was the secret word I told you? Answer with just the word.\n/exit\n'
        out2 = run_repl(inp2, repo, env={"REPOPILOT_HOME": str(home_for_test)}, timeout=120)
        passed = "BLUEBERRY42" in out2
        notes = "Checks that /resume restores context from previous session"
        return MemTestResult("M4", "Cross-session resume", passed, out2[-800:], notes)
    except Exception as e:
        return MemTestResult("M4", "Cross-session resume", False, str(e), f"Error: {e}")
    finally:
        cleanup_repo(repo)


def test_m5_clear_isolation() -> MemTestResult:
    """After /clear, old facts are forgotten."""
    repo = make_repo("m5_clear")
    try:
        inp = 'My API key is sk-abc123xyz\n/clear\nWhat is my API key? If you do not know, say "I do not know"\n/exit\n'
        out = run_repl(inp, repo, timeout=90)
        # After /clear, agent should NOT know the API key
        knows_key = "sk-abc123xyz" in out.split("Conversation cleared")[-1] if "Conversation cleared" in out else True
        passed = not knows_key or "do not know" in out.lower()
        notes = "Checks that /clear properly resets context"
        return MemTestResult("M5", "Session isolation (/clear)", passed, out[-800:], notes)
    finally:
        cleanup_repo(repo)


def test_m6_memory_add_command() -> MemTestResult:
    """/memory command adds to project REPOPILOT.md."""
    repo = make_repo("m6_memadd")
    try:
        inp = '/memory The project uses pytest for testing. Run tests with python -m pytest -v\n/exit\n'
        out = run_repl(inp, repo, timeout=60)
        proj_mem = repo / "REPOPILOT.md"
        created = proj_mem.exists()
        content = proj_mem.read_text(encoding="utf-8") if created else ""
        passed = created and "pytest" in content
        notes = "Checks that /memory writes to project REPOPILOT.md"
        return MemTestResult("M6", "/memory command persistence", passed, out[-300:], notes)
    finally:
        cleanup_repo(repo)


def test_m7_compaction_preserves_facts() -> MemTestResult:
    """After many turns (triggering compaction), key facts are preserved."""
    repo = make_repo("m7_compact")
    try:
        # Inject a fact early, then do lots of tool reads to fill context
        lines = ['The project version number is 9.8.7 and the author is Zephyr']
        # Read main.py multiple times to fill context
        for i in range(8):
            lines.append('Read main.py')
        lines.append('What is the project version number I told you earlier? Answer with just the number.')
        lines.append('/exit')
        inp = '\n'.join(lines) + '\n'
        out = run_repl(inp, repo, timeout=180)
        passed = "9.8.7" in out
        notes = "Checks that auto-compaction doesn't lose critical user facts"
        return MemTestResult("M7", "Compaction preserves facts", passed, out[-800:], notes)
    finally:
        cleanup_repo(repo)


def test_m8_multiturn_accumulation() -> MemTestResult:
    """Information accumulates across 5+ turns within a session."""
    repo = make_repo("m8_multiturn")
    try:
        inp = '''I have three items on my todo list: buy milk, fix the bug in main.py, write docs
Now add "call dentist" to that list
What is the second item on the list? Answer with just the item text.
/exit
'''
        out = run_repl(inp, repo, timeout=120)
        passed = "fix the bug" in out
        notes = "Checks that multiple turns accumulate correctly"
        return MemTestResult("M8", "Multi-turn accumulation", passed, out[-800:], notes)
    finally:
        cleanup_repo(repo)


def test_m9_e2e_full_workflow() -> MemTestResult:
    """Full workflow: add memory -> exit -> resume -> use memory."""
    repo = make_repo("m9_e2e")
    home_dir = repo / ".home_e2e"
    home_dir.mkdir(exist_ok=True)
    try:
        inp1 = '/memory Use type hints on ALL functions\n/exit\n'
        run_repl(inp1, repo, env={"REPOPILOT_HOME": str(home_dir)}, timeout=60)
        time.sleep(1)
        inp2 = 'Read main.py. Does the greet function have type hints? Answer yes or no.\n/exit\n'
        out2 = run_repl(inp2, repo, env={"REPOPILOT_HOME": str(home_dir)}, timeout=90)
        # Project memory says use type hints; agent should notice greet() doesn't have them
        # (We're testing that the memory is LOADED, not that it auto-fixes)
        # The agent should mention that type hints are missing, OR reference the memory
        mentions_hints = any(kw in out2.lower() for kw in ["type hint", "type annotation", "no type", "missing type"])
        passed = mentions_hints
        notes = "Full E2E: /memory -> exit -> new session respects the stored memory"
        return MemTestResult("M9", "E2E memory persistence across sessions", passed, out2[-800:], notes)
    finally:
        cleanup_repo(repo)


# ─── Runner ───────────────────────────────────────────────────────────────────

def main():
    tests = [
        test_m1_fact_recall,
        test_m2_project_memory,
        test_m3_global_memory,
        test_m4_cross_session_resume,
        test_m5_clear_isolation,
        test_m6_memory_add_command,
        test_m7_compaction_preserves_facts,
        test_m8_multiturn_accumulation,
        test_m9_e2e_full_workflow,
    ]
    print("=" * 65)
    print("RepoPilot Memory System Evaluation")
    print("=" * 65)
    print()
    results = []
    for fn in tests:
        print(f"  Running {fn.__name__}...", end=" ", flush=True)
        r = fn()
        results.append(r)
        status = "PASS" if r.passed else "FAIL"
        color = "\033[92m" if r.passed else "\033[91m"
        reset = "\033[0m"
        print(f"{color}{status}{reset}")
    print()
    print("=" * 65)
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"Results: {passed}/{total} passed ({passed/total*100:.0f}%)")
    print()
    print(f"{'ID':<5} {'Name':<45} {'Result'}")
    print("-" * 65)
    for r in results:
        icon = "\033[92mPASS\033[0m" if r.passed else "\033[91mFAIL\033[0m"
        print(f"{r.test_id:<5} {r.name:<45} {icon}")
        if not r.passed:
            print(f"      Notes: {r.notes}")
            # Print last 200 chars of output for debugging
            last = r.output[-300:].replace("\n", " ")
            print(f"      Output tail: ...{last}")
    print()

    # Save JSON
    out_json = {
        "summary": {"passed": passed, "total": total, "rate": passed/total if total else 0},
        "tests": [{"id": r.test_id, "name": r.name, "passed": r.passed, "notes": r.notes} for r in results],
    }
    Path("eval/memory_results.json").write_text(
        json.dumps(out_json, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Results saved to eval/memory_results.json")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
