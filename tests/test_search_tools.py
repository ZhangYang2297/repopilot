from __future__ import annotations
import pytest
from repopilot.tools import GrepTool, GlobTool, ListDirTool, RepoTreeTool, build_default_registry
from repopilot.sandbox import LocalSandbox


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "main.py").write_text("def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n", encoding="utf-8")
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "utils.py").write_text("def greet(name):\n    return f'Hello, {name}!'\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "cached.pyc").write_bytes(b"x")
    return tmp_path


class TestGrepTool:
    def test_grep_finds_match(self, repo):
        with LocalSandbox(repo) as sb:
            tool = GrepTool()
            r = tool.execute({"pattern": "def add"}, sb)
            assert bool(r) is True
            assert "main.py" in r.content
            assert "1" in r.content  # line number

    def test_grep_no_match(self, repo):
        with LocalSandbox(repo) as sb:
            tool = GrepTool()
            r = tool.execute({"pattern": "zzz_nonexist_zzz"}, sb)
            assert bool(r) is True
            assert "No matches" in r.content

    def test_grep_case_insensitive(self, repo):
        with LocalSandbox(repo) as sb:
            tool = GrepTool()
            r = tool.execute({"pattern": "GREET", "ignore_case": True}, sb)
            assert "greet" in r.content.lower() or "GREET" in r.content

    def test_grep_glob_filter(self, repo):
        with LocalSandbox(repo) as sb:
            tool = GrepTool()
            r = tool.execute({"pattern": "def", "glob": "utils.py"}, sb)
            assert bool(r) is True
            # Only utils.py should be in results
            assert "main.py" not in r.content
            assert "utils.py" in r.content

    def test_grep_missing_pattern(self, repo):
        with LocalSandbox(repo) as sb:
            tool = GrepTool()
            r = tool.execute({}, sb)
            assert bool(r) is False

    def test_grep_skips_pycache(self, repo):
        with LocalSandbox(repo) as sb:
            tool = GrepTool()
            r = tool.execute({"pattern": "cached"}, sb)
            assert "No matches" in r.content  # pycache should be skipped


class TestGlobTool:
    def test_glob_py_files(self, repo):
        with LocalSandbox(repo) as sb:
            tool = GlobTool()
            r = tool.execute({"pattern": "*.py"}, sb)
            assert "main.py" in r.content
            assert "utils.py" not in r.content  # *.py doesn't recurse

    def test_glob_recursive(self, repo):
        with LocalSandbox(repo) as sb:
            tool = GlobTool()
            r = tool.execute({"pattern": "**/*.py"}, sb)
            assert "main.py" in r.content
            assert "pkg/utils.py" in r.content

    def test_glob_no_match(self, repo):
        with LocalSandbox(repo) as sb:
            tool = GlobTool()
            r = tool.execute({"pattern": "*.java"}, sb)
            assert "No files" in r.content

    def test_glob_excludes_pycache(self, repo):
        with LocalSandbox(repo) as sb:
            tool = GlobTool()
            r = tool.execute({"pattern": "**/*"}, sb)
            assert ".pyc" not in r.content or "__pycache__" not in r.content


class TestListDirTool:
    def test_list_root(self, repo):
        with LocalSandbox(repo) as sb:
            tool = ListDirTool()
            r = tool.execute({"path": ".", "max_depth": 1}, sb)
            assert "main.py" in r.content
            assert "README.md" in r.content
            assert "pkg/" in r.content

    def test_list_depth2(self, repo):
        with LocalSandbox(repo) as sb:
            tool = ListDirTool()
            r = tool.execute({"path": ".", "max_depth": 2}, sb)
            assert "pkg/" in r.content
            assert "utils.py" in r.content

    def test_list_nonexistent(self, repo):
        with LocalSandbox(repo) as sb:
            tool = ListDirTool()
            r = tool.execute({"path": "nonexist"}, sb)
            assert "not found" in r.content.lower() or "empty" in r.content.lower()

    def test_tree_format(self, repo):
        with LocalSandbox(repo) as sb:
            tool = ListDirTool()
            r = tool.execute({"path": ".", "max_depth": 2}, sb)
            # Should have tree drawing characters
            assert "├──" in r.content or "└──" in r.content


class TestRepoTreeTool:
    def test_repo_tree(self, repo):
        with LocalSandbox(repo) as sb:
            tool = RepoTreeTool()
            r = tool.execute({}, sb)
            assert bool(r) is True
            assert "main.py" in r.content


class TestRegistryEndToEnd:
    def test_registry_grep_via_sandbox(self, repo):
        from repopilot.permission import PermissionEngine
        pe = PermissionEngine(mode="auto")
        reg = build_default_registry(permission_engine=pe)
        with LocalSandbox(repo) as sb:
            r = reg.execute("grep", {"pattern": "def add"}, sb)
            assert bool(r) is True
            assert "main.py" in r.content

    def test_registry_write_then_read(self, repo):
        from repopilot.permission import PermissionEngine
        pe = PermissionEngine(mode="auto")
        reg = build_default_registry(permission_engine=pe)
        with LocalSandbox(repo) as sb:
            reg.execute("write_file", {"path": "test_new.py", "content": "x = 42\n"}, sb)
            r = reg.execute("read_file", {"path": "test_new.py"}, sb)
            assert "x = 42" in r.content

    def test_registry_edit_file(self, repo):
        from repopilot.permission import PermissionEngine
        pe = PermissionEngine(mode="auto")
        reg = build_default_registry(permission_engine=pe)
        with LocalSandbox(repo) as sb:
            r = reg.execute("edit_file", {
                "path": "main.py",
                "old_string": "return a + b",
                "new_string": "return a + b + 100",
            }, sb)
            assert bool(r) is True
            content = (repo / "main.py").read_text()
            assert "+ 100" in content
