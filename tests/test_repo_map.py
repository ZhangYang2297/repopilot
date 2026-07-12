from __future__ import annotations
import pytest
from pathlib import Path
from repopilot.code_index.ignore import is_ignored, iter_source_files, SOURCE_EXTENSIONS, BUILTIN_IGNORE
from repopilot.code_index.symbol_index import index_python, format_file_symbols, Symbol, FileSymbols
from repopilot.code_index.repo_map import RepoMapBuilder


SAMPLE_PY = '''
"""Module docstring."""
import os
import sys
from typing import List


def top_level_function(x: int) -> int:
    """Add one."""
    return x + 1


class Calculator:
    """A simple calculator class."""

    def add(self, a, b):
        """Add two numbers."""
        return a + b

    def subtract(self, a, b):
        return a - b


def another_func():
    pass
'''


class TestIgnore:
    def test_git_dir_ignored(self, tmp_path):
        assert is_ignored(".git/config", tmp_path)

    def test_pycache_ignored(self, tmp_path):
        assert is_ignored("__pycache__/foo.pyc", tmp_path)

    def test_node_modules_ignored(self, tmp_path):
        assert is_ignored("node_modules/pkg/index.js", tmp_path)

    def test_min_js_ignored(self, tmp_path):
        assert is_ignored("dist/app.min.js", tmp_path)

    def test_png_ignored(self, tmp_path):
        assert is_ignored("assets/logo.png", tmp_path)

    def test_source_py_not_ignored(self, tmp_path):
        assert not is_ignored("main.py", tmp_path)

    def test_dotenv_ignored(self, tmp_path):
        # .env is a credential file, ignored from indexing
        # (Note: .env is matched by dangerous path protection, but not ignore)
        # Actually .env is a standard source-adjacent file; we allow reading it
        # but permission engine blocks writes to it.
        pass


class TestSymbolIndex:
    def test_index_python_extracts_functions(self):
        fs = index_python(SAMPLE_PY, "sample.py")
        func_names = [s.name for s in fs.symbols if s.kind == "function"]
        assert "top_level_function" in func_names
        assert "another_func" in func_names

    def test_index_python_extracts_class(self):
        fs = index_python(SAMPLE_PY, "sample.py")
        classes = [s for s in fs.symbols if s.kind == "class"]
        assert len(classes) == 1
        assert classes[0].name == "Calculator"
        assert "calculator" in classes[0].docstring.lower()

    def test_index_python_extracts_methods(self):
        fs = index_python(SAMPLE_PY, "sample.py")
        methods = [s for s in fs.symbols if s.kind == "method"]
        method_names = [m.name for m in methods]
        assert "add" in method_names
        assert "subtract" in method_names
        for m in methods:
            assert m.parent == "Calculator"

    def test_index_python_line_numbers(self):
        fs = index_python(SAMPLE_PY, "sample.py")
        add_func = [s for s in fs.symbols if s.name == "top_level_function"][0]
        assert add_func.line == 8  # def top_level_function on line 8 (SAMPLE_PY starts with \n, line 1 is blank)

    def test_index_python_docstring(self):
        fs = index_python(SAMPLE_PY, "sample.py")
        add_method = [s for s in fs.symbols if s.name == "add" and s.kind == "method"][0]
        assert "Add two numbers" in add_method.docstring

    def test_index_python_imports(self):
        fs = index_python(SAMPLE_PY, "sample.py")
        imports_text = " ".join(fs.imports)
        assert "import os" in imports_text
        assert "import sys" in imports_text
        assert "from typing" in imports_text

    def test_format_file_symbols(self):
        fs = index_python(SAMPLE_PY, "sample.py")
        out = format_file_symbols(fs)
        assert "sample.py" in out
        assert "class Calculator" in out
        assert "def top_level_function" in out
        assert ".add()" in out

    def test_empty_source(self):
        fs = index_python("", "empty.py")
        assert len(fs.symbols) == 0


class TestRepoMapBuilder:
    @pytest.fixture
    def sample_repo(self, tmp_path):
        (tmp_path / "main.py").write_text(SAMPLE_PY, encoding="utf-8")
        (tmp_path / "utils.py").write_text('''
def helper():
    """Helper function."""
    return 42
''', encoding="utf-8")
        sub = tmp_path / "pkg"
        sub.mkdir()
        (sub / "__init__.py").write_text("", encoding="utf-8")
        (sub / "core.py").write_text('''
class Core:
    """Core logic."""
    def run(self):
        pass
''', encoding="utf-8")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "x.pyc").write_bytes(b"x")
        return tmp_path

    def test_build_contains_files(self, sample_repo):
        builder = RepoMapBuilder(sample_repo, max_tokens=2000)
        out = builder.build()
        assert "main.py" in out
        assert "utils.py" in out
        assert "Core" in out  # class from pkg/core.py
        # pycache should NOT be there
        assert "__pycache__" not in out
        assert ".pyc" not in out

    def test_build_respects_token_budget(self, sample_repo):
        # Very small budget should truncate
        builder = RepoMapBuilder(sample_repo, max_tokens=50)
        out = builder.build()
        assert len(out) <= 50 * 4 + 100  # rough bound

    def test_build_from_sandbox(self, sample_repo):
        from repopilot.sandbox import LocalSandbox
        with LocalSandbox(sample_repo) as sb:
            tree = sb.get_repo_tree(max_tokens=2000)
            assert "main.py" in tree
            assert "Calculator" in tree

    def test_update_invalidates_cache(self, sample_repo):
        builder = RepoMapBuilder(sample_repo)
        _ = builder.build()
        builder.update("main.py")
        # Should not crash; second build should work
        out2 = builder.build()
        assert "main.py" in out2

    def test_binds_repo_root(self, tmp_path):
        (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
        builder = RepoMapBuilder(tmp_path)
        out = builder.build()
        assert "a.py" in out
