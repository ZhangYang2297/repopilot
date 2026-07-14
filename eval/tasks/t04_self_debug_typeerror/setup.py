"""Setup: a calculator module with TypeError bugs that produce runtime errors."""
from pathlib import Path
def setup(repo: Path):
    (repo / "calculator.py").write_text('''
def divide(a, b):
    """Divide a by b."""
    return a / b  # no zero division check

def average(numbers):
    """Return average of a list of numbers."""
    total = 0
    for n in numbers:
        total + n  # BUG: should be total += n (assignment missing)
    return total / len(numbers)

def percentage(part, whole):
    """Return part/whole * 100."""
    return part / whole * 100

def format_result(value, decimals=2):
    return round(value, decimals).toString()  # BUG: Python uses str() not .toString()
''', encoding="utf-8")
    (repo / "test_calculator.py").write_text('''
import pytest
from calculator import divide, average, percentage, format_result

def test_divide():
    assert divide(10, 2) == 5.0

def test_divide_by_zero():
    with pytest.raises(ZeroDivisionError):
        divide(1, 0)

def test_average():
    assert average([1, 2, 3, 4, 5]) == 3.0

def test_average_single():
    assert average([42]) == 42.0

def test_percentage():
    assert percentage(25, 100) == 25.0

def test_format_result():
    assert "3.14" in format_result(3.14159, 2)
''', encoding="utf-8")
