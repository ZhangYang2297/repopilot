from __future__ import annotations
from typer.testing import CliRunner
from unittest.mock import patch
from repopilot.cli import app
from repopilot.config import reset_settings_for_tests

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "RepoPilot" in result.output


def test_chat_todo_message():
    reset_settings_for_tests()
    # Pretend user already has a model configured so wizard doesn't run
    with patch("repopilot.cli.get_settings") as mock_gs:
        from repopilot.config import Settings
        mock_s = Settings(model="openai/test", fast_model="openai/test", strong_model="openai/test")
        mock_s.is_configured = lambda: True
        mock_gs.return_value = mock_s
        result = runner.invoke(app, ["chat", "hello world", "--repo", "."])
    assert result.exit_code == 0
    assert "TODO" in result.output or "hello world" in result.output
