from __future__ import annotations
import os
import tempfile
import pytest
from pathlib import Path
from repopilot.sandbox import LocalSandbox, ExecResult


@pytest.fixture
def tmp_repo(tmp_path):
    """Create a small temporary git-like repo with test files."""
    (tmp_path / "main.py").write_text("def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n", encoding="utf-8")
    (tmp_path / "test_math.py").write_text("from main import add\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8")
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "__init__.py").write_text("", encoding="utf-8")
    (sub / "utils.py").write_text("def greet(name):\n    return f'Hello, {name}!'\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "cached.pyc").write_bytes(b"x")
    return tmp_path


def test_read_file_with_line_numbers(tmp_repo):
    with LocalSandbox(tmp_repo) as s:
        r = s.read_file("main.py")
        assert r.total_lines == 5  # 4 code lines + trailing newline
        assert "1|def add" in r.content
        assert "2|    return a + b" in r.content


def test_read_file_pagination(tmp_repo):
    with LocalSandbox(tmp_repo) as s:
        # offset=3 (skip lines 1,2,3), limit=2 -> lines 4,5
        r = s.read_file("main.py", offset=3, limit=2)
        assert r.start_line == 4
        assert "def subtract" in r.content


def test_read_file_not_found(tmp_repo):
    with LocalSandbox(tmp_repo) as s:
        with pytest.raises(FileNotFoundError):
            s.read_file("nonexistent.py")


def test_write_file(tmp_repo):
    with LocalSandbox(tmp_repo) as s:
        s.write_file("new_file.py", "print('hello')\n")
        assert (tmp_repo / "new_file.py").exists()
        assert "print('hello')" in (tmp_repo / "new_file.py").read_text()


def test_edit_file(tmp_repo):
    with LocalSandbox(tmp_repo) as s:
        diff = s.edit_file("main.py", "return a + b", "return a + b + 1")
        assert "return a + b + 1" in (tmp_repo / "main.py").read_text()
        assert "--- a/main.py" in diff or "+++ b/main.py" in diff


def test_edit_file_string_not_found(tmp_repo):
    with LocalSandbox(tmp_repo) as s:
        with pytest.raises(ValueError, match="not found"):
            s.edit_file("main.py", "nonexistent_xyz", "replacement")


def test_exec_echo(tmp_repo):
    with LocalSandbox(tmp_repo) as s:
        r = s.exec("echo hello")
        assert r.exit_code == 0
        assert "hello" in r.stdout
        assert r.ok is True


def test_exec_command_failure(tmp_repo):
    with LocalSandbox(tmp_repo) as s:
        r = s.exec("python -c \"import sys; sys.exit(2)\"")
        assert r.exit_code == 2
        assert r.ok is False


def test_exec_timeout(tmp_repo):
    with LocalSandbox(tmp_repo) as s:
        r = s.exec("python -c \"import time; time.sleep(10)\"", timeout=1)
        assert r.timed_out is True


def test_exec_cwd_isolation(tmp_repo):
    with LocalSandbox(tmp_repo) as s:
        r = s.exec("cd pkg && python -c \"import os; print(os.getcwd())\"")
        assert r.exit_code == 0
        assert "pkg" in r.stdout or "pkg" in r.stderr


def test_path_traversal_blocked(tmp_repo):
    with LocalSandbox(tmp_repo) as s:
        with pytest.raises(PermissionError):
            s.read_file("../outside.txt")
        with pytest.raises(PermissionError):
            s.write_file("../../etc/passwd", "bad")


def test_glob_matches(tmp_repo):
    with LocalSandbox(tmp_repo) as s:
        pys = s.glob("*.py")
        assert "main.py" in pys
        assert "test_math.py" in pys
        # __pycache__ should be excluded
        assert not any("__pycache__" in f for f in pys)
        # Recursive glob
        all_py = s.glob("**/*.py")
        assert "pkg/utils.py" in all_py


def test_grep_finds_string(tmp_repo):
    with LocalSandbox(tmp_repo) as s:
        matches = s.grep("def add")
        assert len(matches) >= 1
        assert matches[0].file == "main.py"
        assert matches[0].line_no == 1


def test_grep_case_insensitive(tmp_repo):
    with LocalSandbox(tmp_repo) as s:
        matches = s.grep("TEST_ADD", ignore_case=True)
        assert len(matches) >= 1


def test_grep_with_glob_filter(tmp_repo):
    with LocalSandbox(tmp_repo) as s:
        matches = s.grep("def", glob_filter="utils.py")
        assert all("utils.py" in m.file for m in matches)


def test_grep_skips_pycache(tmp_repo):
    with LocalSandbox(tmp_repo) as s:
        matches = s.grep("cached")
        assert len(matches) == 0  # pycache files skipped


def test_list_dir(tmp_repo):
    with LocalSandbox(tmp_repo) as s:
        tree = s.list_dir(".", max_depth=1)
        assert "main.py" in tree
        assert "test_math.py" in tree
        assert "README.md" in tree
        assert "pkg/" in tree


def test_list_dir_depth(tmp_repo):
    with LocalSandbox(tmp_repo) as s:
        tree = s.list_dir(".", max_depth=2)
        assert "pkg/" in tree
        assert tree["pkg/"] is not None
        assert "utils.py" in tree["pkg/"]


def test_get_repo_tree(tmp_repo):
    with LocalSandbox(tmp_repo) as s:
        tree = s.get_repo_tree()
        assert "main.py" in tree
        assert "# Repo Map" in tree
        assert "def add" in tree or "add" in tree


def test_exec_working_dir(tmp_repo):
    with LocalSandbox(tmp_repo) as s:
        r = s.exec("cd pkg && python -c \"import utils; print(utils.greet('world'))\"")
        assert "Hello, world" in r.stdout
