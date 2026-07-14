"""Setup: a data processing module that needs type hints and docstrings added, plus a new function."""
from pathlib import Path
def setup(repo: Path):
    (repo / "data_proc.py").write_text('''
import csv
import statistics

def read_csv(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows

def column_values(rows, column):
    return [r[column] for r in rows if column in r]

def to_numeric(values):
    result = []
    for v in values:
        try:
            result.append(float(v))
        except (ValueError, TypeError):
            pass
    return result

def column_stats(rows, column):
    vals = to_numeric(column_values(rows, column))
    if not vals:
        return None
    return {
        "count": len(vals),
        "mean": statistics.mean(vals),
        "min": min(vals),
        "max": max(vals),
        "stdev": statistics.stdev(vals) if len(vals) > 1 else 0.0,
    }
''', encoding="utf-8")
    (repo / "test_data_proc.py").write_text('''
import pytest, tempfile, os
from data_proc import read_csv, column_values, to_numeric, column_stats, group_by

def test_read_csv():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
        f.write("name,age,city\\n")
        f.write("Alice,30,NYC\\n")
        f.write("Bob,25,LA\\n")
        path = f.name
    try:
        rows = read_csv(path)
        assert len(rows) == 2
        assert rows[0]["name"] == "Alice"
    finally:
        os.unlink(path)

def test_column_values():
    rows = [{"a": "1", "b": "x"}, {"a": "2", "b": "y"}]
    assert column_values(rows, "a") == ["1", "2"]
    assert column_values(rows, "missing") == []

def test_to_numeric():
    assert to_numeric(["1", "2.5", "abc", "3"]) == [1.0, 2.5, 3.0]
    assert to_numeric([]) == []

def test_column_stats():
    rows = [{"val": "10"}, {"val": "20"}, {"val": "30"}]
    stats = column_stats(rows, "val")
    assert stats is not None
    assert stats["count"] == 3
    assert stats["mean"] == 20.0
    assert stats["min"] == 10.0
    assert stats["max"] == 30.0

def test_column_stats_empty():
    assert column_stats([], "val") is None

def test_group_by():
    rows = [
        {"city": "NYC", "name": "Alice"},
        {"city": "LA", "name": "Bob"},
        {"city": "NYC", "name": "Charlie"},
    ]
    groups = group_by(rows, "city")
    assert "NYC" in groups
    assert len(groups["NYC"]) == 2
    assert len(groups["LA"]) == 1
''', encoding="utf-8")
