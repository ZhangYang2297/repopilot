"""Security regression tests for sandbox fixes."""
from __future__ import annotations
import os
import pytest
from pathlib import Path

from repopilot.sandbox.local_sandbox import LocalSandbox


class TestAtomicWrite:
    def test_write_creates_file(self, tmp_repo):
        with LocalSandbox(tmp_repo) as sb:
            sb.write_file("new_file.py", "print('hello')")
            assert (tmp_repo / "new_file.py").read_text() == "print('hello')"

    def test_write_overwrites_existing(self, tmp_repo):
        with LocalSandbox(tmp_repo) as sb:
            sb.write_file("main.py", "new content")
            assert (tmp_repo / "main.py").read_text() == "new content"

    def test_write_creates_parent_dirs(self, tmp_repo):
        with LocalSandbox(tmp_repo) as sb:
            sb.write_file("a/b/c/deep.py", "x = 1")
            assert (tmp_repo / "a/b/c/deep.py").read_text() == "x = 1"


class TestEditSafety:
    def test_edit_short_old_string_rejected(self, tmp_repo):
        with LocalSandbox(tmp_repo) as sb:
            with pytest.raises(ValueError, match="too short"):
                sb.edit_file("main.py", "a", "b")

    def test_edit_ambiguous_match_rejected(self, tmp_repo):
        (tmp_repo / "dup.txt").write_text("foo foo foo\n", encoding="utf-8")
        with LocalSandbox(tmp_repo) as sb:
            with pytest.raises(ValueError, match="appears 3 times"):
                sb.edit_file("dup.txt", "foo", "bar")

    def test_edit_replace_all_ambiguous_ok(self, tmp_repo):
        (tmp_repo / "dup.txt").write_text("foo foo foo\n", encoding="utf-8")
        with LocalSandbox(tmp_repo) as sb:
            diff = sb.edit_file("dup.txt", "foo", "bar", replace_all=True)
            assert "bar" in diff
            assert (tmp_repo / "dup.txt").read_text().count("bar") == 3

    def test_edit_unique_match_works(self, tmp_repo):
        with LocalSandbox(tmp_repo) as sb:
            diff = sb.edit_file("main.py", "def add(a, b):", "def add(a, b, c=0):")
            assert "c=0" in diff


class TestPathTraversal:
    def test_local_sandbox_blocks_path_traversal(self, tmp_repo):
        with LocalSandbox(tmp_repo) as sb:
            with pytest.raises(PermissionError):
                sb.read_file("../secret.txt")

    def test_docker_safe_container_path_strips_leading_slash(self):
        from repopilot.sandbox.docker_sandbox import DockerSandbox
        # _safe_container_path should strip leading / and resolve ..
        # Can't fully test Docker without daemon, but test the static method
        # We need a sandbox instance with repo_path
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            sb = DockerSandbox(Path(td))
            assert sb._safe_container_path("main.py") == "/workspace/main.py"
            assert sb._safe_container_path("/etc/passwd") == "/workspace/etc/passwd"
            # Path traversal: ../../etc should not escape
            result = sb._safe_container_path("../../etc/passwd")
            assert result.startswith("/workspace/")
            assert ".." not in result


class TestRunPythonCleanup:
    def test_temp_script_cleaned_up(self, tmp_repo):
        from repopilot.sandbox.local_sandbox import LocalSandbox
        from repopilot.tools.exec_tools import RunPythonTool
        with LocalSandbox(tmp_repo) as sb:
            tool = RunPythonTool()
            tool.execute({"code": "print(42)"}, sb)
            # Check no _runpy_*.py temp files remain
            temp_files = list(tmp_repo.glob("_runpy_*.py"))
            assert len(temp_files) == 0, f"Temp files not cleaned: {temp_files}"


class TestBinaryFileSkip:
    def test_grep_skips_binary_extensions(self, tmp_repo):
        """Verify binary files are skipped in grep results (at least no crash)."""
        # Write a fake binary file
        (tmp_repo / "fake.pyc").write_bytes(b"\x00\x01\x02\x03binary content here")
        with LocalSandbox(tmp_repo) as sb:
            # Should not crash and should not include .pyc results
            matches = sb.grep("content")
            pyc_matches = [m for m in matches if m.file.endswith(".pyc")]
            assert len(pyc_matches) == 0


@pytest.fixture
def tmp_repo(tmp_path):
    """Create a minimal test repo."""
    (tmp_path / "main.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "test_math.py").write_text("from main import add\ndef test_add():\n    assert add(2,3) == 5\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Test\n", encoding="utf-8")
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    return tmp_path
