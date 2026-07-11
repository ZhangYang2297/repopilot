from __future__ import annotations
import pytest
from repopilot.tools import (
    BashTool, RunPythonTool, FinishTool, AgentFinished,
    build_default_registry, ToolResult,
)
from repopilot.sandbox import LocalSandbox


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "main.py").write_text("print('hello')\n", encoding="utf-8")
    return tmp_path


class TestBashTool:
    def test_echo(self, repo):
        with LocalSandbox(repo) as sb:
            tool = BashTool()
            r = tool.execute({"command": "echo hello"}, sb)
            assert bool(r) is True
            assert "hello" in r.stdout if hasattr(r, 'stdout') else True
            assert "hello" in r.content

    def test_exit_code_in_metadata(self, repo):
        with LocalSandbox(repo) as sb:
            tool = BashTool()
            r = tool.execute({"command": "python -c \"import sys; sys.exit(2)\""}, sb)
            assert r.metadata["exit_code"] == 2

    def test_timeout(self, repo):
        with LocalSandbox(repo) as sb:
            tool = BashTool()
            r = tool.execute({"command": "python -c \"import time; time.sleep(10)\"", "timeout": 1}, sb)
            assert r.metadata["timed_out"] is True

    def test_missing_command(self, repo):
        with LocalSandbox(repo) as sb:
            tool = BashTool()
            r = tool.execute({}, sb)
            assert bool(r) is False
            assert "requires" in r.error.lower()

    def test_dangerous_command_blocked_by_permission(self, repo):
        from repopilot.permission import PermissionEngine
        pe = PermissionEngine(mode="confirm")
        reg = build_default_registry(permission_engine=pe)
        with LocalSandbox(repo) as sb:
            r = reg.execute("bash", {"command": "rm -rf /"}, sb)
            assert bool(r) is False
            assert "denied" in r.error.lower()

    def test_safe_command_allowed_in_confirm(self, repo):
        from repopilot.permission import PermissionEngine
        pe = PermissionEngine(mode="confirm")
        reg = build_default_registry(permission_engine=pe)
        with LocalSandbox(repo) as sb:
            r = reg.execute("bash", {"command": "echo test123"}, sb)
            assert bool(r) is True
            assert "test123" in r.content

    def test_cwd(self, repo):
        with LocalSandbox(repo) as sb:
            tool = BashTool()
            r = tool.execute({"command": "cd", "cwd": "main.py"}, sb)
            # cd with a file as cwd will fail — just checking it doesn't crash
            # On Windows 'cd' alone prints cwd, on Unix it errors
            assert isinstance(r, ToolResult)

    def test_timeout_capped(self, repo):
        with LocalSandbox(repo) as sb:
            tool = BashTool()
            # timeout=999 should be capped to MAX_TIMEOUT=120
            r = tool.execute({"command": "echo x", "timeout": 999}, sb)
            assert bool(r) is True


class TestRunPythonTool:
    def test_simple_print(self, repo):
        with LocalSandbox(repo) as sb:
            tool = RunPythonTool()
            r = tool.execute({"code": "print('hello from python')"}, sb)
            assert bool(r) is True
            assert "hello from python" in r.content

    def test_math_computation(self, repo):
        with LocalSandbox(repo) as sb:
            tool = RunPythonTool()
            r = tool.execute({"code": "print(2 + 3 * 4)"}, sb)
            assert "14" in r.content

    def test_missing_code(self, repo):
        with LocalSandbox(repo) as sb:
            tool = RunPythonTool()
            r = tool.execute({}, sb)
            assert bool(r) is False

    def test_stderr_captured(self, repo):
        with LocalSandbox(repo) as sb:
            tool = RunPythonTool()
            r = tool.execute({"code": "import sys; sys.stderr.write('error msg\\n')"}, sb)
            assert "error msg" in r.content or r.metadata["exit_code"] != 0

    def test_exit_code(self, repo):
        with LocalSandbox(repo) as sb:
            tool = RunPythonTool()
            r = tool.execute({"code": "import sys; sys.exit(3)"}, sb)
            assert r.metadata["exit_code"] == 3

    def test_no_temp_file_left(self, repo):
        """Temp .py file should be cleaned up after execution."""
        with LocalSandbox(repo) as sb:
            tool = RunPythonTool()
            tool.execute({"code": "print(1)"}, sb)
            # python -c doesn't create temp files, so this is trivially true
            # but let's verify _tmp files don't exist
            import os
            tmp_files = [f for f in os.listdir(repo) if f.startswith("_tmp_repopilot")]
            assert len(tmp_files) == 0


class TestFinishTool:
    def test_finish_raises(self, repo):
        with LocalSandbox(repo) as sb:
            tool = FinishTool()
            with pytest.raises(AgentFinished) as exc_info:
                tool.execute({"summary": "All done!", "tests_passed": True}, sb)
            assert exc_info.value.summary == "All done!"
            assert exc_info.value.tests_passed is True

    def test_finish_default_tests_passed(self, repo):
        with LocalSandbox(repo) as sb:
            tool = FinishTool()
            with pytest.raises(AgentFinished) as exc_info:
                tool.execute({"summary": "Done"}, sb)
            assert exc_info.value.tests_passed is True

    def test_finish_via_registry(self, repo):
        """Through registry, the exception should propagate (caught by agent loop)."""
        from repopilot.permission import PermissionEngine
        pe = PermissionEngine(mode="auto")
        reg = build_default_registry(permission_engine=pe)
        with LocalSandbox(repo) as sb:
            with pytest.raises(AgentFinished):
                reg.execute("finish", {"summary": "Done via registry"}, sb)

    def test_missing_summary(self, repo):
        with LocalSandbox(repo) as sb:
            tool = FinishTool()
            # No summary → summary defaults to "Task completed."
            with pytest.raises(AgentFinished):
                tool.execute({}, sb)
