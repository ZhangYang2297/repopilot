<div align="center">

# RepoPilot

**Local-first AI code agent in your terminal.**

[English](README.md) | [中文](README.zh-CN.md)

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![PyPI version](https://img.shields.io/pypi/v/repopilot-agent.svg)](https://pypi.org/project/repopilot-agent/)

</div>

## What is RepoPilot?

RepoPilot is a CLI coding agent inspired by Claude Code and Codex CLI. Navigate to any project, run `repopilot`, and describe what you want done in natural language — RepoPilot reads, searches, edits, runs tests, and fixes bugs autonomously in a sandboxed environment.

```
$ cd your-project
$ repopilot

────────────────────────────── RepoPilot ──────────────────────────────
  Directory: /your-project
  Model:     doubao-seed-evolving
  Sandbox:   local
  Approval:  auto

Type /help for commands, /exit to quit.

repopilot> fix the failing test in test_auth.py
> read_file(path=test_auth.py)
> bash(command=python -m pytest test_auth.py -v)
> edit_file(path=auth.py, ...)
> bash(command=python -m pytest test_auth.py -v)

  All tests pass. Fixed the token validation bug in auth.py line 42.
```

## Features

- **Claude Code / Codex CLI style REPL** — `cd project && repopilot`, start chatting immediately
- **Pure ReAct agent loop** — single model does all thinking, no multi-agent overhead
- **Streaming with waiting feedback** — shows `Thinking...` immediately, then renders assistant text incrementally
- **Session diff and undo** — inspect changes with `/diff` and roll them back step-by-step with `/undo`
- **Interactive approvals** — confirm writes/commands with `y/n/a/d`; dangerous operations remain hard-denied
- **Tool timing** — each tool execution displays its elapsed time
- **Persistent multi-turn conversation** with automatic context compaction
- **Layered memory system** — global + project `REPOPILOT.md` files (like CLAUDE.md)
- **Cross-session resume** — `/resume` to continue where you left off
- **Docker sandbox** with CPU/memory limits and optional network isolation
- **4 approval modes**: auto / confirm / edit-only / deny (default: confirm — you approve writes/executions)
- **Dangerous command blacklist** (path traversal, `rm -rf /`, `curl|sh`, force push, credential theft)
- **10 built-in tools**: read/write/edit/grep/glob/list_dir/bash/run_python/repo_tree/finish
- **tree-sitter repo map** — code structure overview without reading every file
- **Circuit breaker + exponential backoff** for reliable LLM calls
- **Cross-platform** — Windows / Linux / macOS with automatic Unix→Windows command translation
- **Any OpenAI-compatible LLM** — use your own API key (Doubao, DeepSeek, OpenAI, vLLM, local models, etc.)
- **No RAG / no vector database** — deterministic grep/glob/tree-sitter retrieval is faster and more accurate for code

## Installation

```bash
pip install repopilot-agent
```

Or install the latest version directly from GitHub:

```bash
pip install git+https://github.com/ZhangYang2297/repopilot.git
```

For an isolated install (recommended for CLI tools):

```bash
pipx install repopilot-agent
```

**Requirements**: Python 3.10+

> **Windows note**: if pip install fails with "Cargo, the Rust package manager, is not installed",
> run pip install repopilot-agent --only-binary :all: to force pre-built wheels, or use pipx.

### First Run

On first run you will be prompted for your LLM configuration:

1. **Model name** (e.g. `openai/doubao-seed-evolving`, `openai/gpt-4o`, `openai/deepseek-chat`)
2. **API key** (sk-...)
3. **Base URL** (for providers other than OpenAI, e.g. `https://ark.cn-beijing.volces.com/api/v3`)

You can also configure via environment variables:

```bash
export REPOPILOT_MODEL=openai/doubao-seed-evolving
export REPOPILOT_API_KEY=sk-your-key
export REPOPILOT_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
```

## Usage

### Interactive Mode (Recommended)

```bash
cd your-project
repopilot                          # current directory, local sandbox, confirm approval
repopilot -r ../other-proj         # specify a different project directory
repopilot --sandbox docker         # run inside a Docker container
repopilot --approval-mode auto     # skip confirmations (trust the agent)
repopilot -m openai/gpt-4o         # override model
```

### One-shot Task Mode

```bash
repopilot chat "fix the bug in auth.py"
repopilot chat "add --verbose flag to cli.py" -r ./myproj
```

### Slash Commands

| Command | Description |
|---------|-------------|
| `/exit`, `/quit` | Exit (Ctrl+C / Ctrl+D also supported) |
| `/help` | Show help |
| `/model [name]` | Show or switch model |
| `/approval [mode]` | Switch approval mode |
| `/compact` | Trigger context compaction |
| `/clear` | Start fresh conversation |
| `/cd [path]` | Switch working directory |
| `/memory [note]` | Show or add memory notes |
| `/resume [id]` | Resume a previous session |
| `/sessions` | List recent sessions |
| `/cost` | Show token usage/cost |
| `/status` | Show current configuration |
| `/diff` | Show all file changes made during this session |
| `/undo` | Revert the most recent file change |

### Project Memory (REPOPILOT.md)

Create a `REPOPILOT.md` in your project root to give RepoPilot persistent instructions:

```markdown
# Project Memory

## Build/Test
- Test: python -m pytest tests/ -v
- Lint: ruff check .

## Conventions
- Use type hints on all functions
- Never modify files in migrations/
```

Global memory lives at `~/.repopilot/REPOPILOT.md` and applies across all projects.

## Configuration

Config file: `~/.repopilot/config.toml`

```toml
[core]
model = "openai/doubao-seed-evolving"
api_key = "sk-..."
base_url = "https://ark.cn-beijing.volces.com/api/v3"
sandbox_type = "local"
approval_mode = "auto"
max_steps = 200
budget_tokens = 500000
tool_timeout = 120
```

Manage config via CLI:

```bash
repopilot config show
repopilot config set model openai/gpt-4o
repopilot config init  # re-run setup wizard
repopilot models       # list recommended models
```

## Architecture

```
┌─────────────────────────────────┐
│  CLI (Typer + Rich)    REPL     │
├─────────────────────────────────┤
│  Shared AgentLoopCore (ReAct)   │
├─────────────────────────────────┤
│  Context Manager  L0-L5 memory  │
├─────────────────────────────────┤
│  Tool Registry + Permission     │
├─────────────────────────────────┤
│  Sandbox (Local / Docker)       │
├─────────────────────────────────┤
│  LLM Service (LiteLLM)          │
└─────────────────────────────────┘
```

Both one-shot `chat` tasks and the interactive REPL use the same UI-independent
`AgentLoopCore` for response normalization and tool execution. The REPL adds
pre-first-event `Thinking...` feedback, streaming rendering, interactive approval,
persistent context, `/diff`, and `/undo`. The indicator only communicates that a
request is active; it does not expose or fabricate hidden model reasoning.

## Development Status

- Current package version: **v0.2.0**
- Phase 2A is complete: streaming, first-event waiting feedback, TTFT/total-duration metrics, tool timing, interactive approval, `/diff`, and `/undo`
- **492** project tests pass (excluding fixture and E2E data projects from collection)

## Built-in Tools

| Tool | Description |
|------|-------------|
| `read_file` | Read a file (with optional line range and offset limit) |
| `write_file` | Write content to a file (creates or overwrites) |
| `edit_file` | Find-and-replace edit (string replacement) |
| `grep_search` | Search file contents with regex |
| `glob` | Find files by glob pattern |
| `list_dir` | List directory contents |
| `repo_tree` | Show tree-sitter generated repository map |
| `bash` | Execute a shell command (sandboxed) |
| `run_python` | Execute Python code in an isolated temp file |
| `finish` | Signal task completion and return to user |

## Supported LLM Providers

Any OpenAI-compatible endpoint works out of the box via [LiteLLM](https://docs.litellm.ai/):

- **Volcengine ARK (Doubao)** — recommended, tested extensively
- **OpenAI** (GPT-4o, GPT-4, o1, etc.)
- **DeepSeek** (deepseek-chat, deepseek-reasoner)
- **Alibaba Qwen** (qwen2.5-coder series)
- **Zhipu GLM** (glm-4, glm-5 series)
- **Local models** via vLLM / Ollama / llama.cpp (any OpenAI-compatible server)
- **Anthropic Claude** (via LiteLLM)

## License

MIT — see [LICENSE](LICENSE) for details.

## Acknowledgements

Built after studying Claude Code (Anthropic), Codex CLI (OpenAI), and the SWE-bench / SWE-agent research.



