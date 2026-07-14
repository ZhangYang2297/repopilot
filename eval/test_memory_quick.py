"""Quick memory test — runs key tests without the harness overhead."""
from __future__ import annotations
import os, sys, subprocess, tempfile, shutil, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

PYTHON = sys.executable
REPO_ROOT = Path(__file__).parent.parent

def run_repl(input_str, repo_dir, env=None, timeout=120):
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    try:
        r = subprocess.run(
            [PYTHON, "-c", "from repopilot.cli import app; app()", "--repo", str(repo_dir)],
            input=input_str, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace", env=full_env,
        )
        return r.stdout + r.stderr
    except subprocess.TimeoutExpired as e:
        return (e.stdout or "") + (e.stderr or "") + "\n[TIMEOUT]"


def make_repo():
    tmp = Path(tempfile.mkdtemp(prefix="memtest_"))
    (tmp / "main.py").write_text('def greet(name):\n    return f"Hello, {name}!"\n', encoding="utf-8")
    (tmp / "README.md").write_text("# Test\n", encoding="utf-8")
    # Set up isolated home by copying config
    return tmp


def setup_home(home: Path):
    real_home = Path(os.environ.get("REPOPILOT_HOME", str(Path.home() / ".repopilot")))
    home.mkdir(parents=True, exist_ok=True)
    real_config = real_home / "config.toml"
    if real_config.exists():
        shutil.copy2(real_config, home / "config.toml")
    else:
        sys.path.insert(0, str(REPO_ROOT))
        from repopilot.config import get_settings
        s = get_settings()
        lines = ["[core]"]
        if s.model: lines.append(f'model = "{s.model}"')
        if s.api_key: lines.append(f'api_key = "{s.api_key}"')
        if s.base_url: lines.append(f'base_url = "{s.base_url}"')
        (home / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


results = []

def test(name, fn):
    t0 = time.time()
    try:
        passed, notes, output = fn()
    except Exception as e:
        passed, notes, output = False, f"Exception: {e}", str(e)
    dt = time.time() - t0
    results.append((name, passed, notes, output, dt))
    icon = "PASS" if passed else "FAIL"
    print(f"  [{icon}] {name} ({dt:.1f}s)")
    if not passed:
        print(f"        Notes: {notes}")

# ── M1: In-session fact recall ──
def t_m1():
    repo = make_repo()
    try:
        setup_home(repo / ".home")
        out = run_repl('My favorite programming language is Rust\nWhat is my favorite language? Answer in one word.\n/exit\n', repo, env={"REPOPILOT_HOME": str(repo / ".home")}, timeout=90)
        return "Rust" in out and "[TIMEOUT]" not in out, "Should recall Rust", out[-500:]
    finally:
        shutil.rmtree(repo, ignore_errors=True)

# ── M2: Project memory injection ──
def t_m2():
    repo = make_repo()
    try:
        setup_home(repo / ".home")
        (repo / "REPOPILOT.md").write_text("# Rules\n- Always respond in Chinese\n", encoding="utf-8")
        out = run_repl('What does the greet function do? Answer briefly.\n/exit\n', repo, env={"REPOPILOT_HOME": str(repo / ".home")}, timeout=90)
        has_cn = any('\u4e00' <= c <= '\u9fff' for c in out)
        return has_cn, "Should respond in Chinese per project memory", out[-400:]
    finally:
        shutil.rmtree(repo, ignore_errors=True)

# ── M3: Global memory injection ──
def t_m3():
    repo = make_repo()
    try:
        home = repo / ".home"
        setup_home(home)
        (home / "REPOPILOT.md").write_text("# Global\n- Always end responses with: ===END===\n- Be extremely brief.\n", encoding="utf-8")
        out = run_repl('What is 2+2?\n/exit\n', repo, env={"REPOPILOT_HOME": str(home)}, timeout=90)
        return "===END===" in out and "wizard" not in out.lower(), "Should follow global memory end marker", out[-400:]
    finally:
        shutil.rmtree(repo, ignore_errors=True)

# ── M5: /clear isolation ──
def t_m5():
    repo = make_repo()
    try:
        setup_home(repo / ".home")
        out = run_repl('My API key is sk-abc123xyz\n/clear\nWhat is my API key? If unknown say I_DO_NOT_KNOW\n/exit\n', repo, env={"REPOPILOT_HOME": str(repo / ".home")}, timeout=90)
        after_clear = out.split("Conversation cleared")[-1] if "Conversation cleared" in out else out
        knows = "sk-abc123xyz" in after_clear and "I_DO_NOT_KNOW" not in after_clear
        return not knows, "Should forget API key after /clear", out[-500:]
    finally:
        shutil.rmtree(repo, ignore_errors=True)

# ── M6: /memory command persistence ──
def t_m6():
    repo = make_repo()
    try:
        setup_home(repo / ".home")
        out = run_repl('/memory Always use pytest -v for testing\n/exit\n', repo, env={"REPOPILOT_HOME": str(repo / ".home")}, timeout=60)
        pm = repo / "REPOPILOT.md"
        return pm.exists() and "pytest" in pm.read_text(encoding="utf-8"), "/memory should write REPOPILOT.md", out[-300:]
    finally:
        shutil.rmtree(repo, ignore_errors=True)

# ── M7: Compaction preserves facts ──
def t_m7():
    repo = make_repo()
    try:
        setup_home(repo / ".home")
        cmds = ["The secret code is XANADU99"]
        for _ in range(5):
            cmds.append("Read main.py")
        cmds.append("What is the secret code? Answer with just the code.")
        cmds.append("/exit")
        out = run_repl("\n".join(cmds) + "\n", repo, env={"REPOPILOT_HOME": str(repo / ".home")}, timeout=180)
        return "XANADU99" in out, "Should remember secret code after multiple reads", out[-500:]
    finally:
        shutil.rmtree(repo, ignore_errors=True)

# ── M8: Multi-turn accumulation ──
def t_m8():
    repo = make_repo()
    try:
        setup_home(repo / ".home")
        out = run_repl('Remember: my cat is named Whiskers and I work at Initech\nWhat is my cats name and where do I work? Answer briefly.\n/exit\n', repo, env={"REPOPILOT_HOME": str(repo / ".home")}, timeout=90)
        return "Whiskers" in out and "Initech" in out, "Should remember both facts across turns", out[-500:]
    finally:
        shutil.rmtree(repo, ignore_errors=True)

# ── M9: E2E full workflow (memory -> exit -> new session respects it) ──
def t_m9():
    repo = make_repo()
    try:
        home = repo / ".home"
        setup_home(home)
        out1 = run_repl('/memory Always answer in exactly one sentence\n/exit\n', repo, env={"REPOPILOT_HOME": str(home)}, timeout=60)
        time.sleep(0.5)
        out2 = run_repl('What is Python?\n/exit\n', repo, env={"REPOPILOT_HOME": str(home)}, timeout=90)
        # The response should be short (one sentence) because project memory says so
        # Look for evidence the response is concise (<=2 sentences, no bullet points)
        resp_part = out2.split("repopilot:")[-1].split("repopilot:")[0] if "repopilot:" in out2 else out2
        has_long = len([l for l in resp_part.split(".") if len(l.strip()) > 80]) > 2
        mentions_memory = any(kw in out2.lower() for kw in ["one sentence", "concise", "brief"])
        return not has_long or mentions_memory or "REPOPILOT.md" in out2 or "one sentence" in out2, "Should follow project memory in new session", out2[-600:]
    finally:
        shutil.rmtree(repo, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 65)
    print("RepoPilot Memory System Evaluation")
    print("=" * 65)
    print()

    test("M1 In-session fact recall", t_m1)
    test("M2 Project memory (REPOPILOT.md)", t_m2)
    test("M3 Global memory (~/.repopilot)", t_m3)
    test("M5 Session isolation (/clear)", t_m5)
    test("M6 /memory command persistence", t_m6)
    test("M7 Compaction preserves facts", t_m7)
    test("M8 Multi-turn accumulation", t_m8)
    test("M9 E2E memory across sessions", t_m9)

    print()
    print("=" * 65)
    passed = sum(1 for _, p, _, _, _ in results if p)
    total = len(results)
    print(f"Results: {passed}/{total} passed ({passed/total*100:.0f}%)")
    for name, p, notes, _, dt in results:
        icon = "PASS" if p else "FAIL"
        print(f"  [{icon}] {name} ({dt:.1f}s)")
        if not p:
            print(f"        {notes}")

    import json
    out_json = {
        "summary": {"passed": passed, "total": total, "rate": passed/total},
        "tests": [{"name": n, "passed": p, "notes": nt, "time_s": round(dt,1)} for n,p,nt,_,dt in results],
    }
    Path("eval/memory_results.json").write_text(json.dumps(out_json, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved to eval/memory_results.json")
