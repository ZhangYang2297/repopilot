"""Setup: create a simple math module with a bug in the add function."""
from pathlib import Path
def setup(repo: Path):
    (repo / "mathlib.py").write_text('''
def add(a, b):
    return a - b  # BUG: should be a + b

def subtract(a, b):
    return a - b

def multiply(a, b):
    return a * b
''', encoding="utf-8")
    (repo / "test_math.py").write_text('''
from mathlib import add, subtract, multiply

def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0
    assert add(0, 0) == 0

def test_subtract():
    assert subtract(5, 3) == 2

def test_multiply():
    assert multiply(4, 3) == 12
''', encoding="utf-8")
