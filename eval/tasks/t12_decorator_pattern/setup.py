"""Setup: code with repeated timing logic that should be refactored into a decorator."""
from pathlib import Path
def setup(repo: Path):
    (repo / "timing.py").write_text('''
import time

# BUG: These functions all repeat the same timing boilerplate.
# The agent should refactor using a decorator pattern.

def slow_function(n):
    start = time.time()
    total = 0
    for i in range(n):
        total += i
    elapsed = time.time() - start
    print(f"slow_function took {elapsed:.4f}s")
    return total

def fibonacci(n):
    start = time.time()
    if n <= 1:
        result = n
    else:
        a, b = 0, 1
        for _ in range(n - 1):
            a, b = b, a + b
        result = b
    elapsed = time.time() - start
    print(f"fibonacci took {elapsed:.4f}s")
    return result

def sleep_and_return(value, seconds=0.01):
    start = time.time()
    time.sleep(seconds)
    result = value
    elapsed = time.time() - start
    print(f"sleep_and_return took {elapsed:.4f}s")
    return result
''', encoding="utf-8")
    (repo / "test_timing.py").write_text('''
import time, sys, os
sys.path.insert(0, ".")

def test_timing_decorator_exists():
    """timing.py must define a 'timed' decorator."""
    import timing
    assert hasattr(timing, "timed"), "Must define @timed decorator"

def test_fibonacci_correct():
    import timing
    # Reload module in case it was cached
    import importlib; importlib.reload(timing)
    assert timing.fibonacci(10) == 55
    assert timing.fibonacci(1) == 1
    assert timing.fibonacci(0) == 0

def test_slow_function_correct():
    import timing
    import importlib; importlib.reload(timing)
    assert timing.slow_function(100) == sum(range(100))

def test_sleep_and_return_correct():
    import timing
    import importlib; importlib.reload(timing)
    assert timing.sleep_and_return(42, 0.01) == 42
''', encoding="utf-8")
