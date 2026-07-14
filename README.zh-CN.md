<div align="center">

# RepoPilot

**终端中的本地优先 AI 代码助手。**

[English](README.md) | [中文](README.zh-CN.md)

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![PyPI version](https://img.shields.io/pypi/v/repopilot-agent.svg)](https://pypi.org/project/repopilot-agent/)

</div>

## RepoPilot 是什么？

RepoPilot 是一款受 Claude Code 和 Codex CLI 启发的命令行 AI 代码助手。进入任意项目目录，运行 `repopilot`，用自然语言描述你要做的事——RepoPilot 会在沙箱环境中自主完成读文件、搜索、编辑、运行测试、修复 Bug 等操作。

```
$ cd your-project
$ repopilot

────────────────────────────── RepoPilot ──────────────────────────────
  Directory: /your-project
  Model:     doubao-seed-evolving
  Sandbox:   local
  Approval:  auto

输入 /help 查看命令，/exit 退出。

repopilot> 修复 test_auth.py 中失败的测试
> read_file(path=test_auth.py)
> bash(command=python -m pytest test_auth.py -v)
> edit_file(path=auth.py, ...)
> bash(command=python -m pytest test_auth.py -v)

  测试全部通过。已修复 auth.py 第 42 行的 token 验证 bug。
```

## 核心特性

- **Claude Code / Codex CLI 风格交互** — `cd` 进项目目录，直接 `repopilot` 开聊
- **纯 ReAct 代理循环** — 单模型完成所有推理，无多代理开销
- **持久化多轮对话** — 支持自动上下文压缩，长对话不爆 token
- **分层记忆系统** — 全局 + 项目级 `REPOPILOT.md`（类似 CLAUDE.md）
- **跨会话恢复** — `/resume` 一键回到上次对话状态
- **Docker 沙箱** — 支持 CPU/内存限制、网络隔离
- **4 种审批模式**：auto（自动）/ confirm（确认）/ edit-only（仅编辑需确认）/ deny（全部拒绝）（默认 confirm）
- **危险命令黑名单** — 路径穿越检测、`rm -rf /`、`curl|sh`、强制推送、凭据窃取等一律拦截
- **10 个内置工具**：读/写/编辑/grep/glob/列目录/执行 bash/运行 Python/仓库树/结束任务
- **tree-sitter 代码地图** — 无需逐个打开文件即可看到代码结构
- **熔断器 + 指数退避重试** — LLM 调用更稳定
- **跨平台** — Windows / Linux / macOS，自动将 Unix 命令翻译为 Windows 命令
- **兼容任意 OpenAI 格式的 LLM** — 火山方舟（豆包）、DeepSeek、OpenAI、vLLM、本地模型等均可
- **无 RAG / 无向量数据库** — 确定性的 grep/glob/tree-sitter 检索更快更准确

## 安装

```bash
pip install repopilot-agent
```

或直接从 GitHub 安装最新版：

```bash
pip install git+https://github.com/ZhangYang2297/repopilot.git
```

推荐使用 `pipx` 做隔离安装（不污染全局 Python 环境）：

```bash
pipx install repopilot-agent
```

**环境要求**：Python 3.10+

### 首次运行

首次运行会引导你配置 LLM：

1. **模型名称**（如 `openai/doubao-seed-evolving`、`openai/gpt-4o`、`openai/deepseek-chat`）
2. **API Key**（sk-...）
3. **Base URL**（非 OpenAI 官方需要填写，如火山方舟为 `https://ark.cn-beijing.volces.com/api/v3`）

也可以通过环境变量配置：

```bash
export REPOPILOT_MODEL=openai/doubao-seed-evolving
export REPOPILOT_API_KEY=sk-your-key
export REPOPILOT_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
```

## 使用方法

### 交互模式（推荐）

```bash
cd your-project
repopilot                          # 当前目录，本地沙箱，写入/执行需确认
repopilot -r ../other-proj         # 指定其他项目目录
repopilot --sandbox docker         # 在 Docker 容器中运行
repopilot --approval-mode auto     # 自动审批（完全信任 agent）
repopilot -m openai/gpt-4o         # 切换模型
```

### 一次性任务模式

```bash
repopilot chat "修复 auth.py 中的 bug"
repopilot chat "给 cli.py 添加 --verbose 参数" -r ./myproj
```

### 斜杠命令

| 命令 | 说明 |
|------|------|
| `/exit`, `/quit` | 退出（Ctrl+C / Ctrl+D 同样有效） |
| `/help` | 显示帮助 |
| `/model [名称]` | 查看或切换模型 |
| `/approval [模式]` | 切换审批模式 |
| `/compact` | 手动触发上下文压缩 |
| `/clear` | 开始新对话 |
| `/cd [路径]` | 切换工作目录 |
| `/memory [笔记]` | 查看或添加记忆笔记 |
| `/resume [id]` | 恢复之前的会话 |
| `/sessions` | 列出最近的会话 |
| `/cost` | 显示 token 用量和费用 |
| `/status` | 显示当前配置 |

### 项目记忆（REPOPILOT.md）

在项目根目录创建 `REPOPILOT.md`，让 RepoPilot 获得持久化的项目知识：

```markdown
# 项目记忆

## 构建/测试
- 测试: python -m pytest tests/ -v
- 检查: ruff check .

## 约定
- 所有函数必须加类型注解
- 不要修改 migrations/ 目录下的文件
```

全局记忆位于 `~/.repopilot/REPOPILOT.md`，对所有项目生效。

## 配置

配置文件路径：`~/.repopilot/config.toml`

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

通过命令行管理配置：

```bash
repopilot config show          # 查看当前配置
repopilot config set model openai/gpt-4o  # 修改配置项
repopilot config init          # 重新运行配置向导
repopilot models               # 列出推荐模型
```

## 架构

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

## 内置工具

| 工具 | 说明 |
|------|------|
| `read_file` | 读取文件（支持指定行范围和偏移限制） |
| `write_file` | 写入文件（新建或覆盖） |
| `edit_file` | 查找替换编辑（字符串替换） |
| `grep_search` | 正则搜索文件内容 |
| `glob` | 按 glob 模式查找文件 |
| `list_dir` | 列出目录内容 |
| `repo_tree` | 显示 tree-sitter 生成的仓库代码地图 |
| `bash` | 执行 shell 命令（沙箱内） |
| `run_python` | 在隔离临时文件中执行 Python 代码 |
| `finish` | 标记任务完成，返回给用户 |

## 支持的模型提供商

通过 [LiteLLM](https://docs.litellm.ai/) 支持所有兼容 OpenAI 接口的模型：

- **火山方舟（豆包）** — 推荐，经过充分测试
- **OpenAI**（GPT-4o、GPT-4、o1 等）
- **DeepSeek**（deepseek-chat、deepseek-reasoner）
- **阿里通义千问**（qwen2.5-coder 系列）
- **智谱 GLM**（glm-4、glm-5 系列）
- **本地模型** — 通过 vLLM / Ollama / llama.cpp 等（任意 OpenAI 兼容服务端）
- **Anthropic Claude**（通过 LiteLLM）

## 开源协议

MIT 协议 — 详见 [LICENSE](LICENSE)。

## 致谢

本项目在研究 Claude Code（Anthropic）、Codex CLI（OpenAI）以及 SWE-bench / SWE-agent 论文的基础上构建。



