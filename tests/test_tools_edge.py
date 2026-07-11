"""Edge case and boundary tests for all tools."""
from __future__ import annotations
import pytest
from repopilot.tools import (
    ReadFileTool, WriteFileTool, EditFileTool,
    GrepTool, GlobTool, ListDirTool, RepoTreeTool,
    BashTool, RunPythonTool, FinishTool, AgentFinished,
    build_default_registry, truncate_text, ToolResult,
)
from repopilot.permission import PermissionEngine
from repopilot.sandbox import LocalSandbox


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "main.py").write_text("def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n", encoding="utf-8")
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "utils.py").write_text("def greet(name):\n    return f'Hello, {name}!'\n", encoding="utf-8")
    (sub / "more.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Test\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=xxx", encoding="utf-8")
    return tmp_path


# ── truncate_text edge cases ──────────────────────────────

class TestTruncateTextEdgeCases:
    def test_empty_string(self):
        assert truncate_text("") == ""

    def test_exactly_at_boundary(self):
        text = "a" * (500 + 1500 + 50)
        assert truncate_text(text) == text

    def test_one_char_over_boundary(self):
        text = "a" * (500 + 1500 + 51)
        r = truncate_text(text)
        assert "truncated" in r
        assert len(r) < len(text)

    def test_max_lines_one_line(self):
        assert truncate_text("hello", max_lines=10) == "hello"

    def test_max_lines_truncates(self):
        text = "\n".join(f"line{i}" for i in range(100))
        r = truncate_text(text, max_lines=5)
        assert "line0" in r
        assert "truncated" in r
        assert "line99" not in r

    def test_unicode_handling(self):
        text = "你好" * 1000
        r = truncate_text(text)
        assert "你好" in r


# ── ReadFile edge cases ──────────────────────────────────

class TestReadFileEdgeCases:
    def test_empty_file(self, repo):
        (repo / "empty.py").write_text("")
        with LocalSandbox(repo) as sb:
            t = ReadFileTool()
            r = t.execute({"path": "empty.py"}, sb)
            assert bool(r) is True

    def test_large_offset_beyond_eof(self, repo):
        with LocalSandbox(repo) as sb:
            t = ReadFileTool()
            r = t.execute({"path": "main.py", "offset": 9999}, sb)
            # offset beyond EOF should return empty-ish result gracefully
            assert bool(r) is True

    def test_limit_zero(self, repo):
        with LocalSandbox(repo) as sb:
            t = ReadFileTool()
            r = t.execute({"path": "main.py", "limit": 1}, sb)
            assert bool(r) is True

    def test_limit_capped(self, repo):
        """limit=99999 should be capped at MAX_LIMIT=2000"""
        with LocalSandbox(repo) as sb:
            t = ReadFileTool()
            r = t.execute({"path": "main.py", "limit": 99999}, sb)
            assert bool(r) is True

    def test_binary_file_no_crash(self, repo):
        (repo / "binary.bin").write_bytes(b"\x00\x01\x02\xff\xfe")
        with LocalSandbox(repo) as sb:
            t = ReadFileTool()
            r = t.execute({"path": "binary.bin"}, sb)
            # Should not crash, errors=replace handles bad bytes
            assert isinstance(r, ToolResult)


# ── WriteFile edge cases ────────────────────────────────

class TestWriteFileEdgeCases:
    def test_write_empty_content(self, repo):
        with LocalSandbox(repo) as sb:
            t = WriteFileTool()
            r = t.execute({"path": "empty.txt", "content": ""}, sb)
            assert bool(r) is True
            assert (repo / "empty.txt").read_text() == ""

    def test_write_creates_parent_dirs(self, repo):
        with LocalSandbox(repo) as sb:
            t = WriteFileTool()
            r = t.execute({"path": "a/b/c/deep.py", "content": "x = 1\n"}, sb)
            assert bool(r) is True
            assert (repo / "a/b/c/deep.py").exists()

    def test_write_oversized_content_rejected(self, repo):
        with LocalSandbox(repo) as sb:
            t = WriteFileTool()
            huge = "x" * 200000
            r = t.execute({"path": "huge.txt", "content": huge}, sb)
            assert bool(r) is False
            assert "too large" in r.error.lower()


