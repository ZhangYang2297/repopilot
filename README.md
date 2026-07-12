# RepoPilot

Local-first code agent CLI tool inspired by Codex CLI and Claude Code. Runs in your terminal, uses Docker or local sandbox for safe code execution, and works with any OpenAI-compatible LLM API.

## Features

- **Pure ReAct Agent Loop**: Single-model reasoning (no over-engineered planner/reflector), aligned with Claude Code and Codex CLI
- **Dual Sandbox**: Docker containers (isolated, safe) or local filesystem (fast)
- **Permission Engine**: 4 modes (auto/confirm/edit-only/deny), dangerous command blacklists, path protection
- **Tree-sitter Repo Map**: AST-based code structure overview with token budget control
- **Three-level Context Compression**: tool-compact (rule-based), micro-compact, auto-compact (LLM summarization)
- **Session Persistence**: JSONL event log + SQLite index, rewind support
- **Hook System**: Lifecycle events (pre/post LLM, pre/post tool, on_finish, on_error)
- **Cost Tracking**: Per-model token and cost accounting
- **Circuit Breaker**: Sliding-window fault tolerance for LLM API failures
- **Atomic File Writes**: Temp file + rename to prevent data loss
- **434 unit tests** covering all modules

## Quick Start

### Install

```bash
cd RepoPilot
pip install -e .
```

### First Run

```bash
repopilot
```

On first run you'll be prompted for:
1. Model name (provider/model format, e.g. `openai/doubao-seed-evolving` for Volcengine ARK)
2. API key (`sk-...` format)
3. Base URL (for OpenAI-compatible endpoints, leave empty for OpenAI default)

### Usage

```bash
# Fix a bug in current directory
repopilot chat "Fix the failing test in test_math.py" --sandbox local --approval-mode auto

# Create a new feature in Docker sandbox
repopilot chat "Add a --verbose flag to utils.py" -r ./myproject --sandbox docker

# Use a different model
repopilot chat "Refactor this module" -m openai/gpt-4o

# Manage configuration
repopilot config show
repopilot model doubao-seed-evolving
repopilot models  # list recommended models
```

## Architecture

```
CLI (typer + Rich)
  └─ Agent Loop (ReAct)
       ├─ Context Manager (system → repo_map → summary → history → tools)
       │    └─ Compaction (tool/micro/auto, 75%/90% thresholds)
       ├─ LLM Service (LiteLLM, retry+jitter, circuit breaker)
       ├─ Tool Registry (read/write/edit/grep/glob/list/bash/run_python/finish)
       │    └─ Permission Engine (auto/confirm/edit-only/deny + blacklists)
       ├─ Sandbox (Local / Docker)
       │    ├─ atomic writes, path traversal protection
       │    └─ Docker: cgroups limits, network mode, shquote injection defense
       ├─ Code Index (tree-sitter Repo Map)
       ├─ Session Store (JSONL events + SQLite metadata)
       └─ Hook System (cost tracking, logging, audit)
```

## Safety Features

- **Path traversal protection**: All file paths resolved and validated against repo root
- **Shell injection defense**: `shquote()` for all Docker exec arguments
- **Dangerous command blacklist**: rm -rf, sudo, curl|sh, base64|sh, chmod 777, force push, fork bombs, etc.
- **Sensitive path protection**: .ssh, .env, .aws, /etc, /proc, .git blocked
- **Ambiguous edit rejection**: edit_file requires unique match (or replace_all=true) and minimum context length
- **Cgroup resource limits**: Docker mode limits CPU (2 cores) and memory (2GB)
- **Atomic file writes**: Temp file + os.replace prevents corruption on crash
- **Binary file skipping**: grep automatically skips .pyc/.png/.exe etc.

## Commands

| Command | Description |
|---------|-------------|
| `repopilot chat <task>` | Run agent on a task |
| `repopilot model <name>` | Set model |
| `repopilot models` | List recommended models |
| `repopilot config show` | Show configuration |
| `repopilot config set <k> <v>` | Set config value |
| `repopilot config init` | Re-run setup wizard |
| `repopilot version` | Show version |

## Project Status

Phase 1 complete (T1-T15): Agent core is working end-to-end with real LLM.

## License

MIT
