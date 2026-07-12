from __future__ import annotations
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from repopilot.sandbox import DockerSandbox, ExecResult


def _make_sandbox(tmp_path, exec_returns=None):
    """Create DockerSandbox with mocked docker client."""
    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_client.containers.run.return_value = mock_container
    mock_client.api.exec_create.return_value = {"Id": "exec1"}
    if exec_returns is None:
        exec_returns = (b"", b"", 0)
    # exec_start returns streaming tuples (stdout, stderr); exec_inspect returns ExitCode
    stdout_b, stderr_b, exit_code = exec_returns

    def fake_exec_start(exec_id, stream=True, demux=True):
        # Yield one chunk then end
        yield (stdout_b, stderr_b)
        return
    mock_client.api.exec_start.side_effect = fake_exec_start
    mock_client.api.exec_inspect.return_value = {"ExitCode": exit_code}
    mock_client.images.get.return_value = MagicMock()  # image exists locally
    s = DockerSandbox(
        repo_path=tmp_path, image="python:3.10-slim",
        mem_limit="1g", cpu_quota=100000, network_mode="none",
        docker_client=mock_client,
    )
    return s, mock_client, mock_container


def test_setup_starts_container(tmp_path):
    s, mc, container = _make_sandbox(tmp_path)
    s.setup()
    mc.containers.run.assert_called_once()
    kwargs = mc.containers.run.call_args.kwargs
    assert kwargs["detach"] is True
    assert kwargs["working_dir"] == "/workspace"
    assert "/workspace" in [m["Target"] for m in kwargs["mounts"]]
    s.teardown()


def test_teardown_kills_container(tmp_path):
    s, mc, container = _make_sandbox(tmp_path)
    s.setup()
    s.teardown()
    container.kill.assert_called_once()
    container.remove.assert_called_once()


def test_context_manager(tmp_path):
    s, mc, container = _make_sandbox(tmp_path)
    with s:
        pass
    container.kill.assert_called_once()


def test_exec_returns_result(tmp_path):
    stdout = b"hello from container\n"
    s, mc, container = _make_sandbox(tmp_path, exec_returns=(stdout, b"", 0))
    s.setup()
    r = s.exec("echo hello", timeout=5)
    assert r.exit_code == 0
    assert "hello from container" in r.stdout
    assert r.ok is True
    s.teardown()


def test_exec_nonzero_exit(tmp_path):
    s, mc, container = _make_sandbox(tmp_path, exec_returns=(b"", b"error", 2))
    s.setup()
    r = s.exec("false")
    assert r.exit_code == 2
    assert r.ok is False
    s.teardown()


def test_shquote():
    from repopilot.sandbox.docker_sandbox import shquote
    assert shquote("hello") == "'hello'"
    assert shquote("it's") == "'it'\"'\"'s'"


def test_container_path_under_workspace(tmp_path):
    s, _, _ = _make_sandbox(tmp_path)
    assert s._safe_container_path("main.py") == "/workspace/main.py"
    assert s._safe_container_path("pkg/utils.py") == "/workspace/pkg/utils.py"
    assert s._safe_container_path("/etc/passwd") == "/workspace/etc/passwd"  # stripped leading slash
