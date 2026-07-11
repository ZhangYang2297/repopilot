from __future__ import annotations
import pytest
from pathlib import Path
from repopilot.tools import (
    ReadFileTool, WriteFileTool, EditFileTool,
    build_default_registry, ToolResult,
)
from repopilot.sandbox import LocalSandbox


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "main.py").write_text("def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n", encoding="utf-8")
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "utils.py").write_text("def greet(name):\n    return f'Hello, {name}!'\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=xxx", encoding="utf-8")
    return tmp_path


class TestReadFileTool:
    def test_read_file(self, repo):
        with LocalSandbox(repo) as sb:
            tool = ReadFileTool()
            r = tool.execute({"path": "main.py"}, sb)
            assert bool(r) is True
            assert "def add" in r.content
            assert "1|" in r.content  # line numbers present
            assert r.metadata["path"] == "main.py"

    def test_read_file_pagination(self, repo):
        with LocalSandbox(repo) as sb:
            tool = ReadFileTool()
            r = tool.execute({"path": "main.py", "offset": 3, "limit": 2}, sb)
            assert "def subtract" in r.content
            "4|def subtract" in r.content

    def test_read_file_not_found(self, repo):
        with LocalSandbox(repo) as sb:
            tool = ReadFileTool()
            r = tool.execute({"path": "nonexist.py"}, sb)
            assert bool(r) is False
            assert "not found" in r.error.lower()

    def test_read_missing_path_arg(self, repo):
        with LocalSandbox(repo) as sb:
            tool = ReadFileTool()
            r = tool.execute({}, sb)
            assert bool(r) is False

    def test_read_truncated_notice(self, repo):
        with LocalSandbox(repo) as sb:
            tool = ReadFileTool()
            r = tool.execute({"path": "main.py", "limit": 2}, sb)
            # truncated file has notice
            assert "truncated" in r.content.lower() or "Use offset" in r.content or "..." in r.content


class TestWriteFileTool:
    def test_write_new_file(self, repo):
        with LocalSandbox(repo) as sb:
            tool = WriteFileTool()
            r = tool.execute({"path": "new.py", "content": "print('hi')\n"}, sb)
            assert bool(r) is True
            assert (repo / "new.py").exists()
            assert "Wrote" in r.content

    def test_write_existing_file(self, repo):
        with LocalSandbox(repo) as sb:
            tool = WriteFileTool()
            r = tool.execute({"path": "main.py", "content": "x = 1\n"}, sb)
            assert bool(r) is True
            assert "x = 1" in (repo / "main.py").read_text()

    def test_write_missing_path(self, repo):
        with LocalSandbox(repo) as sb:
            tool = WriteFileTool()
            r = tool.execute({"content": "hi"}, sb)
            assert bool(r) is False


class TestEditFileTool:
    def test_edit_success(self, repo):
        with LocalSandbox(repo) as sb:
            tool = EditFileTool()
            r = tool.execute({
                "path": "main.py",
                "old_string": "return a + b",
                "new_string": "return a + b + 1",
            }, sb)
            assert bool(r) is True
            assert "Applied edit" in r.content
            assert "b + 1" in (repo / "main.py").read_text()

    def test_edit_string_not_found(self, repo):
        with LocalSandbox(repo) as sb:
            tool = EditFileTool()
            r = tool.execute({
                "path": "main.py",
                "old_string": "nonexistent_xyz",
                "new_string": "y",
            }, sb)
            assert bool(r) is False
            assert "failed" in r.error.lower() or "not found" in r.error.lower()

    def test_edit_file_not_found(self, repo):
        with LocalSandbox(repo) as sb:
            tool = EditFileTool()
            r = tool.execute({"path": "ghost.py", "old_string": "x", "new_string": "y"}, sb)
            assert bool(r) is False

    def test_edit_identical_strings(self, repo):
        with LocalSandbox(repo) as sb:
            tool = EditFileTool()
            r = tool.execute({"path": "main.py", "old_string": "x", "new_string": "x"}, sb)
            assert bool(r) is False


class TestDefaultRegistry:
    def test_registry_contains_all_tools(self, repo):
        reg = build_default_registry()
        names = reg.tool_names()
        for expected in ("read_file", "write_file", "edit_file",
                         "grep", "glob", "list_dir", "get_repo_tree"):
            assert expected in names, f"Missing tool: {expected}"

    def test_end_to_end_read_via_registry(self, repo):
        from repopilot.permission import PermissionEngine
        pe = PermissionEngine(mode="auto")
        reg = build_default_registry(permission_engine=pe)
        with LocalSandbox(repo) as sb:
            r = reg.execute("read_file", {"path": "main.py"}, sb)
            assert bool(r) is True
            assert "def add" in r.content

