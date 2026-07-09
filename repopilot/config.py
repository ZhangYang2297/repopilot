from __future__ import annotations
import os
from dataclasses import dataclass, field, fields, asdict
from pathlib import Path
from typing import Optional

import yaml  # using PyYAML-compatible via pyyaml if available; fallback tomllib

# Tomllib is in 3.11+; use tomli for 3.10
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore


def _expand(p: str | Path) -> Path:
    return Path(p).expanduser().resolve()


CONFIG_FILENAME = "config.toml"


@dataclass
class Settings:
    """Global settings for RepoPilot, loaded from env > config file > defaults."""

    # LLM — these are the ONLY required fields; first-run wizard will prompt for them
    model: str = ""                          # LiteLLM format: provider/model e.g. openai/gpt-4o-mini
    fast_model: str = ""                     # cheap model for plan/reflect/compact
    strong_model: str = ""                   # strong model for final answer
    api_key: str = ""                        # API key (sk-...); empty means "use env var"
    base_url: str = ""                       # Custom base URL (for ARK/DashScope/vLLM/local)

    # Sandbox
    sandbox_type: str = "docker"             # docker | local
    docker_image: str = "repopilot-sandbox:py310"
    docker_cpu_quota: int = 200000           # cgroup cpu_quota (2 CPUs when period=100000)
    docker_mem_limit: str = "2g"
    docker_network: str = "bridge"           # bridge | none
    local_cwd: str = ""

    # Permission
    approval_mode: str = "confirm"           # auto | confirm | edit-only | deny

    # Agent
    max_steps: int = 50
    budget_tokens: int = 200_000
    tool_timeout: int = 30
    reflect_every: int = 5
    max_consecutive_failures: int = 5

    # Paths (not persisted to config.toml — always derived)
    home_dir: Path = field(default_factory=lambda: _expand(
        os.environ.get("REPOPILOT_HOME", "~/.repopilot")))

    # Streaming / cost
    stream: bool = True
    cost_tracking: bool = True

    # Compaction
    compact_micro_ratio: float = 0.7
    compact_auto_ratio: float = 0.85
    recent_keep_steps: int = 10

    def __post_init__(self):
        # Ensure home_dir is always a Path (env override may pass str)
        if not isinstance(self.home_dir, Path):
            self.home_dir = _expand(self.home_dir)
        # Coerce Path for any path-like strings (defensive, in case TOML loads strings)
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
        for f in (self.model, self.fast_model, self.strong_model):
            if f and "/" not in f:
                raise ValueError(
                    f"model must be in 'provider/model' format (LiteLLM), got {f!r}. "
                    f"Examples: openai/gpt-4o-mini, openai/qwen2.5-coder-32b-instruct"
                )

    # ── directory helpers ──────────────────────────────────
    @property
    def sessions_dir(self) -> Path: return self.home_dir / "sessions"
    @property
    def state_db_path(self) -> Path: return self.home_dir / "state.sqlite"
    @property
    def jobs_db_path(self) -> Path: return self.home_dir / "jobs.sqlite"
    @property
    def memories_path(self) -> Path: return self.home_dir / "memories.md"
    @property
    def skills_dir(self) -> Path: return self.home_dir / "skills"
    @property
    def cache_dir(self) -> Path: return self.home_dir / "cache"
    @property
    def logs_dir(self) -> Path: return self.home_dir / "logs"
    @property
    def config_file(self) -> Path: return self.home_dir / CONFIG_FILENAME

    def ensure_dirs(self) -> None:
        for d in (self.home_dir, self.sessions_dir, self.skills_dir,
                  self.cache_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ── TOML persistence ──────────────────────────────────
    def is_configured(self) -> bool:
        """True if the user has provided a model (API key may come from env)."""
        return bool(self.model)

    _ENV_MAP = {
        "model":            ("REPOPILOT_MODEL",            str),
        "fast_model":       ("REPOPILOT_FAST_MODEL",       str),
        "strong_model":     ("REPOPILOT_STRONG_MODEL",     str),
        "api_key":          ("REPOPILOT_API_KEY",          str),
        "base_url":         ("REPOPILOT_BASE_URL",         str),
        "sandbox_type":     ("REPOPILOT_SANDBOX",          str),
        "docker_image":     ("REPOPILOT_DOCKER_IMAGE",     str),
        "approval_mode":    ("REPOPILOT_APPROVAL_MODE",    str),
        "max_steps":        ("REPOPILOT_MAX_STEPS",        int),
        "budget_tokens":    ("REPOPILOT_BUDGET_TOKENS",    int),
        "tool_timeout":     ("REPOPILOT_TOOL_TIMEOUT",     int),
        "home_dir":         ("REPOPILOT_HOME",             str),
        "docker_network":   ("REPOPILOT_DOCKER_NETWORK",   str),
        "docker_mem_limit": ("REPOPILOT_DOCKER_MEM",       str),
        "stream":           ("REPOPILOT_STREAM",           lambda v: v.lower() in ("1","true","yes")),
        "cost_tracking":    ("REPOPILOT_COST",             lambda v: v.lower() in ("1","true","yes")),
    }

    _TOML_SECTION = "core"   # everything under [core] in config.toml
    _PERSISTED_FIELDS = {    # which fields go into config.toml
        "model", "fast_model", "strong_model", "api_key", "base_url",
        "sandbox_type", "docker_image", "docker_network", "docker_mem_limit",
        "approval_mode", "max_steps", "budget_tokens", "tool_timeout",
        "stream", "cost_tracking",
    }

    def save(self) -> None:
        """Persist non-default settings to ~/.repopilot/config.toml."""
        self.ensure_dirs()
        defaults = Settings()
        lines = [f"# RepoPilot configuration (edit or run `repopilot config`)",
                 f"[{self._TOML_SECTION}]", ""]
        d = asdict(self)
        for f in fields(self):
            if f.name not in self._PERSISTED_FIELDS:
                continue
            val = d[f.name]
            if val == getattr(defaults, f.name):
                continue  # don't write defaults
            if isinstance(val, bool):
                lines.append(f"{f.name} = {'true' if val else 'false'}")
            elif isinstance(val, (int, float)):
                lines.append(f"{f.name} = {val}")
            elif isinstance(val, str):
                if val:
                    escaped = val.replace("\\", "\\\\").replace('"', '\\"')
                    lines.append(f'{f.name} = "{escaped}"')
        self.config_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, **overrides) -> "Settings":
        """Load: hardcoded defaults <-- config.toml <-- env vars <-- overrides.

        Important: home_dir must be resolved FIRST so we read the right config.toml.
        """
        # Step 0: resolve home_dir (default <-- env <-- override)
        home_raw = overrides.get("home_dir") or os.environ.get("REPOPILOT_HOME", "~/.repopilot")
        home = _expand(home_raw)

        # Step 1: defaults
        s = cls()
        s.home_dir = home
        s.ensure_dirs()

        # Step 2: config.toml (if exists at resolved home_dir)
        if s.config_file.exists():
            try:
                with open(s.config_file, "rb") as fh:
                    data = tomllib.load(fh)
                core = data.get(cls._TOML_SECTION, {})
                for f in fields(cls):
                    if f.name in core and f.name in cls._PERSISTED_FIELDS:
                        setattr(s, f.name, core[f.name])
            except Exception:
                pass

        # Step 3: env vars
        for f_name, (env_key, converter) in cls._ENV_MAP.items():
            raw = os.environ.get(env_key)
            if raw is not None and raw != "":
                try:
                    setattr(s, f_name, converter(raw))
                except (ValueError, TypeError):
                    pass

        # Step 4: explicit overrides
        for k, v in overrides.items():
            if hasattr(s, k):
                setattr(s, k, v)

        # Final normalize
        s.__post_init__()
        # Auto-derive fast/strong if only model is set
        if s.model and not s.fast_model:
            s.fast_model = s.model
        if s.model and not s.strong_model:
            s.strong_model = s.model

        s.ensure_dirs()
        return s



_settings: Optional["Settings"] = None


def get_settings() -> Settings:
    """Return the global Settings (lazy-loaded, cached)."""
    global _settings
    if _settings is None:
        _settings = Settings.load()
    return _settings


def reset_settings_for_tests() -> None:
    global _settings
    _settings = None
