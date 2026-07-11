# RepoPilot Phase 1 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建 Code Agent 核心骨架，能在 Docker 沙箱内对 Python 项目自主完成简单代码修改任务（如"修复failing test"、"给函数加参数"），带 streaming 输出、权限审批、trajectory 记录。

> **📊 当前进度（2026-07-11更新）**：Task 1-5 基础版本已完成并通过64个单元测试；Task 6-15 待开发。
> 实际实现路径对原计划有轻微调整：先完成核心基础层（Config/LLM/Sandbox），再叠加Permission/Tool/Hook/AgentLoop等上层模块。
> 项目路径：`C:\Users\admin\Documents\RepoPilot`
> 文档目录：`C:\Users\admin\Documents\RepoPilot\docs\`

**Architecture:** Plan-Act-Reflect 主循环 + LiteLLM 多后端 + Docker/Local 双沙箱 + Permission Engine 四模式 + Hook 生命周期 + tree-sitter Repo Map + grep/glob/read/edit/write/bash/run_python 工具集 + JSONL/SQLite 会话存储 + Typer/Rich CLI + 实时 cost 追踪。

**Tech Stack:** Python 3.10+, uv, Typer, Rich, LiteLLM, tree-sitter, Docker SDK, structlog, pytest, Textual（Phase 2 才用，Phase 1 不装）。单租户本地 CLI，**无向量库/无 RAG/无 embedding**。

**Spec:** `docs/superpowers/specs/2026-07-09-code-agent-design.md`

## Global Constraints

- Python 版本：≥3.10，用 `from __future__ import annotations`
- 包名：`repopilot`，所有代码在 `repopilot/` 下
- 打包：`pyproject.toml` 用 `[project.scripts] repopilot = "repopilot.cli:app"`
- 代码风格：用 ruff（line-length 100），类型注解必须
- **禁止引入**：chromadb、sentence-transformers、rank-bm25、faiss、langchain、langgraph、autogen、crewai
- LLM 调用统一走 LiteLLM `litellm.acompletion`（流式）/ `litellm.completion`（非流式）
- 所有路径操作必须用 `pathlib.Path`
- 日志用 structlog，JSON 格式
- 测试文件和生产文件一一对应，测试函数命名 `test_<function>_<scenario>`
- 沙箱默认 Docker，Local 模式仅在 `--sandbox local` 或 config 中显式开启
- 数据库/SQLite 文件放 `~/.repopilot/`（用户目录），session JSONL 也放那里
- Windows/Linux/macOS 都要支持（Docker Desktop on Windows 用 Linux 容器）
- 默认模型通过环境变量配置：`REPOPILOT_MODEL`，fast/strong 可分设

---

## Phase 1 Task Map（14 个 Task，预计 2 周）

| Task | 名称 | 依赖 |
|------|------|------|
| T1 | 项目脚手架 + pyproject.toml + CLI 骨架 | — |
| T2 | 配置与全局常量 | T1 |
| T3 | LLM Service（LiteLLM + 三档 + streaming + 熔断）| T2 |
| T4 | Sandbox 抽象基类 + Docker 实现 | T2 |
| T5 | Local Sandbox 实现 | T4 |
| T6 | Permission Engine + 命令黑白名单 + approver | T2 |
| T7 | Tool 基类 + ToolRegistry + 工具结果截断 | T4 |
| T8 | 核心工具：read/write/edit/grep/glob/list_dir | T7,T4 |
| T9 | 核心工具：bash/run_python/finish | T7,T4,T6 |
| T10 | Hook Manager + 内置 hooks（cost/log）| T2 |
| T11 | tree-sitter + Repo Map 构建 | T4 |
| T12 | Session Store（JSONL + SQLite 索引）| T2 |
| T13 | Context Manager + 三级压缩（tool-compact 为纯规则，micro/auto 用 fast LLM）| T3,T12 |
| T14 | Agent Loop（Plan-Act-Reflect）+ Planner/Reflector/Parser + CLI 接入 streaming | T3,T8,T9,T10,T11,T12,T13 |
| T15 | Prompt 调优 + E2E 测试验证 | T14 |

---

### Task 1: 项目脚手架 + CLI 骨架

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `repopilot/__init__.py`
- Create: `repopilot/cli.py`
- Create: `tests/__init__.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: 用 uv init 创建项目骨架**

```bash
cd <your-projects-dir>
mkdir RepoPilot
cd RepoPilot
uv init --package --name repopilot --python 3.10
```

- [ ] **Step 2: 编辑 pyproject.toml（覆盖 uv init 的默认内容）**

```toml
[project]
name = "repopilot"
version = "0.1.0"
description = "Local-first code agent with Docker sandbox and SWE-bench harness"
requires-python = ">=3.10"
license = {text = "MIT"}
dependencies = [
    "typer>=0.12",
    "rich>=13",
    "prompt-toolkit>=3.0",
    "litellm>=1.40",
    "openai>=1.30",
    "docker>=7.0",
    "tree-sitter>=0.21",
    "tree-sitter-python>=0.21",
    "pathspec>=0.12",
    "structlog>=24.0",
    "pyyaml>=6.0",
    "jinja2>=3.1",
    "httpx>=0.27",
    "diskcache>=5.6",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-timeout",
    "pytest-cov",
    "ruff>=0.5",
    "ipython",
]

[project.scripts]
repopilot = "repopilot.cli:app"

[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v --timeout=30"
```

- [ ] **Step 3: 创建 .gitignore**

```
__pycache__/
*.pyc
.venv/
dist/
*.egg-info/
.pytest_cache/
.ruff_cache/
*.db
*.db-wal
*.db-shm
sessions/
outputs/
reports/
.cache/
.env
.mypy_cache/
```

- [ ] **Step 4: 创建 .env.example**

```
# LLM API Keys
OPENAI_API_KEY=
DASHSCOPE_API_KEY=
ARK_API_KEY=
ANTHROPIC_API_KEY=
BRAVE_SEARCH_API_KEY=

# Default model (LiteLLM format: provider/model)
REPOPILOT_MODEL=openai/qwen2.5-coder-32b-instruct
REPOPILOT_FAST_MODEL=openai/qwen2.5-coder-7b-instruct
REPOPILOT_STRONG_MODEL=openai/qwen2.5-coder-32b-instruct

# Sandbox
REPOPILOT_SANDBOX=docker  # docker | local
REPOPILOT_DOCKER_IMAGE=repopilot-sandbox:py310

# Config
REPOPILOT_HOME=~/.repopilot
```

- [ ] **Step 5: 创建 repopilot/__init__.py**

```python
from __future__ import annotations
__version__ = "0.1.0"
```

- [ ] **Step 6: 创建 CLI 骨架 repopilot/cli.py**

