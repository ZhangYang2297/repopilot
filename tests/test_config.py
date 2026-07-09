from __future__ import annotations
import os
import pytest
from pathlib import Path
from repopilot.config import Settings, get_settings, reset_settings_for_tests


def test_defaults():
    s = Settings()
    assert s.max_steps == 50
    assert s.budget_tokens == 200_000
    assert s.approval_mode == "confirm"
    assert s.sandbox_type in ("docker", "local")
    assert s.model.startswith("openai/") or "/" in s.model
    assert s.fast_model != ""
    assert s.strong_model != ""
    assert isinstance(s.home_dir, Path)
    assert s.compact_micro_ratio == 0.7
    assert s.compact_auto_ratio == 0.85
    assert s.recent_keep_steps == 10


def test_invalid_sandbox_raises():
    with pytest.raises(ValueError, match="sandbox_type"):
        Settings(sandbox_type="kubernetes")


def test_invalid_approval_mode_raises():
    with pytest.raises(ValueError, match="approval_mode"):
        Settings(approval_mode="admin")


def test_home_dir_expands_user():
    s = Settings(home_dir="~/.repopilot_test")
    assert str(s.home_dir).startswith(str(Path.home()))
    assert "~" not in str(s.home_dir)


def test_env_override(monkeypatch, tmp_path):
    reset_settings_for_tests()
    monkeypatch.setenv("REPOPILOT_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("REPOPILOT_MAX_STEPS", "10")
    monkeypatch.setenv("REPOPILOT_HOME", str(tmp_path / "home"))
    s = Settings.load()
    assert s.model == "openai/gpt-4o-mini"
    assert s.max_steps == 10
    assert s.home_dir == (tmp_path / "home").resolve()


def test_env_int_conversion(monkeypatch):
    reset_settings_for_tests()
    monkeypatch.setenv("REPOPILOT_BUDGET_TOKENS", "50000")
    s = Settings.load()
    assert s.budget_tokens == 50000
    reset_settings_for_tests()


def test_env_bool_conversion(monkeypatch):
    reset_settings_for_tests()
    monkeypatch.setenv("REPOPILOT_STREAM", "0")
    s = Settings.load()
    assert s.stream is False
    reset_settings_for_tests()


def test_ensure_dirs_creates_directories(tmp_path):
    s = Settings(home_dir=str(tmp_path / "rp"))
    s.ensure_dirs()
    assert (tmp_path / "rp" / "sessions").is_dir()
    assert (tmp_path / "rp" / "skills").is_dir()
    assert (tmp_path / "rp" / "cache").is_dir()
    assert (tmp_path / "rp" / "logs").is_dir()


def test_directory_properties(tmp_path):
    s = Settings(home_dir=str(tmp_path / "rp"))
    assert s.sessions_dir == (tmp_path / "rp" / "sessions").resolve()
    assert s.state_db_path.name == "state.sqlite"
    assert s.jobs_db_path.name == "jobs.sqlite"
    assert s.memories_path.name == "memories.md"
    assert s.skills_dir.name == "skills"
    assert s.cache_dir.name == "cache"


def test_get_settings_cached(monkeypatch, tmp_path):
    reset_settings_for_tests()
    monkeypatch.setenv("REPOPILOT_HOME", str(tmp_path / "cached"))
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
    reset_settings_for_tests()


def test_invalid_max_steps_raises():
    with pytest.raises(ValueError):
        Settings(max_steps=0)


def test_invalid_budget_raises():
    with pytest.raises(ValueError):
        Settings(budget_tokens=100)
