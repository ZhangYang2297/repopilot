"""Setup: create a large generated file and a log parser that must handle it."""
from pathlib import Path
def setup(repo: Path):
    # Generate a 500-line log file
    lines = []
    for i in range(1, 501):
        if i % 10 == 0:
            lines.append(f"2024-01-{i%28+1:02d} 12:00:{i%60:02d} ERROR: Connection timeout on attempt {i}")
        elif i % 5 == 0:
            lines.append(f"2024-01-{i%28+1:02d} 12:00:{i%60:02d} WARN: Slow response ({i*10}ms)")
        else:
            lines.append(f"2024-01-{i%28+1:02d} 12:00:{i%60:02d} INFO: Request {i} processed successfully")
    (repo / "server.log").write_text("\n".join(lines), encoding="utf-8")

    # Buggy log parser
    (repo / "log_parser.py").write_text('''
import re

def parse_log_line(line):
    parts = line.split(" ", 3)
    return {"date": parts[0], "time": parts[1], "level": parts[2].rstrip(":"), "message": parts[3]}

def count_errors(log_content):
    return len(re.findall(r"ERROR:", log_content))

def get_errors(log_path):
    errors = []
    with open(log_path) as f:
        for line in f:
            if "ERROR" in line:
                errors.append(parse_log_line(line.strip()))
    return errors

def error_rate(log_path):
    total = 0
    errors = 0
    with open(log_path) as f:
        for line in f:
            total += 1
            if "ERROR:" in line:
                errors += 1
    return errors / total  # BUG: division by zero when file empty (edge case)

def filter_by_level(log_path, level):
    results = []
    with open(log_path) as f:
        for line in f:
            parsed = parse_log_line(line.strip())
            if parsed["level"] == level.upper():  # BUG: level param isn't normalized
                results.append(parsed)
    return results
''', encoding="utf-8")
    (repo / "test_log_parser.py").write_text('''
import pytest
from log_parser import parse_log_line, count_errors, get_errors, error_rate, filter_by_level

def test_parse_log_line():
    line = "2024-01-15 12:00:30 ERROR: Something went wrong"
    parsed = parse_log_line(line)
    assert parsed["level"] == "ERROR"
    assert "Something" in parsed["message"]

def test_count_errors():
    content = open("server.log").read()
    assert count_errors(content) == 50  # every 10th line out of 500

def test_get_errors():
    errors = get_errors("server.log")
    assert len(errors) == 50
    assert all(e["level"] == "ERROR" for e in errors)

def test_error_rate():
    rate = error_rate("server.log")
    assert 0.09 < rate < 0.11  # 50/500 = 0.1

def test_error_rate_empty_file(tmp_path):
    p = tmp_path / "empty.log"
    p.write_text("")
    # Should return 0.0 for empty file, not raise ZeroDivisionError
    # We need to import differently to test with custom path
    import log_parser
    total = 0
    errors = 0
    with open(p) as f:
        for line in f:
            total += 1
            if "ERROR:" in line:
                errors += 1
    assert (errors / total if total > 0 else 0.0) == 0.0

def test_filter_by_level_case_insensitive():
    errors = filter_by_level("server.log", "error")
    assert len(errors) == 50
    warns = filter_by_level("server.log", "WARN")
    assert len(warns) == 50  # every 5th line is WARN (but not ERROR)
''', encoding="utf-8")