```python
from __future__ import annotations
import typer
from rich.console import Console

app = typer.Typer(
    name="repopilot",
    help="RepoPilot — Local-first code agent.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


@app.command()
def version() -> None:
    """Show version."""
    from repopilot import __version__
    console.print(f"[bold green]RepoPilot[/bold green] v{__version__}")


@app.command()
def chat(
    task: str = typer.Argument(..., help="Task to perform"),
    repo: str = typer.Option(".", "--repo", "-r", help="Path to target repo"),
    model: str = typer.Option("", "--model", "-m", help="Override model"),
    sandbox: str = typer.Option("", "--sandbox", help="docker|local"),
    approval_mode: str = typer.Option("confirm", "--approval-mode",
                                      help="auto|confirm|edit-only|deny"),
    max_steps: int = typer.Option(50, "--max-steps", help="Max agent steps"),
    budget_tokens: int = typer.Option(200_000, "--budget-tokens",
                                      help="Input token budget"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run agent on a task in a repo."""
    console.print(f"[yellow]TODO:[/yellow] agent loop not yet implemented")
    console.print(f"  task={task!r} repo={repo} model={model!r} sandbox={sandbox!r}")


if __name__ == "__main__":
    app()
```

- [ ] **Step 7: 创建 tests/test_cli.py**

```python
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
```

- [ ] **Step 8: 安装依赖并跑测试**

```bash
uv sync --group dev
uv run pytest tests/ -v
```

Expected: 2 tests PASS.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat(t1): scaffold project with Typer CLI skeleton"
```

---

### Task 2: 配置与全局常量

**Files:**
- Create: `repopilot/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings` dataclass（供 T3/T4/T6 读取 model/sandbox/home 路径等配置）
- Produces: `REPOPILOT_HOME`, `MODEL_DEFAULTS`, `SANDBOX_DEFAULTS` 常量

- [ ] **Step 1: 写 failing test**

`tests/test_config.py`:
```python
from __future__ import annotations
import os
from pathlib import Path
from repopilot.config import Settings, get_settings


def test_defaults_are_set():
    s = Settings()
    assert s.max_steps == 50
    assert s.budget_tokens == 200_000
    assert s.approval_mode == "confirm"
    assert s.sandbox_type in ("docker", "local")
    assert isinstance(s.home_dir, Path)


