from __future__ import annotations
from typer.testing import CliRunner
from unittest.mock import patch, MagicMock
from repopilot.cli import app
from repopilot.config import reset_settings_for_tests

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "RepoPilot" in result.output


def test_chat_command():
    """Test that chat command invokes the agent loop (mocked)."""
    reset_settings_for_tests()
    from repopilot.config import Settings
    mock_s = Settings(model="openai/test", fast_model="openai/test", strong_model="openai/test",
                      api_key="sk-test", base_url="https://example.com/v1",
                      sandbox_type="local")
    mock_s.is_configured = lambda: True
    fake_result = MagicMock()
    fake_result.status = "completed"
    fake_result.summary = "done"
    fake_result.steps = 1
    fake_result.trajectory = []
    fake_result.total_tokens = 100
    fake_result.duration_ms = 500
    fake_result.session_id = "abc"
    fake_result.error = ""

    mock_local = MagicMock()
    mock_local.__enter__ = MagicMock(return_value=mock_local)
    mock_local.__exit__ = MagicMock(return_value=False)

    with patch("repopilot.cli.get_settings", return_value=mock_s), \
         patch("repopilot.agent.loop.run_agent", return_value=fake_result), \
         patch("repopilot.llm.service.build_llm_from_settings", return_value=MagicMock()), \
         patch("repopilot.sandbox.LocalSandbox", return_value=mock_local), \
         patch("repopilot.session.store.SessionStore", return_value=MagicMock()):
        result = runner.invoke(app, ["chat", "hello", "--repo", ".", "--sandbox", "local", "--approval-mode", "auto"])
    assert result.exit_code == 0, f"Output: {result.output}\nExc: {result.exception}"
    assert "Result" in result.output or "done" in result.output