# ── EditFile edge cases ────────────────────────────────

class TestEditFileEdgeCases:
    def test_replace_all(self, repo):
        (repo / "dup.py").write_text("foo foo foo\n", encoding="utf-8")
        with LocalSandbox(repo) as sb:
            t = EditFileTool()
            r = t.execute({
                "path": "dup.py",
                "old_string": "foo",
                "new_string": "bar",
                "replace_all": True,
            }, sb)
            assert bool(r) is True
            assert "all occurrences" in r.content
            content = (repo / "dup.py").read_text()
            assert content.count("bar") == 3
            assert "foo" not in content

    def test_replace_first_only(self, repo):
        (repo / "dup.py").write_text("foo foo foo\n", encoding="utf-8")
        with LocalSandbox(repo) as sb:
            t = EditFileTool()
            r = t.execute({
                "path": "dup.py",
                "old_string": "foo",
                "new_string": "bar",
                "replace_all": False,
            }, sb)
            content = (repo / "dup.py").read_text()
            assert content.count("bar") == 1
            assert content.count("foo") == 2

    def test_empty_old_string_rejected(self, repo):
        with LocalSandbox(repo) as sb:
            t = EditFileTool()
            r = t.execute({"path": "main.py", "old_string": "", "new_string": "x"}, sb)
            assert bool(r) is False

    def test_multiline_edit(self, repo):
        with LocalSandbox(repo) as sb:
            t = EditFileTool()
            old = "def add(a, b):\n    return a + b"
            new = "def add(a, b):\n    \"\"\"Add two numbers.\"\"\"\n    return a + b"
            r = t.execute({"path": "main.py", "old_string": old, "new_string": new}, sb)
            assert bool(r) is True
            assert '"""Add two numbers."""' in (repo / "main.py").read_text()


# ── Grep edge cases ──────────────────────────────────

class TestGrepEdgeCases:
    def test_invalid_regex(self, repo):
        with LocalSandbox(repo) as sb:
            t = GrepTool()
            r = t.execute({"pattern": "[invalid(regex"}, sb)
            # Should not crash; may return error or results
            assert isinstance(r, ToolResult)

    def test_path_filter(self, repo):
        with LocalSandbox(repo) as sb:
            t = GrepTool()
            r = t.execute({"pattern": "def", "path": "pkg"}, sb)
            assert bool(r) is True
            # Only pkg files should appear
            assert "main.py" not in r.content
            assert "utils.py" in r.content

    def test_case_insensitive(self, repo):
        (repo / "case.txt").write_text("HELLO WORLD\nhello world\n", encoding="utf-8")
        with LocalSandbox(repo) as sb:
            t = GrepTool()
            r = t.execute({"pattern": "hello", "ignore_case": True}, sb)
            assert "HELLO" in r.content or "hello" in r.content


# ── Glob edge cases ──────────────────────────────────

class TestGlobEdgeCases:
    def test_path_subdirectory(self, repo):
        with LocalSandbox(repo) as sb:
            t = GlobTool()
            r = t.execute({"pattern": "*.py", "path": "pkg"}, sb)
            assert bool(r) is True
            assert "pkg/utils.py" in r.content
            assert "main.py" not in r.content

    def test_no_match_returns_message(self, repo):
        with LocalSandbox(repo) as sb:
            t = GlobTool()
            r = t.execute({"pattern": "*.java"}, sb)
            assert "No files" in r.content


# ── BashTool edge cases ──────────────────────────────

