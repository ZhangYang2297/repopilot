import subprocess, sys
from pathlib import Path
def verify(repo: Path) -> bool:
    r = subprocess.run([sys.executable, "-m", "pytest", "test_users.py", "-v", "--tb=short"],
                       cwd=str(repo), capture_output=True, text=True, timeout=30)
    print(r.stdout[-800:])
    return r.returncode == 0 and "4 passed" in r.stdout
