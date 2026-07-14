import subprocess, sys
from pathlib import Path
def verify(repo: Path) -> bool:
    r = subprocess.run([sys.executable, "-m", "pytest", "test_todo_cli.py", "-v", "--tb=short"],
                       cwd=str(repo), capture_output=True, text=True, timeout=30)
    print(r.stdout[-800:])
    # Clean up any todos.json created during test
    todos = repo / "todos.json"
    if todos.exists():
        todos.unlink()
    return r.returncode == 0 and "3 passed" in r.stdout
