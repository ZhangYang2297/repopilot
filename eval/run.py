"""RepoPilot Evaluation Harness.

Runs a suite of tasks through the agent, measures success rate, token usage,
tool calls, and error recovery capability.

Usage:
    python -m eval.run [--tasks 1-12] [--max-steps 80] [--output eval/results.json]

Each task directory under eval/tasks/ contains:
    setup.py   - setup(repo_path) creates initial buggy code
    task.md    - natural language task description for the agent
    verify.py  - verify(repo_path) returns True if task completed correctly
    teardown.py - optional teardown(repo_path) cleanup
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import shutil
import sys
import time
import tempfile
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

EVAL_DIR = Path(__file__).parent
TASKS_DIR = EVAL_DIR / "tasks"


@dataclass
class TaskResult:
    task_id: str
    task_name: str
    difficulty: str
    category: str
    success: bool = False
    steps_used: int = 0
    tokens_used: int = 0
    total_time_s: float = 0.0
    llm_calls: int = 0
    tool_calls: int = 0
    error: str = ""
    trajectory: list[dict] = field(default_factory=list)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _get_task_info(task_dir: Path) -> dict:
    """Parse task directory name for metadata."""
    name = task_dir.name
    # Format: tNN_category_name
    parts = name.split("_", 2)
    tid = parts[0] if len(parts) > 0 else name
    # Difficulty heuristic based on task number
    num = int(tid[1:]) if tid.startswith("t") and tid[1:].isdigit() else 0
    if num <= 3:
        diff = "easy"
    elif num <= 6:
        diff = "medium"
    elif num <= 9:
        diff = "hard"
    else:
        diff = "hard"
    # Category
    categories = {
        "01": "bugfix", "02": "bugfix", "04": "debug",
        "03": "new_feature", "08": "new_feature", "10": "new_feature",
        "05": "multifile", "09": "import_fix",
        "06": "security", "07": "edge_cases",
        "11": "regex", "12": "refactor",
    }
    cat = categories.get(tid[1:] if len(tid) >= 3 else tid, "general")
    return {"id": tid, "name": name, "difficulty": diff, "category": cat}


def run_task(task_dir: Path, max_steps: int = 80, verbose: bool = False) -> TaskResult:
    """Run a single task through the agent and return the result."""
    info = _get_task_info(task_dir)
    result = TaskResult(
        task_id=info["id"],
        task_name=info["name"],
        difficulty=info["difficulty"],
        category=info["category"],
    )

    # Create temporary working directory for the task
    tmpdir = Path(tempfile.mkdtemp(prefix=f"repopilot_eval_{info['id']}_"))
    repo = tmpdir / "repo"
    repo.mkdir(parents=True, exist_ok=True)

    try:
        # Run setup
        setup_path = task_dir / "setup.py"
        if setup_path.exists():
            setup_mod = _load_module(f"setup_{info['id']}", setup_path)
            setup_mod.setup(repo)

        # Load task description
        task_text = (task_dir / "task.md").read_text(encoding="utf-8").strip()

        if verbose:
            print(f"\n{'='*60}")
            print(f"Task {info['id']}: {info['name']} ({info['difficulty']}/{info['category']})")
            print(f"{'='*60}")
            print(f"Task: {task_text[:100]}...")
            print(f"Repo: {repo}")

        # Run agent
        t0 = time.time()
        steps = 0
        tokens = 0
        llm_calls = 0
        tool_call_count = 0
        trajectory = []
        success = False
        error_msg = ""

        try:
            # Import RepoPilot internals
            from repopilot.config import get_settings, reset_settings_for_tests, Settings
            from repopilot.llm.service import build_llm_from_settings
            from repopilot.sandbox import LocalSandbox
            from repopilot.agent.loop import run_agent
            from repopilot.session.store import SessionStore

            reset_settings_for_tests()
            settings = get_settings()
            settings.max_steps = max_steps
            settings.budget_tokens = 400_000
            settings.approval_mode = "auto"
            settings.sandbox_type = "local"

            llm = build_llm_from_settings(settings)
            session_store = SessionStore(sessions_dir=settings.sessions_dir)
            session = session_store.create(title=f"eval-{info['id']}", cwd=str(repo), model=settings.model)

            sb = LocalSandbox(repo)
            agent_result = run_agent(
                task=task_text,
                repo_path=repo,
                llm=llm,
                sandbox=sb,
                permission_engine=None,  # auto mode via default
                session_store=session_store,
                max_steps=max_steps,
                budget_tokens=400_000,
                verbose=verbose,
            )

            steps = agent_result.steps
            tokens = agent_result.total_tokens
            llm_calls = steps
            tool_call_count = len(agent_result.trajectory) if agent_result.trajectory else steps
            trajectory = agent_result.trajectory or []

            if verbose:
                print(f"\nAgent finished: status={agent_result.status}, steps={steps}")

        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            if verbose:
                print(f"\nAgent error: {error_msg}")
                traceback.print_exc()

        result.steps_used = steps
        result.tokens_used = tokens
        result.llm_calls = llm_calls
        result.tool_calls = tool_call_count
        result.total_time_s = time.time() - t0
        result.error = error_msg
        result.trajectory = trajectory[:20]  # truncate for storage

        # Run verification
        verify_path = task_dir / "verify.py"
        if verify_path.exists():
            try:
                verify_mod = _load_module(f"verify_{info['id']}", verify_path)
                success = verify_mod.verify(repo)
                if verbose:
                    print(f"Verification: {'PASS' if success else 'FAIL'}")
            except Exception as e:
                success = False
                result.error = f"verify error: {type(e).__name__}: {e}"
                if verbose:
                    print(f"Verification error: {e}")
                    traceback.print_exc()

        result.success = success

    finally:
        # Teardown
        try:
            teardown_path = task_dir / "setup.py"
            if teardown_path.exists():
                setup_mod = _load_module(f"setup_td_{info['id']}", teardown_path)
                if hasattr(setup_mod, "teardown"):
                    setup_mod.teardown(repo)
        except Exception:
            pass
        # Clean up temp dir
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass

    return result


def main():
    parser = argparse.ArgumentParser(description="RepoPilot Evaluation Harness")
    parser.add_argument("--tasks", default="1-12", help="Task range, e.g. '1-5' or '1,3,5'")
    parser.add_argument("--max-steps", type=int, default=80, help="Max agent steps per task")
    parser.add_argument("--output", default=str(EVAL_DIR / "results.json"), help="Output JSON path")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--category", default="", help="Filter by category (bugfix, new_feature, etc.)")
    args = parser.parse_args()

    # Parse task selection
    selected = set()
    for part in args.tasks.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            selected.update(range(int(lo), int(hi) + 1))
        else:
            selected.add(int(part))

    # Discover tasks
    task_dirs = sorted(TASKS_DIR.iterdir())
    tasks_to_run = []
    for td in task_dirs:
        if not td.is_dir():
            continue
        info = _get_task_info(td)
        num = int(info["id"][1:]) if info["id"][1:].isdigit() else 0
        if num not in selected:
            continue
        if args.category and info["category"] != args.category:
            continue
        tasks_to_run.append(td)

    print(f"RepoPilot Evaluation Harness")
    print(f"Tasks: {len(tasks_to_run)} | Max steps: {args.max_steps}")
    print(f"{'='*60}")

    results = []
    for td in tasks_to_run:
        r = run_task(td, max_steps=args.max_steps, verbose=args.verbose)
        results.append(r)
        status_icon = "PASS" if r.success else "FAIL"
        color = "\033[92m" if r.success else "\033[91m"
        reset = "\033[0m"
        print(f"  [{color}{status_icon}{reset}] {r.task_id} {r.task_name}  "
              f"({r.difficulty}/{r.category})  steps={r.steps_used}  "
              f"tokens={r.tokens_used:,}  time={r.total_time_s:.1f}s")

    # Summary
    print(f"\n{'='*60}")
    passed = sum(1 for r in results if r.success)
    total = len(results)
    total_tokens = sum(r.tokens_used for r in results)
    total_time = sum(r.total_time_s for r in results)
    total_steps = sum(r.steps_used for r in results)
    print(f"Results: {passed}/{total} passed ({passed/total*100:.0f}% success rate)")
    print(f"Total tokens: {total_tokens:,}")
    print(f"Total steps: {total_steps}")
    print(f"Total time: {total_time:.1f}s")

    # Per-category breakdown
    cats = {}
    for r in results:
        cats.setdefault(r.category, {"pass": 0, "total": 0})
        cats[r.category]["total"] += 1
        if r.success:
            cats[r.category]["pass"] += 1
    if cats:
        print(f"\nBy category:")
        for cat, c in sorted(cats.items()):
            print(f"  {cat:15s}: {c['pass']}/{c['total']} ({c['pass']/c['total']*100:.0f}%)")

    # Per-difficulty breakdown
    diffs = {}
    for r in results:
        diffs.setdefault(r.difficulty, {"pass": 0, "total": 0})
        diffs[r.difficulty]["total"] += 1
        if r.success:
            diffs[r.difficulty]["pass"] += 1
    if diffs:
        print(f"\nBy difficulty:")
        for d in ["easy", "medium", "hard"]:
            if d in diffs:
                c = diffs[d]
                print(f"  {d:10s}: {c['pass']}/{c['total']} ({c['pass']/c['total']*100:.0f}%)")

    # Save results
    output = {
        "summary": {
            "total_tasks": total,
            "passed": passed,
            "success_rate": passed / total if total else 0,
            "total_tokens": total_tokens,
            "total_steps": total_steps,
            "total_time_s": total_time,
            "by_category": {k: {"pass": v["pass"], "total": v["total"]} for k, v in cats.items()},
            "by_difficulty": {k: {"pass": v["pass"], "total": v["total"]} for k, v in diffs.items()},
        },
        "tasks": [asdict(r) for r in results],
    }
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Remove non-serializable trajectory items
    for t in output["tasks"]:
        t["trajectory"] = []
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\nResults saved to {out_path}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