def test_env_override(monkeypatch):
    monkeypatch.setenv("REPOPILOT_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("REPOPILOT_MAX_STEPS", "10")
    s = Settings.load()
    assert s.model == "openai/gpt-4o-mini"
    assert s.max_steps == 10


def test_home_dir_expands_user():
    s = Settings(home_dir="~/.repopilot_test")
    assert str(s.home_dir).startswith(str(Path.home()))
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_config.py -v
```
Expected: FAIL (module not found).

- [ ] **Step 3: 实现 repopilot/config.py**

```python
from __future__ import annotations
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional


def _expand(p: str | Path) -> Path:
    return Path(p).expanduser().resolve()


@dataclass
class Settings:
    # LLM
    model: str = "openai/qwen2.5-coder-32b-instruct"
    fast_model: str = "openai/qwen2.5-coder-7b-instruct"
    strong_model: str = "openai/qwen2.5-coder-32b-instruct"

    # Sandbox
    sandbox_type: str = "docker"  # docker | local
    docker_image: str = "repopilot-sandbox:py310"
    docker_cpu_quota: int = 200000   # 2 CPUs (cgroup cpu_period=100000)
    docker_mem_limit: str = "2g"
    docker_network: str = "bridge"   # bridge | none
    local_cwd: str = ""              # locked cwd for local mode

    # Permission
    approval_mode: str = "confirm"   # auto | confirm | edit-only | deny

    # Agent
    max_steps: int = 50
    budget_tokens: int = 200_000
    tool_timeout: int = 30           # per-tool seconds
    reflect_every: int = 5           # reflect every N steps
    max_consecutive_failures: int = 5

    # Paths
    home_dir: Path = field(default_factory=lambda: _expand(
        os.environ.get("REPOPILOT_HOME", "~/.repopilot")))

    # Streaming
    stream: bool = True

    # Cost tracking
    cost_tracking: bool = True

    def __post_init__(self):
        self.home_dir = _expand(self.home_dir)
        if self.sandbox_type not in ("docker", "local"):
            raise ValueError(f"sandbox_type must be docker|local, got {self.sandbox_type!r}")
        if self.approval_mode not in ("auto", "confirm", "edit-only", "deny"):
            raise ValueError(f"invalid approval_mode: {self.approval_mode!r}")

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

    def ensure_dirs(self) -> None:
        for d in (self.home_dir, self.sessions_dir, self.skills_dir, self.cache_dir):
            d.mkdir(parents=True, exist_ok=True)

    @classmethod
    def load(cls, **overrides) -> "Settings":
        env_map = {
            "model": "REPOPILOT_MODEL",
            "fast_model": "REPOPILOT_FAST_MODEL",
            "strong_model": "REPOPILOT_STRONG_MODEL",
            "sandbox_type": "REPOPILOT_SANDBOX",
            "docker_image": "REPOPILOT_DOCKER_IMAGE",
            "approval_mode": "REPOPILOT_APPROVAL_MODE",
            "max_steps": "REPOPILOT_MAX_STEPS",
            "budget_tokens": "REPOPILOT_BUDGET_TOKENS",
            "home_dir": "REPOPILOT_HOME",
        }
        kwargs: dict = {}
        for f in fields(cls):
            env = env_map.get(f.name)
            if env and os.environ.get(env):
                val = os.environ[env]
                if f.type in ("int", int):
                    val = int(val)
                kwargs[f.name] = val
        kwargs.update(overrides)
        s = cls(**kwargs)
        s.ensure_dirs()
        return s


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.load()
    return _settings


def reset_settings_for_tests() -> None:
    global _settings
    _settings = None
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_config.py -v
```
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add repopilot/config.py tests/test_config.py
git commit -m "feat(t2): Settings with env override and paths"
```

---

### Task 3: LLM Service（LiteLLM + 三档路由 + streaming + 熔断）

**Files:**
- Create: `repopilot/llm/__init__.py`
- Create: `repopilot/llm/service.py`
- Create: `repopilot/llm/circuit_breaker.py`
- Create: `repopilot/llm/stream_handler.py`
- Test: `tests/test_llm.py`
- Test: `tests/test_circuit_breaker.py`

**Interfaces:**
- Consumes: `Settings` from T2
- Produces:
  - `class LLMService` with methods:
    - `chat(messages, tools=None, temperature=0.3, tier="default", stream=False) -> LLMResponse`
    - `async achat(...) -> AsyncIterator[StreamEvent]` (streaming)
    - `chat_fast(system, user, **kw) -> str`
    - `chat_strong(system, user, **kw) -> str`
  - `class CircuitBreaker` (sliding window 20 calls, 50% error rate → open, 60s cooldown → half-open)
  - `class StreamEvent` (TypedDict: `type: "text"|"tool_call"|"done"`, `content`, `tool_calls`)
  - `class LLMResponse` (dataclass: `content`, `tool_calls`, `usage`, `model`)

- [ ] **Step 1: 先写 CircuitBreaker 测试**

`tests/test_circuit_breaker.py`:
```python
from __future__ import annotations
import time
from repopilot.llm.circuit_breaker import CircuitBreaker, CircuitOpenError


def test_circuit_starts_closed():
    cb = CircuitBreaker(window=10, min_calls=3, error_rate=0.5, cooldown=0.1)
    assert cb.allow_request() is True


def test_circuit_opens_after_threshold():
    cb = CircuitBreaker(window=10, min_calls=3, error_rate=0.5, cooldown=10)
    for _ in range(5):
        cb.record_failure()
    assert cb.allow_request() is False


def test_circuit_half_open_after_cooldown():
    cb = CircuitBreaker(window=10, min_calls=2, error_rate=0.5, cooldown=0.05)
    cb.record_failure(); cb.record_failure()
    assert cb.allow_request() is False
    time.sleep(0.1)
    # After cooldown, one probe is allowed (half-open)
    assert cb.allow_request() is True
    cb.record_success()  # probe succeeds → close again
    assert cb.allow_request() is True
```

- [ ] **Step 2: 实现 repopilot/llm/circuit_breaker.py**

```python
from __future__ import annotations
import time
import threading
from collections import deque
from dataclasses import dataclass


class CircuitOpenError(Exception):
    pass


@dataclass
class CircuitBreaker:
    window: int = 20
    min_calls: int = 5
    error_rate: float = 0.5
    cooldown: float = 60.0
    _state: str = "closed"  # closed | open | half-open
    _opened_at: float = 0.0

    def __post_init__(self):
        self._calls: deque = deque(maxlen=self.window)  # 1=success, 0=failure
        self._lock = threading.Lock()

    def allow_request(self) -> bool:
        with self._lock:
            if self._state == "closed":
                return True
            if self._state == "open":
                if time.time() - self._opened_at >= self.cooldown:
                    self._state = "half-open"
                    return True
                return False
            # half-open: allow exactly one probe
            return True

    def record_success(self):
        with self._lock:
            if self._state == "half-open":
                self._state = "closed"
                self._calls.clear()
            self._calls.append(1)

    def record_failure(self):
        with self._lock:
            self._calls.append(0)
            if self._state == "half-open":
                self._state = "open"
                self._opened_at = time.time()
                return
            if len(self._calls) >= self.min_calls:
                failures = sum(1 for c in self._calls if c == 0)
                if failures / len(self._calls) >= self.error_rate:
                    self._state = "open"
                    self._opened_at = time.time()

    def reset(self):
        with self._lock:
            self._calls.clear()
            self._state = "closed"
```

- [ ] **Step 3: 跑 CircuitBreaker 测试确认通过**

```bash
uv run pytest tests/test_circuit_breaker.py -v
```
Expected: 3 PASS.

- [ ] **Step 4: 写 LLMService 测试（mock litellm）**

`tests/test_llm.py`:
```python
from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock
from repopilot.llm.service import LLMService, LLMResponse, Tier


def _fake_completion(**kwargs):
    msg = MagicMock()
    msg.content = "Hello"
    msg.tool_calls = None
    choice = MagicMock(); choice.message = msg
    resp = MagicMock(); resp.choices = [choice]
    usage = MagicMock(); usage.prompt_tokens = 100; usage.completion_tokens = 50
    resp.usage = usage
    return resp


@patch("repopilot.llm.service.litellm.completion", _fake_completion)
def test_chat_returns_response():
    svc = LLMService(model="openai/fake", fast_model="openai/fake", strong_model="openai/fake")
    r = svc.chat([{"role": "user", "content": "hi"}], tier=Tier.FAST)
    assert isinstance(r, LLMResponse)
    assert r.content == "Hello"
    assert r.usage["prompt_tokens"] == 100


@patch("repopilot.llm.service.litellm.completion")
def test_chat_fast_uses_fast_model(mock_c):
    mock_c.side_effect = _fake_completion
    svc = LLMService(model="openai/default", fast_model="openai/fast", strong_model="openai/strong")
    svc.chat([{"role":"user","content":"hi"}], tier=Tier.FAST)
    assert mock_c.call_args.kwargs["model"] == "openai/fast"
```

- [ ] **Step 5: 实现 repopilot/llm/__init__.py**

```python
from __future__ import annotations
from .service import LLMService, LLMResponse, Tier
from .circuit_breaker import CircuitBreaker, CircuitOpenError
__all__ = ["LLMService", "LLMResponse", "Tier", "CircuitBreaker", "CircuitOpenError"]
```

- [ ] **Step 6: 实现 repopilot/llm/service.py**

```python
from __future__ import annotations
import enum
import time
import random
from dataclasses import dataclass, field
from typing import Any, Optional

import litellm
from repopilot.llm.circuit_breaker import CircuitBreaker, CircuitOpenError

litellm.drop_params = True  # don't error on provider-incompatible params
litellm.set_verbose = False


class Tier(str, enum.Enum):
    FAST = "fast"
    DEFAULT = "default"
    STRONG = "strong"


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    model: str = ""


# Retry classification
def _is_retryable(exc: Exception) -> bool:
    import openai
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError)):
        return True
    if isinstance(exc, openai.APIStatusError):
        return getattr(exc, "status_code", 500) == 429 or getattr(exc, "status_code", 0) >= 500
    return False


class LLMService:
    TIER_TIMEOUTS = {Tier.FAST: 15, Tier.DEFAULT: 25, Tier.STRONG: 40}

    def __init__(self, model: str, fast_model: str, strong_model: str,
                 api_key: str = "", base_url: str = "",
                 max_retries: int = 2, backoff_base: float = 0.5, backoff_cap: float = 8.0):
        self.models = {Tier.DEFAULT: model, Tier.FAST: fast_model, Tier.STRONG: strong_model}
        self.api_key = api_key or None
        self.base_url = base_url or None
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.cb = CircuitBreaker()

    def _model(self, tier: Tier) -> str:
        return self.models.get(tier, self.models[Tier.DEFAULT])

    def chat(self, messages: list[dict], tools: Optional[list[dict]] = None,
             temperature: float = 0.3, tier: Tier = Tier.DEFAULT,
             stream: bool = False) -> LLMResponse:
        if not self.cb.allow_request():
            raise CircuitOpenError(f"LLM circuit open for model {self._model(tier)}")
        kwargs = dict(
            model=self._model(tier),
            messages=messages,
            temperature=temperature,
            timeout=self.TIER_TIMEOUTS[tier],
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.base_url:
            kwargs["base_url"] = self.base_url

        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = litellm.completion(**kwargs)
                self.cb.record_success()
                return self._parse_response(resp, kwargs["model"])
            except Exception as e:
                last_exc = e
                if not _is_retryable(e) or attempt == self.max_retries:
                    self.cb.record_failure()
                    raise
                delay = min(self.backoff_cap, self.backoff_base * (2 ** attempt))
                delay = delay * (0.5 + random.random() * 0.5)  # full jitter
                time.sleep(delay)
        raise last_exc  # type: ignore

    def chat_fast(self, system: str, user: str, **kw) -> str:
        return self.chat([{"role":"system","content":system},{"role":"user","content":user}],
                         tier=Tier.FAST, **kw).content

    def chat_strong(self, system: str, user: str, **kw) -> str:
        return self.chat([{"role":"system","content":system},{"role":"user","content":user}],
                         tier=Tier.STRONG, **kw).content

    def _parse_response(self, resp, model: str) -> LLMResponse:
        choice = resp.choices[0].message
        tool_calls = []
        if getattr(choice, "tool_calls", None):
            for tc in choice.tool_calls:
                import json as _json
                fn = tc.function
                try:
                    args = _json.loads(fn.arguments or "{}")
                except _json.JSONDecodeError:
                    args = {"_raw": fn.arguments}
                tool_calls.append({"id": tc.id, "name": fn.name, "arguments": args})
        usage = {}
        if getattr(resp, "usage", None):
            usage = {"prompt_tokens": resp.usage.prompt_tokens or 0,
                     "completion_tokens": resp.usage.completion_tokens or 0,
                     "total_tokens": resp.usage.total_tokens or 0}
        return LLMResponse(content=choice.content or "", tool_calls=tool_calls,
                           usage=usage, model=model)

    def async_chat(self, *a, **kw):
        raise NotImplementedError("streaming via achat in Task 14")
```

- [ ] **Step 7: 跑 LLM 测试确认通过**

```bash
uv run pytest tests/test_circuit_breaker.py tests/test_llm.py -v
```
Expected: 5 PASS.

- [ ] **Step 8: 创建 stream_handler.py 占位（Phase 1 非流式先跑通，streaming 在 T14 接入）**

`repopilot/llm/stream_handler.py`:
```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterator, Optional
from rich.console import Console


@dataclass
class StreamEvent:
    type: str  # "text_delta" | "tool_call" | "done"
    content: str = ""
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None


class RichStreamHandler:
    def __init__(self, console: Optional[Console] = None):
        self.console = console or Console()

    def on_event(self, ev: StreamEvent) -> None:
        if ev.type == "text_delta":
            self.console.print(ev.content, end="", highlight=False)
        elif ev.type == "tool_call":
            self.console.print(f"\n[dim]  → {ev.tool_name}[/dim]")
        elif ev.type == "done":
            self.console.print()
```

- [ ] **Step 9: Commit**

```bash
git add repopilot/llm/ tests/test_circuit_breaker.py tests/test_llm.py
git commit -m "feat(t3): LLMService with CircuitBreaker and 3-tier routing"
```

---

### Task 4: Sandbox 抽象基类 + Docker 实现

**Files:**
- Create: `repopilot/sandbox/__init__.py`
- Create: `repopilot/sandbox/base.py`
- Create: `repopilot/sandbox/docker_sandbox.py`
- Test: `tests/test_docker_sandbox.py`（需要 Docker Desktop 运行）

**Interfaces:**
- Produces:
  - `class Sandbox(ABC)` with abstract methods:
    - `setup(repo_path: Path) -> None` 挂载/启动
    - `teardown() -> None` 清理
    - `read_file(path, offset=0, limit=200) -> str`
    - `write_file(path, content) -> None`
    - `edit_file(path, old_string, new_string) -> None`
    - `exec(command, timeout=30, cwd=None) -> ExecResult`
    - `glob(pattern) -> list[str]`
    - `grep(pattern, glob_=None, ignore_case=False) -> list[GrepMatch]`
    - `list_dir(path, max_depth=2) -> dict`
    - `get_repo_tree(max_tokens=4000) -> str`（repo map 占位，T11 真正实现）
  - `@dataclass ExecResult(stdout, stderr, exit_code, timed_out)`
  - `@dataclass GrepMatch(file, line_no, content)`
  - `class DockerSandbox(Sandbox)` 实现

- [ ] **Step 1: 定义 Sandbox 抽象基类**

`repopilot/sandbox/__init__.py`:
```python
from __future__ import annotations
from .base import Sandbox, ExecResult, GrepMatch
from .docker_sandbox import DockerSandbox
from .local_sandbox import LocalSandbox
__all__ = ["Sandbox", "ExecResult", "GrepMatch", "DockerSandbox", "LocalSandbox"]
```

`repopilot/sandbox/base.py` — 定义 `Sandbox(ABC)` 抽象类，包含上述所有抽象方法 + `ExecResult`/`GrepMatch` dataclass + `truncate_output(text, head=500, tail=1500)` 工具函数。

- [ ] **Step 2: 实现 DockerSandbox** 核心逻辑：
- `setup()`: 启动容器（`docker.from_env().containers.run(image, mounts=[Mount("/workspace", host_path, type="bind")]`），mem_limit/cpu_quota/network_mode 按 Settings 配置
- `teardown()`: `container.kill(); container.remove()`
- `exec()`: `container.exec_run(cmd, workdir=/workspace, timeout=timeout)`，返回 ExecResult；超时捕获 `requests.exceptions.ReadTimeout`
- `read_file()`: 通过 `container.get_archive(path)` 读文件后分页返回；带行号格式化
- `write_file()`: 通过 `container.put_archive(path, tar_buffer)` 写入
- `edit_file()`: 在容器内 `read_file → replace old_string with new_string → write_file`，old_string 必须精确匹配否则 ToolError
- `glob()`: `exec(f"find . -path './.git' -prune -o -name '{pattern}' -print")`
- `grep()`: `exec(f"grep -rn {'-i' if ignore_case else ''} --include='{glob_}' '{pattern}' .")` 解析成 GrepMatch 列表；自动跳过 `.git/node_modules/__pycache__/.venv`
- `list_dir()`: `exec(f"find {path} -maxdepth {max_depth} -not -path '*/.git/*' ...")` 解析成树形 dict
- `get_repo_tree()`: 临时返回简单的 `find . -type f | head -200`（T11 tree-sitter 替换为真正的 Repo Map）
- `__enter__/__exit__` 支持 `with DockerSandbox(...) as s:` 自动 setup/teardown

- [ ] **Step 3: 写 Docker 测试**（需要 Docker Desktop 启动）

`tests/test_docker_sandbox.py`:
```python
import pytest, tempfile, os
from pathlib import Path
from repopilot.sandbox import DockerSandbox, ExecResult

pytestmark = pytest.mark.skipif(os.environ.get("SKIP_DOCKER"), reason="no docker")

def test_exec_echo():
    with tempfile.TemporaryDirectory() as tmp:
        with DockerSandbox(repo_path=Path(tmp), image="python:3.10-slim",
                          mem_limit="512m", network="none") as s:
            r = s.exec("echo hello")
            assert r.exit_code == 0
            assert "hello" in r.stdout

def test_write_and_read_file():
    with tempfile.TemporaryDirectory() as tmp:
        with DockerSandbox(repo_path=Path(tmp), image="python:3.10-slim") as s:
            s.write_file("test.txt", "hello\nworld\n")
            content = s.read_file("test.txt")
            assert "hello" in content
            assert "world" in content

def test_edit_file_string_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        with DockerSandbox(repo_path=Path(tmp), image="python:3.10-slim") as s:
            s.write_file("a.py", "def foo():\n    return 1\n")
            with pytest.raises(Exception):
                s.edit_file("a.py", "nonexistent", "replacement")

def test_exec_timeout():
    with tempfile.TemporaryDirectory() as tmp:
        with DockerSandbox(repo_path=Path(tmp), image="python:3.10-slim") as s:
            r = s.exec("sleep 10", timeout=1)
            assert r.timed_out or r.exit_code != 0

def test_grep_finds_string():
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "test.py").write_text("def myfunc():\n    pass\n")
        with DockerSandbox(repo_path=Path(tmp), image="python:3.10-slim") as s:
            matches = s.grep("myfunc")
            assert len(matches) == 1
            assert matches[0].file.endswith("test.py")
```

- [ ] **Step 4: 跑测试**

```bash
# 需要 Docker Desktop 启动
uv run pytest tests/test_docker_sandbox.py -v
```
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add repopilot/sandbox/ tests/test_docker_sandbox.py
git commit -m "feat(t4): Sandbox ABC + DockerSandbox implementation"
```

---

### Task 5: Local Sandbox 实现

**Files:**
- Create: `repopilot/sandbox/local_sandbox.py`
- Test: `tests/test_local_sandbox.py`

**Interfaces:**
- Consumes: `Sandbox` ABC from T4
- Produces: `class LocalSandbox(Sandbox)` — 在本地 subprocess 执行，cwd 锁定到 repo_path，path traversal 防护（禁止访问 repo_path 之外的路径）。write/edit 走原生文件操作，exec 走 `subprocess.run(..., cwd=repo_path, timeout=...)`，grep 用 Python 原生实现（避免依赖系统 grep）。

- [ ] **Step 1: 实现 LocalSandbox**，注意：
- cwd 锁死 repo_path.resolve()
- 所有 path 参数 `(repo_path / user_path).resolve()`，若不以 `repo_path.resolve()` 开头则抛 PermissionError
- exec 用 `subprocess.run(cmd, shell=True, cwd=repo_path, capture_output=True, text=True, timeout=timeout)`
- grep 用 `pathlib.Path.rglob()` + 正则逐文件扫描
- glob 用 `Path.glob()` （注意递归 `**/pattern`）
- `teardown()` 本地模式无容器，no-op
- list_dir 用 os.walk 组装树形

- [ ] **Step 2: 写测试** `tests/test_local_sandbox.py`（复用 T4 逻辑但在本地 tempdir，不需要 Docker）

- [ ] **Step 3: 跑测试并提交**
```bash
uv run pytest tests/test_local_sandbox.py -v
git add repopilot/sandbox/local_sandbox.py tests/test_local_sandbox.py
git commit -m "feat(t5): LocalSandbox with path-traversal protection"
```

---

### Task 6: Permission Engine

**Files:**
- Create: `repopilot/permission/__init__.py`
- Create: `repopilot/permission/patterns.py`
- Create: `repopilot/permission/engine.py`
- Create: `repopilot/permission/approver.py`
- Test: `tests/test_permission.py`

**Interfaces:**
- Produces:
  - `class PermissionEngine(mode: str)` with method:
    - `check_tool(tool_name: str, args: dict) -> PermissionDecision`
  - `@dataclass PermissionDecision(action: "allow"|"deny"|"ask", reason: str="")`
  - `class Approver(ABC)` with `ask(tool_name, args, reason) -> "y"|"n"|"a"|"d"|"e"`
  - `class CLIApprover(Approver)`: Rich 交互提示，用 prompt-toolkit 读输入
  - `class AutoApprover(Approver)`: auto 模式下默认 allow；deny 模式默认 deny；规则黑名单仍触发 deny
  - 内置黑名单：dangerous_paths（`~/.ssh`, `.env`, `*id_rsa*`, `/etc/`），dangerous_cmds（`rm -rf /`, `sudo `, `curl ...|sh`, `chmod -R 777`, `git push --force`），safe_cmd 白名单（`ls`, `cat`, `git status`, `git diff`, `pytest`, ...）

- [ ] **Step 1: 实现 patterns.py**：定义 `DANGEROUS_PATHS`, `DANGEROUS_CMD_PATTERNS` (正则), `SAFE_CMDS`(命令前缀列表)

- [ ] **Step 2: 实现 engine.py**：`check_tool(tool_name, args)` 根据 mode 分层决策：
- Tier 0 工具（read/grep/glob/list_dir）→ always allow
- edit-only 模式：禁止 exec 类工具
- deny 模式：所有写/执行都 deny
- auto 模式：黑名单匹配 → deny，白名单 → allow，其他 → allow
- confirm 模式：黑名单 → deny，白名单 → allow，其他 → ask
- 危险 path 检测：对 write_file/edit_file/bash（路径参数）检测是否命中 dangerous_paths
- 危险 cmd 检测：对 bash/run_python 命令用 DANGEROUS_CMD_PATTERNS 正则匹配
- network 检测：bash 含 `curl`/`wget`/`pip install` → 沙箱 network=none 情况下 deny；否则 ask

- [ ] **Step 3: 实现 approver.py**：CLIApprover 用 Rich `console.print` + `Prompt.ask` 显示命令预览和选项 `[y]es [a]lways [n]o [d]eny-and-stop [e]dit`

- [ ] **Step 4: 写测试** `tests/test_permission.py`（纯 AutoApprover，不需要交互）覆盖：
- Tier 0 工具在所有模式下 allow
- rm -rf 在所有模式下 deny
- sudo 在 confirm 模式下 ask
- edit-only 模式下 bash deny
- 写 ~/.ssh/config 在所有模式下 deny
- a 模式记忆：always 结果在当前 session 内被记住

- [ ] **Step 5: 跑测试提交**
```bash
uv run pytest tests/test_permission.py -v
git add repopilot/permission/ tests/test_permission.py
git commit -m "feat(t6): PermissionEngine with 4 approval modes and CLI approver"
```

---

### Task 7: Tool 基类 + ToolRegistry

**Files:**
- Create: `repopilot/tools/__init__.py`
- Create: `repopilot/tools/base.py`
- Create: `repopilot/tools/registry.py`
- Create: `repopilot/tools/result.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Produces:
  - `@dataclass ToolResult(content: str, error: str|None=None, metadata: dict={})`，支持 `__bool__`（error 为 None 为 True）
  - `class Tool(ABC)`:
    - `name: str`, `description: str`, `parameters_schema: dict`（JSON Schema）
    - `tier: int`（0=只读,1=写,2=执行,3=高危）
    - `execute(args: dict, sandbox: Sandbox, context: ContextManager) -> ToolResult`
  - `class ToolRegistry`:
    - `register(tool: Tool)` / `get(name) -> Tool` / `list_tools() -> list[Tool]`
    - `schemas() -> list[dict]`（LiteLLM function schemas）
    - `execute(name, args, sandbox, context, permission) -> ToolResult`（先 permission check，allow 才执行）
  - 辅助：`truncate_text(text, head=500, tail=1500, max_lines=None) -> str`（tool-compact 核心）

- [ ] **Step 1-3: 实现 base/result/registry 三个文件**，按上面接口。
- [ ] **Step 4: 写测试**覆盖注册、schema 生成、权限拦截、truncate 边界。
- [ ] **Step 5: 跑测试提交**
```bash
uv run pytest tests/test_tools.py -v
git add repopilot/tools/base.py repopilot/tools/registry.py repopilot/tools/result.py repopilot/tools/__init__.py tests/test_tools.py
git commit -m "feat(t7): Tool base class and ToolRegistry"
```

---

### Task 8: 核心文件/搜索工具（read/write/edit/grep/glob/list_dir）

**Files:**
- Create: `repopilot/tools/file_tools.py`
- Create: `repopilot/tools/search_tools.py`
- Test: `tests/test_file_tools.py`（用 LocalSandbox tempdir，快）
- Test: `tests/test_search_tools.py`

**工具清单：**
- `ReadFileTool(Tool)` tier=0: `read_file(path, offset=0, limit=200)` 返回带行号的内容，offset/limit 分页
- `WriteFileTool(Tool)` tier=1: `write_file(path, content)`
- `EditFileTool(Tool)` tier=1: `edit_file(path, old_string, new_string)` 调用 sandbox.edit_file
- `GrepTool(Tool)` tier=0: `grep(pattern, glob=None, ignore_case=False)` → 格式化 GrepMatch 列表
- `GlobTool(Tool)` tier=0: `glob(pattern)` → 换行分隔的文件列表
- `ListDirTool(Tool)` tier=0: `list_dir(path='.', max_depth=2)` → 树形目录字符串

- [ ] **Step 1-3: 实现六个工具**，每个 register 到 ToolRegistry。注意 read_file 返回格式必须统一带行号：`    1|def foo():\n    2|    return 1\n`
- [ ] **Step 4: 测试**用 tmp 目录，覆盖 read 分页、write 覆盖、edit 字符串不匹配错误、grep 匹配/不匹配、glob 多文件、list_dir 递归。
- [ ] **Step 5: 跑测试提交**
```bash
uv run pytest tests/test_file_tools.py tests/test_search_tools.py -v
git commit -m "feat(t8): file tools (read/write/edit) and search tools (grep/glob/list_dir)"
```

---

### Task 9: 执行工具 + finish（bash/run_python/finish）

**Files:**
- Modify: `repopilot/tools/__init__.py`
- Create: `repopilot/tools/exec_tools.py`
- Create: `repopilot/tools/meta_tools.py`
- Test: `tests/test_exec_tools.py`
- Test: `tests/test_meta_tools.py`

**工具清单：**
- `BashTool(Tool)` tier=2/3: `bash(command, timeout=30, workdir=None)` → 通过 sandbox.exec；输出自动 truncate（head 500+tail 1500）；命令命中黑名单时 ToolResult(error="blocked by policy")
- `RunPythonTool(Tool)` tier=2: `run_python(code, timeout=10, deps=None)` → 把 code 写入临时 .py 文件在 sandbox 内 `python tmp.py` 执行，比 bash -c 更安全（可限定超时、捕获 stderr）
- `FinishTool(Tool)` tier=0: `finish(summary, tests_passed=True)` → 抛出 `AgentFinished` 异常让主循环捕获退出

- [ ] **Step 1-2: 实现 exec_tools.py/meta_tools.py**，定义 `class AgentFinished(Exception)` 在 base.py。
- [ ] **Step 3: 测试**：bash echo 成功、bash 危险命令 blocked（permission engine 集成）、run_python 执行简单 print、finish 抛出异常。
- [ ] **Step 4: 跑测试提交**
```bash
uv run pytest tests/test_exec_tools.py tests/test_meta_tools.py -v
git commit -m "feat(t9): bash/run_python/finish tools"
```

---

### Task 10: Hook Manager + Cost tracker + Logger

**Files:**
- Create: `repopilot/hooks/__init__.py`
- Create: `repopilot/hooks/manager.py`
- Create: `repopilot/hooks/builtin.py`
- Create: `repopilot/agent/cost.py`
- Create: `repopilot/logging_setup.py`
- Test: `tests/test_hooks.py`
- Test: `tests/test_cost.py`

**Interfaces:**
- `class HookManager`: `register(event: str, fn: Callable)`, `fire(event, *args, **kwargs) -> HookResult(action= "continue"|"skip"|"deny", override: any)`
- Events: `pre_tool`, `post_tool`, `pre_compact`, `post_compact`, `on_finish`, `on_error`, `on_llm_call`
- Hook 返回 HookResult.deny 可阻断工具调用（permission 用这个）；返回 override 可替换工具结果
- Builtin hooks: `audit_log_hook`（structlog 记录每个 tool call）、`cost_tracker_hook`（累加 token/$）
- `class CostTracker`: `on_llm_call(usage, model)` / `on_tool(tool_name, duration)` / `summary() -> dict` / `reset()`
- logging_setup: 配置 structlog JSON 日志到 stderr + `~/.repopilot/logs/repopilot.log`

- [ ] **Step 1-3: 按上述接口实现，CostTracker 用 LiteLLM 的 `model_cost` dict 估算价格（`litellm.model_cost` 提供 per-token price）。**
- [ ] **Step 4: 测试** hook 注册/触发/deny 逻辑；cost tracker 累加正确。
- [ ] **Step 5: 提交**
```bash
uv run pytest tests/test_hooks.py tests/test_cost.py -v
git commit -m "feat(t10): Hook system with cost tracker and audit logger"
```

---

### Task 11: tree-sitter Repo Map

**Files:**
- Create: `repopilot/code_index/__init__.py`
- Create: `repopilot/code_index/ignore.py`
- Create: `repopilot/code_index/tree_sitter_setup.py`
- Create: `repopilot/code_index/repo_map.py`
- Create: `repopilot/code_index/symbol_index.py`
- Test: `tests/test_repo_map.py`

**Interfaces:**
- `class RepoMapBuilder`:
  - `__init__(sandbox, max_tokens=4000)`
  - `build() -> str` 扫描 sandbox 内的 repo 生成代码地图
  - `update(path: str)` 增量更新单个文件
- 输出格式：按目录分组，列出文件和类/函数签名+docstring 首行，大文件截断
- 排序策略：git ls-files 获取版本控制文件列表 → 优先 .py/.js/.ts/.go/.rs 源码 → 跳过 ignore.py 中的模式 → 超过 token 预算时按"最近 git 修改频率"和"与 task 关键词相关度"rank（Phase 1 简化：按文件大小排序，小文件优先）
- `tree_sitter_setup.py`: 安装 tree-sitter-python language（首次运行时自动 build）
- `ignore.py`: 读 .gitignore + 默认忽略（`.git/`, `__pycache__/`, `node_modules/`, `.venv/`, `dist/`, `build/`, `*.min.js`）

- [ ] **Step 1: 实现 ignore.py**（pathspec 库解析 .gitignore + 默认模式）
- [ ] **Step 2: 实现 tree_sitter_setup.py**（`tree_sitter_python` binding 自动加载）
- [ ] **Step 3: 实现 symbol_index.py**——给定文件路径和源码，返回 `[{kind: "class"|"function", name, line, docstring}]`
- [ ] **Step 4: 实现 repo_map.py**——遍历所有相关文件，调用 symbol_index，在 token 预算内组装树形字符串
- [ ] **Step 5: 更新 Sandbox.get_repo_tree()** 调用 RepoMapBuilder（替换 T4 的 find 占位实现）
- [ ] **Step 6: 测试**在临时 Python 项目（多个文件+子类+函数）上 build，验证输出包含签名和docstring，总长度不超预算
- [ ] **Step 7: 提交**
```bash
uv run pytest tests/test_repo_map.py -v
git commit -m "feat(t11): tree-sitter Repo Map builder with gitignore support"
```

---

### Task 12: Session Store（JSONL + SQLite 索引）

**Files:**
- Create: `repopilot/session/__init__.py`
- Create: `repopilot/session/store.py`
- Create: `repopilot/session/index.py`
- Create: `repopilot/session/rewind.py`
- Create: `repopilot/session/slash_commands.py` (placeholder — T14 实现具体命令)
- Test: `tests/test_session.py`

**Interfaces:**
- `class SessionStore`:
  - `create(title=None, cwd=None, model=None) -> Session`
  - `get(session_id) -> Session`
  - `list(limit=50) -> list[SessionMeta]`
  - `append_event(session_id, event: dict)` 追加 JSONL 事件（类型：`user_msg/assistant_msg/tool_call/tool_result/plan/replan/finish/error/slash`）
  - `read_events(session_id, after_id=0) -> list[dict]`
  - `rewind(session_id, step: int) -> list[dict]` 截断到第 N 步之后（保留前 N 个事件）
- JSONL 路径：`~/.repopilot/sessions/YYYY/MM/DD/rollout-<ts>-<shortid>.jsonl`，每行是 `{"id": int, "ts": ISO8601, "type": "...", "payload": ...}`
- SQLite 索引：`~/.repopilot/state.sqlite`，表 `threads(id TEXT PK, rollout_path TEXT, title TEXT, cwd TEXT, model TEXT, created_at INT, updated_at INT, tokens_used INT, last_event_id INT)`
- `rewind` 实现：读取 JSONL 前 N 个事件，truncate 文件，更新 SQLite last_event_id

- [ ] **Step 1-3: 实现 store/index/rewind，JSONL 行 id 从 1 自增，append 原子追加。**
- [ ] **Step 4: 测试**创建会话、追加事件、list、rewind、rewind 后追加新事件。
- [ ] **Step 5: 提交**
```bash
uv run pytest tests/test_session.py -v
git commit -m "feat(t12): Session JSONL store with SQLite index and rewind"
```

---

### Task 13: Context Manager + 三级压缩

**Files:**
- Create: `repopilot/agent/__init__.py`
- Create: `repopilot/agent/context.py`
- Create: `repopilot/agent/compact.py`
- Test: `tests/test_context.py`

**Interfaces:**
- `class ContextManager`:
  - `__init__(budget_tokens, system_prompt, repo_map_str="", memory_str="")`
  - `set_plan(plan: str)`, `add_observation(text)`, `add_assistant(text, tool_calls)`, `add_user(text)`
  - `build_messages(task) -> list[dict]` 组装 LLM 消息数组
  - `token_usage_ratio() -> float`
  - `recent_steps(n=20) -> list[dict]`
  - `inject_skill_prompt(text)` （临时注入 skill 内容）
  - `compact(level: "micro"|"auto")` 触发压缩
- L0: system prompt（固定 ~800 tok）+ REPOPILOT.md 内容
- L0.5: repo_map_str（~4000 tok）+ memory_str（~1000 tok）+ injected skills
- Plan 段: 随 replan 更新
- L2 Compacted summary: micro-compact 压缩最老 3-5 步 / auto-compact 压缩除最近 K 步外全部
- L1 Recent steps: 剩余 budget 容纳最近步骤原文
- Pending tool result: 上一条工具结果（已经经过 tool-compact 截断）
- `tool-compact`: 在 ToolResult 构造时自动应用，truncate_text 纯规则不调 LLM
- `micro-compact(llm)`: 调 fast LLM 把最老 3-5 步压缩为 1-2 句
- `auto-compact(llm)`: 调 fast LLM 把早段历史压缩为结构化摘要（已完成/关键发现/文件位置/未解决问题），保留最近 K=10 步原文

- [ ] **Step 1-3: 实现**（token 估算用 `len(text)//4` 中英混合近似；真实 token 数在拿到 LLM response 后用 usage.prompt_tokens 校正）。
- [ ] **Step 4: 测试**构建 messages 格式正确、token 截断逻辑正确、micro-compact 调用 LLM 合并、auto-compact 大量历史后仍在 budget 内。
- [ ] **Step 5: 提交**
```bash
uv run pytest tests/test_context.py -v
git commit -m "feat(t13): ContextManager with 3-level compaction"
```

---

### Task 14: Agent Loop + Planner/Reflector/Parser + CLI 接入 streaming

**Files:**
- Create: `repopilot/agent/loop.py`
- Create: `repopilot/agent/planner.py`
- Create: `repopilot/agent/reflector.py`
- Create: `repopilot/agent/parser.py`
- Create: `repopilot/agent/prompts/__init__.py` (空)
- Create: `repopilot/agent/prompts/system.md`
- Create: `repopilot/agent/prompts/plan.md`
- Create: `repopilot/agent/prompts/reflect.md`
- Modify: `repopilot/cli.py`（接入 chat 命令真正调用 agent loop，Rich streaming 输出）
- Modify: `repopilot/sandbox/base.py` 的 get_repo_tree 已在 T11 接到 RepoMap
- Modify: `repopilot/__init__.py` 暴露主要入口
- Test: `tests/test_loop_mock.py`（mock LLM 验证状态机）
- Test: `tests/test_parser.py`
- Test: `tests/test_e2e_simple.py`（Docker 内真实跑 1-2 个简单任务，skip 无 Docker）

**Interfaces:**
- `class AgentFinished(Exception)` 在 T9 已定义
- `def run_agent(task, repo_path, config, sandbox=None, llm=None, hooks=None, console=None, stream=True) -> RunResult`
  - 创建所有依赖（sandbox 根据 config、llm、tools registry、hooks、permission engine、context、session store）
  - 调 planner.initial_plan() 生成步骤清单
  - 循环：build_messages → llm.chat(stream=stream) → parse_response（function call + XML fallback）→ handle slash commands → handle finish → 逐个 tool call：fire pre_tool hook → permission check → sandbox.execute → fire post_tool hook → 检查 reflection 触发 → 检查预算
  - 返回 RunResult(status, trajectory, cost, steps)
- `class Planner`:
  - `initial_plan(task, repo_tree) -> str`（fast LLM 生成 markdown steps 清单）
  - `replan(task, reflection, context) -> str`
- `class Reflector`:
  - `reflect(task, plan, recent_steps, failures, token_ratio) -> Reflection(should_replan:bool, should_compress:"none"|"micro"|"auto", reason:str, summary:str="")`
- `class OutputParser`:
  - `parse(response) -> ParsedResponse(action: "tool"|"finish"|"slash"|"text", tool_calls, slash_cmd, message)`
  - 三层：1) native tool_calls 优先；2) 无 tool_calls 时匹配 `<tool_call>...</tool_call>` XML 格式；3) 解析失败把错误 append 到 messages 让模型自修复（最多 3 次）

