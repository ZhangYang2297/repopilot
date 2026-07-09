from __future__ import annotations
import os
import pytest
from pathlib import Path
from repopilot.config import Settings, get_settings, reset_settings_for_tests


def test_defaults():
    s = Settings()
    # Model is empty by default (user must configure)
    assert s.model == ""
    assert s.sandbox_type in ("docker", "local")
    assert s.approval_mode == "confirm"
    assert s.max_steps == 50
    assert s.budget_tokens == 200_000
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


def test_model_requires_provider_prefix():
    with pytest.raises(ValueError, match="provider/model"):
        Settings(model="gpt-4o")  # missing provider/
    # Valid format works
    s = Settings(model="openai/gpt-4o")
    assert s.model == "openai/gpt-4o"


def test_home_dir_expands_user():
    s = Settings(home_dir="~/.repopilot_test")
    assert str(s.home_dir).startswith(str(Path.home()))
    assert "~" not in str(s.home_dir)


def test_env_override(monkeypatch, tmp_path):
    reset_settings_for_tests()
    monkeypatch.setenv("REPOPILOT_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("REPOPILOT_MAX_STEPS", "10")
    monkeypatch.setenv("REPOPILOT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("REPOPILOT_API_KEY", "sk-test1234")
    s = Settings.load()
    assert s.model == "openai/gpt-4o-mini"
    assert s.max_steps == 10
    assert s.api_key == "sk-test1234"
    assert s.home_dir == (tmp_path / "home").resolve()
    reset_settings_for_tests()


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
    assert s.config_file.name == "config.toml"


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


def test_is_configured():
    s = Settings()
    assert s.is_configured() is False
    s.model = "openai/gpt-4o"
    assert s.is_configured() is True


def test_save_and_load_persists(tmp_path):
    s = Settings(
        home_dir=str(tmp_path / "rp"),
        model="openai/test-model",
        api_key="sk-abcdefghijklmnop",
        base_url="https://api.example.com/v1",
        sandbox_type="local",
        approval_mode="auto",
    )
    s.ensure_dirs()
    s.save()
    assert s.config_file.exists()
    toml_text = s.config_file.read_text()
    assert "model" in toml_text
    assert "sk-abc" in toml_text  # key is stored
    # Load fresh
    reset_settings_for_tests()
    s2 = Settings.load(home_dir=str(tmp_path / "rp"))
    assert s2.model == "openai/test-model"
    assert s2.api_key == "sk-abcdefghijklmnop"
    assert s2.base_url == "https://api.example.com/v1"
    assert s2.sandbox_type == "local"
    assert s2.approval_mode == "auto"
    # Fast/strong auto-derive from model
    assert s2.fast_model == "openai/test-model"
    assert s2.strong_model == "openai/test-model"
    reset_settings_for_tests()


def test_save_does_not_persist_defaults(tmp_path):
    s = Settings(home_dir=str(tmp_path / "rp2"), model="openai/x")
    s.save()
    text = s.config_file.read_text()
    # Default values like max_steps=50 should not be written
    assert "max_steps" not in text
    assert "budget_tokens" not in text
    assert "model" in text


def test_env_overrides_config_file(tmp_path, monkeypatch):
    s = Settings(home_dir=str(tmp_path / "rp3"), model="openai/file-model", api_key="sk-file")
    s.ensure_dirs()
    s.save()
    monkeypatch.setenv("REPOPILOT_MODEL", "openai/env-model")
    reset_settings_for_tests()
    s2 = Settings.load(home_dir=str(tmp_path / "rp3"))
    assert s2.model == "openai/env-model"  # env wins
    reset_settings_for_tests()