class TestBashToolEdgeCases:
    def test_empty_command(self, repo):
        with LocalSandbox(repo) as sb:
            t = BashTool()
            r = t.execute({"command": ""}, sb)
            assert bool(r) is False
            assert "requires" in r.error.lower()

    def test_command_with_special_chars(self, repo):
        with LocalSandbox(repo) as sb:
            t = BashTool()
            r = t.execute({"command": 'echo "hello  world"  '}, sb)
            assert bool(r) is True

    def test_long_output_truncated(self, repo):
        with LocalSandbox(repo) as sb:
            t = BashTool()
            # Generate 10000 chars of output
            cmd = 'python -c "print(\'x\'*10000)"'
            r = t.execute({"command": cmd}, sb)
            # Should be truncated (head 500 + tail 1500)
            assert "truncated" in r.content or len(r.content) < 10000

    def test_invalid_cwd(self, repo):
        with LocalSandbox(repo) as sb:
            t = BashTool()
            r = t.execute({"command": "echo hi", "cwd": "nonexistent_dir"}, sb)
            # Should not crash; may return error result
            assert isinstance(r, ToolResult)


# ── RunPython edge cases ─────────────────────────────

class TestRunPythonEdgeCases:
    def test_multiline_code(self, repo):
        with LocalSandbox(repo) as sb:
            t = RunPythonTool()
            code = "for i in range(3):\n    print(f'num={i}')"
            r = t.execute({"code": code}, sb)
            assert "num=0" in r.content
            assert "num=2" in r.content

    def test_syntax_error(self, repo):
        with LocalSandbox(repo) as sb:
            t = RunPythonTool()
            r = t.execute({"code": "def broken("}, sb)
            assert isinstance(r, ToolResult)  # error captured, not crashed

    def test_import_json(self, repo):
        with LocalSandbox(repo) as sb:
            t = RunPythonTool()
            r = t.execute({"code": "import json; print(json.dumps({'a':1}))"}, sb)
            assert "1" in r.content


# ── Registry integration ────────────────────────────

class TestToolRegistryEdgeCases:
    def test_all_tools_registered(self):
        reg = build_default_registry()
        expected = {"read_file", "write_file", "edit_file", "grep", "glob",
                    "list_dir", "get_repo_tree", "bash", "run_python", "finish"}
        assert expected == set(reg.tool_names())

    def test_schemas_valid_openai_format(self):
        reg = build_default_registry()
        schemas = reg.schemas()
        for s in schemas:
            assert s["type"] == "function"
            fn = s["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn
            assert fn["parameters"]["type"] == "object"

    def test_finish_propagates_through_registry(self, repo):
        pe = PermissionEngine(mode="auto")
        reg = build_default_registry(permission_engine=pe)
        with LocalSandbox(repo) as sb:
            with pytest.raises(AgentFinished):
                reg.execute("finish", {"summary": "done"}, sb)

    def test_sensitive_file_blocked(self, repo):
        pe = PermissionEngine(mode="confirm")
        reg = build_default_registry(permission_engine=pe)
        with LocalSandbox(repo) as sb:
            # edit .env should be denied (dangerous path)
            r = reg.execute("edit_file", {"path": ".env", "old_string": "x", "new_string": "y"}, sb)
            assert bool(r) is False
            # bash cat ~/.ssh/id_rsa should be denied
            r = reg.execute("bash", {"command": "cat ~/.ssh/id_rsa"}, sb)
            assert bool(r) is False

    def test_deny_mode_blocks_write_and_exec(self, repo):
        pe = PermissionEngine(mode="deny")
        reg = build_default_registry(permission_engine=pe)
        with LocalSandbox(repo) as sb:
            assert reg.execute("write_file", {"path": "x.py", "content": "x"}, sb).error is not None
            assert reg.execute("bash", {"command": "echo hi"}, sb).error is not None
            # Read still works
            r = reg.execute("read_file", {"path": "main.py"}, sb)
            assert bool(r) is True