**Prompt 要点:**
- system.md: 角色、工具使用规范、输出格式（优先 function calling；不支持时用 XML）、安全约束、不要过度使用工具、一次最多 3 个工具调用
- plan.md: 给定 repo map 和 task，生成 3-7 步执行计划
- reflect.md: 给定最近步骤和失败次数，判断是否需要 replan/compress/继续

CLI 接入 (`cli.py::chat`):
- 创建 Settings，创建 console，创建 Rich Live/Status 做 streaming 渲染
- 调 run_agent()，结束后打印 summary 和 cost
- 注册 Ctrl+C (KeyboardInterrupt) 优雅退出并保存会话

- [ ] **Step 1-6: 按上述实现所有文件。** Prompt 模板用 YAML 或 .md 都可以（.md 直接读文件）。
- [ ] **Step 7: 写 mock 测试**用 fake LLM 返回预设 tool_calls 序列，验证 loop 正确执行 finish、正确拦截 permission、错误格式触发自修复。
- [ ] **Step 8: Parser 测试**解析 native tool_calls / XML / 错误格式三种情况。
- [ ] **Step 9: E2E 测试**（需要 Docker）：在一个临时 Python 项目里，给 agent "在 hello.py 写一个函数 add(a,b) 返回 a+b，然后运行 python -c 'from hello import add; print(add(2,3))' 验证结果" 任务，断言能跑通。
- [ ] **Step 10: 提交**
```bash
uv run pytest tests/test_parser.py tests/test_loop_mock.py -v
# 有 Docker 时: uv run pytest tests/test_e2e_simple.py -v
git add repopilot/agent/ repopilot/cli.py tests/test_parser.py tests/test_loop_mock.py tests/test_e2e_simple.py
git commit -m "feat(t14): Plan-Act-Reflect loop with parser, planner, reflector, CLI streaming"
```

