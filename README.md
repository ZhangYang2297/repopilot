<div align="center">

# RepoPilot

**Local-first AI code agent in your terminal.**

Inspired by Claude Code and Codex CLI. Navigate to any project, run `repopilot`, and describe what you want done in natural language — RepoPilot reads, searches, edits, runs tests, and fixes bugs autonomously in a sandboxed environment.

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

</div>

## Demo

```bash
$ cd your-project
$ repopilot

────────────────────────────── RepoPilot ──────────────────────────────
  Directory: /your-project
  Model:     openai/doubao-seed-evolving
  Sandbox:   local
  Approval:  auto

Type /help for commands, /exit to quit.

repopilot> fix the failing test in test_auth.py
> read_file(path=test_auth.py)
> bash(command=python -m pytest test_auth.py -v)
> edit_file(path=auth.py, ...)
> bash(command=python -m pytest test_auth.py -v)

  All tests pass. Fixed the token validation bug in auth.py line 42.

repopilot> /exit
Goodbye.
```

## Features

- **Claude Code / Codex CLI style REPL** — `cd project && repopilot`, start chatting immediately
- **Pure ReAct agent loop** — single model does all thinking (no multi-agent overhead)
- **Persistent multi-turn conversation** with automatic context compaction
- **Layered memory system** — global + project `REPOPILOT.md` files (like CLAUDE.md)
- **Cross-session resume** — `/resume` to continue where you left off
- **Docker sandbox** with CPU/memory limits, optional network isolation
- **4 approval modes**: auto / confirm / edit-only / deny
- **Dangerous command blacklist** (rm -rf /, curl|sh, force push, credential theft)
- **10 built-in tools**: read/write/edit/grep/glob/list_dir/bash/run_python/repo_tree/finish
- **tree-sitter repo map** — code structure overview without reading every file
- **Circuit breaker + exponential backoff retry** for LLM calls
- **Cross-platform** — Windows/Linux/macOS with automatic Unix→Windows command translation
- **Any OpenAI-compatible LLM** — use your own API key (Volcengine ARK, DeepSeek, OpenAI, vLLM, etc.)
- **No RAG / no vector database** — deterministic grep/glob/tree-sitter retrieval is faster and more accurate for code

## Installation

```bash
# Clone and install
git clone https://github.com/yourusername/repopilot.git
cd repopilot
pip install -e .
```

On first run, you will be prompted for:
- Model name (e.g. `openai/doubao-seed-evolving`, `openai/gpt-4o`, `openai/deepseek-chat`)
- API key (sk-...)
- Base URL (for providers other than OpenAI, e.g. `https://ark.cn-beijing.volces.com/api/v3`)

Or set environment variables:
```bash
export REPOPILOT_MODEL=openai/doubao-seed-evolving
export REPOPILOT_API_KEY=sk-your-key
export REPOPILOT_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
```

## Usage

### Interactive mode (recommended)
```bash
cd your-project
repopilot                    # uses current directory, local sandbox, auto approval
repopilot -r ../other-proj   # specify project directory
repopilot --sandbox docker   # run in Docker container
repopilot --approval-mode confirm  # confirm before writes/executions
repopilot -m openai/gpt-4o   # override model
```

### One-shot task mode
```bash
repopilot chat "fix the bug in auth.py"
repopilot chat "add --verbose flag to cli.py" -r ./myproj
```

### Slash commands
| Command | Description |
|---------|-------------|
| `/exit`, `/quit` | Exit |
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

Global memory lives at `~/.repopilot/REPOPILOT.md` and applies across projects.

## Architecture

```
┌─────────────────────────────────┐
│  CLI (Typer + Rich)    REPL     │
├─────────────────────────────────┤
│  Agent Loop (ReAct)             │
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

## Evaluation

- **434 unit tests** pass
- **12/12 (100%) Success@1** on custom E2E benchmark (bug fix, new feature, multi-file, security, edge cases, regex, refactoring)
- **8/8 memory tests** pass (fact recall, project/global memory, compaction, cross-session resume)

Run tests:
```bash
python -m pytest tests/ -q
python -m eval.run --tasks 1-12     # E2E benchmark
```

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

## Requirements

- Python 3.10+
- (Optional) Docker Desktop for Docker sandbox

## License

MIT

## Acknowledgements

Built after studying Claude Code (Anthropic), Codex CLI (OpenAI), and the SWE-bench / SWE-agent research.
