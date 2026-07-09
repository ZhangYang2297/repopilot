# RepoPilot

Local-first code agent inspired by Codex CLI / Claude Code.
Plan-Act-Reflect loop, Docker sandbox, tree-sitter repo map, SWE-bench harness.

## Install

```bash
pip install -e ".[dev]"
```

## Quick Start

```bash
repopilot --help
repopilot chat "fix the failing test in tests/test_auth.py" -r ./my-project
```