---

### Task 15: Prompt 调优 + fixture 项目 + 5 个 E2E 任务验证

**Files:**
- Create: `tests/fixtures/simple_project/`（3-5 个 Python 文件的小项目，含一个故意 failing test）
- Modify: `repopilot/agent/prompts/*.md`（根据 E2E 结果调优）
- Modify: `README.md`（加使用说明、demo）
- Test: `tests/test_e2e_tasks.py`（5 个任务，用 fast 模型）

**5 个 E2E 任务（Phase 1 必须通过的能力基线）：**
1. "Add a `--verbose` flag to the cli function" （参数添加）
2. "Fix the failing test in test_math.py"（test says `add(2,2) == 5`，当前 add 返回 a+b）
3. "Create a new file utils.py with a helper function `greet(name)` returning f'Hello, {name}!'"（文件创建）
4. "Find all TODO comments in the project and list them"（grep 搜索）
5. "Run the tests after your fix and confirm they pass"（执行测试并确认）

- [ ] **Step 1: 建 fixture 项目**（`simple_project/` 包含 main.py、math.py、test_math.py（故意 fail）、cli.py）
- [ ] **Step 2: 逐个跑任务，观察 trajectory，调整 prompt（system 提示词里加"先做 plan，然后一步一步执行"，"edit_file 时 old_string 要包含足够上下文至少 3 行"等）
- [ ] **Step 3: 加入 test_e2e_tasks.py（用 `pytest.mark.skipif` 控制 API key 不存在时跳过）**
- [ ] **Step 4: 更新 README**（项目介绍、安装、快速开始、Demo asciicast 或截图占位）
- [ ] **Step 5: 最终提交**
```bash
uv run pytest tests/ -v --ignore=tests/test_docker_sandbox.py  # 无 Docker 时
uv run pytest tests/ -v                                     # 全量
git add tests/fixtures/ README.md repopilot/agent/prompts/ tests/test_e2e_tasks.py
git commit -m "feat(t15): E2E baseline 5 tasks + README + prompt tuning"
git tag v0.1.0-phase1
```

