from __future__ import annotations
from typer.testing import CliRunner
from repopilot.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "RepoPilot" in result.output


def test_chat_todo_message():
    result = runner.invoke(app, ["chat", "hello world", "--repo", "."])
    assert result.exit_code == 0
    assert "TODO" in result.output
