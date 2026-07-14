"""Verify: run pytest and check all tests pass."""
import subprocess, sys
from pathlib import Path
def verify(repo: Path) -> bool:
    r = subprocess.run([sys.executable, "-m", "pytest", "test_math.py", "-v", "--tb=short"],
                       cwd=str(repo), capture_output=True, text=True, timeout=30)
    print(r.stdout[-500:])
    if r.stderr:
        print(r.stderr[-300:])
    return r.returncode == 0 and "3 passed" in r.stdout