---

## Phase 1 完成验收标准

1. `repopilot --help` 显示所有命令
2. `repopilot chat "your task" -r ./some-project --sandbox docker` 在 Docker 沙箱内自主执行
3. Streaming 输出实时显示 thought 和工具调用
4. 危险操作（rm -rf、写 ~/.ssh）被 Permission 拦截
5. 每个会话保存 JSONL 轨迹到 `~/.repopilot/sessions/`，可 replay
6. 5 个 E2E 基线任务在 fast 模型上成功率 ≥60%
7. 单测覆盖率：核心模块（loop/parser/sandbox/permission/tools/context）≥70%
8. `repopilot version` 输出版本号

## Phase 1 总文件清单

```
repopilot/
├── __init__.py, cli.py, config.py, logging_setup.py
├── llm/        (__init__, service, circuit_breaker, stream_handler)
├── sandbox/    (__init__, base, docker_sandbox, local_sandbox)
├── permission/ (__init__, patterns, engine, approver)
├── tools/      (__init__, base, registry, result, file_tools, search_tools, exec_tools, meta_tools)
├── hooks/      (__init__, manager, builtin)
├── code_index/ (__init__, ignore, tree_sitter_setup, repo_map, symbol_index)
├── session/    (__init__, store, index, rewind, slash_commands)
├── agent/      (__init__, loop, planner, reflector, context, compact, cost, parser,
│               prompts/{system,plan,reflect}.md)
tests/
├── test_config, test_cli, test_circuit_breaker, test_llm,
├── test_docker_sandbox, test_local_sandbox,
├── test_permission, test_tools, test_file_tools, test_search_tools,
├── test_exec_tools, test_meta_tools,
├── test_hooks, test_cost, test_repo_map, test_session,
├── test_context, test_parser, test_loop_mock,
├── test_e2e_simple, test_e2e_tasks,
└── fixtures/simple_project/  (3-5 Python文件)
```

预计代码量：~4500 行生产代码 + ~2500 行测试 = ~7000 行
