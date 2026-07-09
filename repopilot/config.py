from __future__ import annotations
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional


def _expand(p: str | Path) -> Path:
    return Path(p).expanduser().resolve()


@dataclass
class Settings:
    """Global settings for RepoPilot, loaded from env vars with sensible defaults."""

    # LLM
    model: str = "openai/qwen2.5-coder-32b-instruct"
    fast_model: str = "openai/qwen2.5-coder-7b-instruct"
    strong_model: str = "openai/qwen2.5-coder-32b-instruct"

    # Sandbox
    sandbox_type: str = "docker"  # docker | local
    docker_image: str = "repopilot-sandbox:py310"
    docker_cpu_quota: int = 200000  # cgroup cpu_quota (2 CPUs when period=100000)
    docker_mem_limit: str = "2g"
    docker_network: str = "bridge"  # bridge | none
    local_cwd: str = ""  # locked cwd for local mode

    # Permission
    approval_mode: str = "confirm"  # auto | confirm | edit-only | deny

    # Agent
    max_steps: int = 50
    budget_tokens: int = 200_000
    tool_timeout: int = 30  # per-tool seconds
    reflect_every: int = 5  # reflect every N steps
    max_consecutive_failures: int = 5

    # Paths
    home_dir: Path = field(default_factory=lambda: _expand(
        os.environ.get("REPOPILOT_HOME", "~/.repopilot")))

    # Streaming
    stream: bool = True

    # Cost tracking
    cost_tracking: bool = True

    # Compaction thresholds
    compact_micro_ratio: float = 0.7  # micro-compact when token usage > 70%
    compact_auto_ratio: float = 0.85  # auto-compact when > 85%
    recent_keep_steps: int = 10  # always keep last N steps uncompressed

    def __post_init__(self):
        self.home_dir = _expand(self.home_dir)
        if self.sandbox_type not in ("docker", "local"):
            raise ValueError(
                f"sandbox_type must be docker|local, got {self.sandbox_type!r}"
            )
        if self.approval_mode not in ("auto", "confirm", "edit-only", "deny"):
            raise ValueError(f"invalid approval_mode: {self.approval_mode!r}")
        if self.max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        if self.budget_tokens < 1000:
            raise ValueError("budget_tokens must be >= 1000")

    # ── directory helpers ──────────────────────────────────
    @property
    def sessions_dir(self) -> Path:
        return self.home_dir / "sessions"

    @property
    def state_db_path(self) -> Path:
        return self.home_dir / "state.sqlite"

    @property
    def jobs_db_path(self) -> Path:
        return self.home_dir / "jobs.sqlite"

    @property
    def memories_path(self) -> Path:
        return self.home_dir / "memories.md"

    @property
    def skills_dir(self) -> Path:
        return self.home_dir / "skills"

    @property
    def cache_dir(self) -> Path:
        return self.home_dir / "cache"

    @property
    def logs_dir(self) -> Path:
        return self.home_dir / "logs"

    def ensure_dirs(self) -> None:
        """Create all required directories under home_dir."""
        for d in (
            self.home_dir, self.sessions_dir, self.skills_dir,
            self.cache_dir, self.logs_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    # ── loader ─────────────────────────────────────────────
    _ENV_MAP = {
        "model": ("REPOPILOT_MODEL", str),
        "fast_model": ("REPOPILOT_FAST_MODEL", str),
        "strong_model": ("REPOPILOT_STRONG_MODEL", str),
        "sandbox_type": ("REPOPILOT_SANDBOX", str),
        "docker_image": ("REPOPILOT_DOCKER_IMAGE", str),
        "approval_mode": ("REPOPILOT_APPROVAL_MODE", str),
        "max_steps": ("REPOPILOT_MAX_STEPS", int),
        "budget_tokens": ("REPOPILOT_BUDGET_TOKENS", int),
        "tool_timeout": ("REPOPILOT_TOOL_TIMEOUT", int),
        "home_dir": ("REPOPILOT_HOME", str),
        "docker_network": ("REPOPILOT_DOCKER_NETWORK", str),
        "docker_mem_limit": ("REPOPILOT_DOCKER_MEM", str),
        "stream": ("REPOPILOT_STREAM", lambda v: v.lower() in ("1", "true", "yes")),
        "cost_tracking": ("REPOPILOT_COST", lambda v: v.lower() in ("1", "true", "yes")),
    }

    @classmethod
    def load(cls, **overrides) -> "Settings":
        """Load settings from env vars, apply overrides, ensure dirs exist."""
        kwargs: dict = {}
        for field_name, (env_key, converter) in cls._ENV_MAP.items():
            raw = os.environ.get(env_key)
            if raw is not None and raw != "":
                try:
                    kwargs[field_name] = converter(raw)
                except (ValueError, TypeError):
                    pass  # ignore bad env values, fall back to default
        kwargs.update(overrides)
        s = cls(**kwargs)
        s.ensure_dirs()
        return s


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Return the global Settings instance (lazy-loaded, cached)."""
    global _settings
    if _settings is None:
        _settings = Settings.load()
    return _settings


def reset_settings_for_tests() -> None:
    """Reset cached settings (call in test setup to get fresh state)."""
    global _settings
    _settings = None
