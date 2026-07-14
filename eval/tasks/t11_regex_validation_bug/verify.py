import subprocess, sys
from pathlib import Path
def verify(repo: Path) -> bool:
    r = subprocess.run([sys.executable, "-m", "pytest", "test_validators.py", "-v", "--tb=short"],
                       cwd=str(repo), capture_output=True, text=True, timeout=30)
    print(r.stdout[-1000:])
    # Count passed tests - should have 11 tests
    return r.returncode == 0 and "passed" in r.stdout and "failed" not in r.stdout.split("passed")[0][-20:] if "passed" in r.stdout else False
