# Code Agent 设计方案（RepoPilot）

> 版本：V1.0 · 日期：2026-07-09
> 状态：待审批 → 写实施计划 → 新项目开发
> 参考：OpenAI Codex CLI、Anthropic Claude Code、Princeton SWE-agent、SWE-bench harness、OpenHands

---

## 1. 项目定位

**一句话**：一个可在本地终端里自主完成真实软件工程任务的 Code Agent——读代码、搜索、改文件、跑命令、看测试结果、反思修复，直到任务完成或预算耗尽。能跑通 SWE-bench-lite 评测，有可量化的 resolve rate，形成 trajectory→评测→优化的反馈闭环。

### 1.1 为什么是这个项目（面向求职）

| 招聘高频要求 | 本项目如何命中 |
|-------------|---------------|
| Tool Use / 工具调用 | 10+ 个原子工具：文件读写、bash 执行、代码搜索、grep、git、test 等 |
| Planning / Reflection / 多步执行 | Plan→Execute→Observe→Reflect→Replan 主循环 |
| 执行反馈闭环 | Bash/Test 真实执行→解析 stdout/stderr/exit_code→Agent 据此决策 |
| Agent 评测体系 | SWE-bench-lite 300 题基准 + LLM-as-Judge + execution-based 验证 |
| 失败模式分析 | trajectory 全量记录 + 自动错误分类（定位失败/上下文遗漏/工具错误/循环） |
| Memory（短期/长期） | 跨轮工作记忆（你已有 L1/L2 可复用）+ 跨会话项目知识图谱（L3） |
| Code Agent 相关 | 核心就是 Code Agent，对标 SWE-agent / OpenHands / Devin 开源版 |
| 轨迹数据 → 反馈优化 | trajectory JSONL → bad case 筛选 → DPO 偏好对构造 → 小模型微调验证 |

### 1.2 核心理念：单租户本地优先（参考 Codex CLI / Claude Code）

**关键观察**：Codex CLI 和 Claude Code 都是**单租户本地 CLI**——用户在自己机器上 cd 进自己的项目，没有多用户并发，没有服务端，没有向量数据库。它们的"记忆"和"检索"机制出人意料地简单，但极其有效：

**Codex CLI 实际做法（来自对本地 `~/.codex/` 目录的逆向观察）**：
- **零向量数据库**：没有 Chroma、没有 FAISS、没有 embedding 索引，`.codex/` 下完全找不到任何向量相关文件
- **检索方式**：Agent 自主调用 grep/glob/read_file/bash 找代码——也就是 SWE-agent 的"主动探索"模式
- **会话记忆**：每次会话一个 JSONL rollout 文件（`sessions/YYYY/MM/DD/rollout-*.jsonl`），存完整消息轨迹
- **跨会话长期记忆**：`memories/raw_memories.md`——一个**纯 Markdown 文件**，后台异步从会话中提取 reusable knowledge / preference / failures，用 Markdown 结构化段落存储，启动时注入 system prompt。带 Git 版本控制（memories 目录是个 git repo）
- **记忆处理 Job 队列**：`memories_1.sqlite` 里有 `stage1_outputs`（原始提取结果）+ `jobs`（异步处理任务，带 lease/retry/watermark）——和你 RAG 项目的 outbox 设计思路完全一致
- **项目级指令**：每个项目根目录的 `AGENTS.md` 文件（就是我读到的那种）告诉 Agent 本项目的规范
- **会话索引**：`state_5.sqlite` 存 threads 表（会话元数据：标题、cwd、model、token 使用量等），不存消息正文
- **全局状态**：`.codex-global-state.json` 存 UI 状态等非关键数据

**Claude Code 实际做法（公开资料 + 逆向）**：
- 同样**无向量数据库**，检索靠 grep/glob/read + bash
- 会话存 `~/.claude/projects/<project-hash>/` 下的 JSONL
- 长期记忆靠 `CLAUDE.md` 文件（项目根目录或父目录，纯 Markdown，用户自己写 + Agent 自动建议更新）
- 有 compact 命令：上下文过长时，LLM 自己把历史压缩成摘要继续（就是你 RAG 项目 L2 summarizer 的思路，同步执行而非后台）
- 无 embedding、无 RAG、无向量库

**结论：单租户 CLI Code Agent 根本不需要向量 RAG。**

为什么：
1. **代码是精确符号系统**，grep 比 embedding 准确 10 倍且零幻觉
2. **单用户本地**，不存在"百万文档检索"的规模问题，grep 在 10 万文件以下都是秒级
3. **Repo Map（tree-sitter）+ grep + read** 三件套足够覆盖 99% 代码导航需求
4. **本地文件就是记忆**——Markdown 文件 + SQLite + JSONL 足够，不需要向量库
5. **向量库是服务端多租户场景的解决方案**（比如 Perplexity、客服 RAG），本地 CLI 完全用不上
6. SWE-agent 纯 grep+find+cat 跑 SWE-bench 就是证据

因此本项目方案重大调整：
- ❌ 移除 Chroma/向量检索/BM25/RRF/reranker 等 RAG 组件（不迁移 RAG 项目的检索引擎）
- ✅ tree-sitter Repo Map 作为"代码索引"（符号表，不是向量索引）
- ✅ grep/glob/read_file/list_dir 作为主力检索工具
- ✅ 记忆全部用本地文件：Markdown 文件 + SQLite 元数据 + JSONL 轨迹
- ✅ 记忆提取用后台 job 队列（你 RAG 项目的 SummarizerWorker 模式直接复用思路）
- ❌ code_search 语义工具从 Phase 2 计划中**完全移除**（不做）
- ✅ 跨会话长期记忆参考 Codex：`~/.repopilot/memories.md`（Markdown），后台异步从会话提取 reusable knowledge

### 1.3 核心可量化指标（写简历用的数字）

- SWE-bench-lite resolve rate（目标 20-35%，单模型无蒸馏基线，合理区间）
- 平均每个任务 steps / tokens / 成本
- 支持的工具数量、沙箱安全性（Docker 隔离）
- 支持的模型后端数量（OpenAI/Anthropic/DashScope/ARK/vLLM 本地）

---

## 2. 竞品分析与设计取舍

| 特性 | Codex CLI | Claude Code | SWE-agent | OpenHands | **本项目 RepoPilot** |
|------|-----------|-------------|-----------|-----------|---------------------|
| 交互形态 | CLI TUI | CLI TUI | CLI + harness | Web UI + CLI | **CLI 优先 + SDK + API**（TUI 作为进阶） |
| Agent Loop | 单步（非循环） | ReAct 循环 | ReAct + 格式约束 | ReAct + Multi-Agent | **Plan-Act-Reflect 循环**（见 §4） |
| 沙箱 | 本地目录（有 approval） | 本地 + Docker | Docker | Docker | **Docker 默认 + 本地模式（配置项）** |
| 上下文管理 | 动态压缩 | 动态压缩 | fixed window | fixed window | **token-budget 自适应 + 分层摘要**（复用你现有 L1/L2 思路） |
| 工具设计 | 通用 bash + edit | 专用 edit/write/grep | 专用 file tools | 专用 tools | **混合策略**：bash 万能 + 高频操作专用化（grep/edit/test）减少 token |
| 评测集成 | 无 | 无 | SWE-bench | SWE-bench + 多 benchmark | **内置 SWE-bench-lite harness**（开箱即跑） |
| 轨迹可视化 | 内部 | 内部 | 简单 | Web UI | **本地 HTML 报告 + SQLite trajectory DB** |
| 多模型支持 | 官方 | 官方 | LiteLLM | LiteLLM | **LiteLLM + 多后端熔断**（复用你现有 llm.py 思路） |

### 2.1 关键设计决策

1. **不做全自动 Multi-Agent**：SWE-agent 证明单 ReAct Agent + 好工具 + 好提示就能跑 SWE-bench；Multi-Agent（Reviewer/Planner/Executor 分角色）可以作为 Phase 3 加分项，不作为核心。
2. **工具设计：混合式**：给 bash 通用工具（万能备份），但把 grep/edit/read_file/test 做专用化（用结构化参数，省 token 且更可靠）。这是 SWE-agent 的核心经验。
3. **沙箱默认 Docker**：跑 benchmark 和处理陌生 repo 必须隔离；本地"我信任这个目录"模式允许直接执行（和 Codex CLI 的 approval 机制类似）。
4. **Loop 设计：Plan-Act-Reflect**，不是纯 ReAct 也不是纯 Plan-then-Execute。每 N 步触发一次 replan，提高长任务成功率。
5. **复用你 RAG 项目的代码**：`llm.py`（多后端+熔断+重试）、`conversation_store.py`（记忆 DAO）、`retrieval/engine.py`（代码检索 RIP）、`server.py` 的 lifespan 模式——这些直接带过来，不重写。
6. **LiteLLM** 作为统一 LLM 网关（支持 100+ 模型厂商），但保留你现有的 fallback/circuit-breaker 分层设计。
7. **不训大模型**，但做 trajectory→DPO 的数据闭环演示（Phase 4），用 Qwen2.5-Coder-1.5B/3B 小模型在 Colab/单卡上能跑就行，证明你理解 post-training 范式。

---

## 3. 技术选型

| 层 | 选型 | 理由 |
|----|------|------|
| 语言 | Python 3.10+ | SWE-bench/tree-sitter/Docker SDK/TRL 生态最完善 |
| Agent Loop | **自研 Plan-Act-Reflect 循环** | Code Agent 的精细控制（budget、reflection、compact 触发）需要完全掌控；SWE-agent 证明 500 行自研 loop 足够 |
| LLM 调用 | **LiteLLM**（统一 100+ 提供商接口）+ 自研分级/熔断/重试 | LiteLLM 处理各家 API 差异；自研层负责 fast/default/strong 三档路由、滑动窗口熔断、指数退避、多后端降级 |
| 流式输出 | **SSE / streaming**（LiteLLM stream=True） | CLI 实时看到 agent thought 和工具输出，体验必备 |
| 代码导航 | **tree-sitter**（Repo Map + 符号索引）+ grep/glob/read | 无向量库无 RAG。tree-sitter 提供 AST 级符号表；grep 是主力检索（零幻觉、秒级） |
| 沙箱执行 | **Docker SDK for Python**（默认）+ LocalSubprocess（trusted 模式）| Docker 提供 cgroup 资源限制/网络隔离/文件系统隔离；Local 模式方便日常用，靠权限审批兜底 |
| 权限控制（Permission）| **自研 Policy Engine**（危险路径/命令分类 + human-in-the-loop 审批）| Codex CLI 同款核心 UX。危险操作（rm -rf、写~/.ssh、外网访问）必须用户确认；安全操作自动放行 |
| CLI 框架 | **Typer** + **Rich**（彩色输出、Markdown 渲染、进度条、Live 刷新）| Python 最现代 CLI 框架，Rich 支持流式渲染 thought/tool_output 分块 |
| TUI 交互界面（Phase 2）| **Textual** | Python 原生 TUI，做分屏对话/文件预览/审批弹窗 |
| 斜杠命令 | 自研 `/command` 解析器挂载在 Typer 上 | `/compact`/`/rewind N`/`/model`/`/help`/`/clear`/`/approval-mode`/`/cost`/`/skills`/`/web on|off` |
| Hook 系统 | 自研生命周期钩子（pre-tool/post-tool/pre-compact/post-compact/on-finish/on-error）| 插件化基础。pre-tool 做权限检查/审计；post-tool 做日志/cost 统计/记忆提取触发 |
| Skill 系统（Skills）| Markdown SKILL.md + 动态加载 | 借鉴 Codex skills：每个 skill 是一个含 SKILL.md 的目录，按需注入 prompt；支持 skill-creator 创建、find-skills 搜索、load_skill 加载 |
| Sub-agent | 自研轻量子 agent 调度（同一个 loop，隔离 context）| `agent-tool` 调用生成子 agent，传入子任务 + 受限工具集 + token 预算，返回结果摘要给主 agent；支持 agent-team 并行 |
| 会话管理 | **JSONL rollout 文件**（轨迹）+ SQLite（元数据索引）| Codex 同款：sessions/YYYY/MM/DD/rollout-*.jsonl，state.sqlite 存 threads 元数据（支持 create/list/rewind/search） |
| 记忆系统 | L1 窗口 + L2 分级 compact（micro/tool/auto）+ L3 Markdown 长期记忆 | L3 为 ~/.repopilot/memories.md（人类可读可 git）；后台异步提取；项目级 REPOPILOT.md 自动加载 |
| Cost/Token 追踪 | 自研 CostTracker（Hook post-tool 汇总）| 实时统计 $ 和 token 用量，CLI 顶部/底部显示 |
| Trajectory 存储 | JSONL（按事件）+ SQLite（索引）+ Parquet（评测导出）| JSONL 方便 replay/调试；Parquet 方便评测数据分析和 DPO 训练 |
| HTTP API（Phase 2）| **FastAPI**（从 RAG 项目迁移）| REST API 供 Web UI/IDE 插件调用 |
| Web UI（Phase 3）| 单页 HTML + Vanilla JS | Trajectory replay、评测报告、skill 管理 |
| 评测 Harness | 自研（对接 SWE-bench-lite HuggingFace datasets）| 开箱即跑：checkout→patch→执行→测试→判定→分类→报告 |
| 包管理 | **uv** + pyproject.toml | 现代 Python 打包，快 |
| 日志 | **structlog**（结构化 JSON）| 方便后续分析 |
| 测试 | pytest | 核心单测 + Docker 沙箱 E2E |

## 4. 核心架构

### 4.1 高层架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                         User / CLI / SDK                        │
│   repopilot "fix the failing test in auth.py"                   │
│   repopilot run --repo ./myproj --task-file task.md             │
│   repopilot eval --benchmark swe-bench-lite --model qwen        │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Agent Core (agent/)                      │
│                                                                 │
│  ┌────────────┐   ┌───────────────┐   ┌──────────────────────┐  │
│  │  Planner   │──▶│  Executor     │──▶│  Reflector           │  │
│  │(task分解    │   │(ReAct loop    │   │(每N步/失败时触发:    │  │
│  │ 生成steps) │   │ thought→tool  │   │ 回顾轨迹→是否需要    │  │
│  │            │   │  →observation │   │ replan/停/重试)      │  │
│  └────────────┘   └───────┬───────┘   └──────────┬───────────┘  │
│                           │                      │              │
│                           └───────循环───────────┘              │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  Context Manager  (token budget 控制，自动压缩)          │    │
│  │  L0 system prompt / L1 最近N步原文 / L2 trajectory摘要   │    │
│  └─────────────────────────────────────────────────────────┘    │
└──────────────────────────┬──────────────────────────────────────┘
                           │ tool calls
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                         Tools Layer (tools/)                    │
│                                                                 │
│  ┌─────────┬──────────┬──────────┬──────────────────────────┐   │
│  │ File    │ Search   │ Execution│ Meta                     │   │
│  │ read    │ grep     │ bash     │ list_dir                 │   │
│  │ write   │ glob     │ test*    │ view_todos               │   │
│  │ edit    │ code_search│        │ ask_user*                │   │
│  │ (patch) │ (RAG)    │          │ finish                   │   │
│  └─────────┴──────────┴──────────┴──────────────────────────┘   │
│         ▲              ▲              ▲                         │
│         │              │              │                         │
│         ▼              ▼              ▼                         │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  Sandbox Layer  (sandbox/)                              │    │
│  │  ├─ DockerSandbox   (默认：容器内执行，隔离)              │    │
│  │  └─ LocalSandbox    (可选：本地 subprocess，带审批)       │    │
│  └─────────────────────────────────────────────────────────┘    │
└──────────────────────────┬──────────────────────────────────────┘
                           │ read/write
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Persistence Layer (store/)                    │
│                                                                 │
│  trajectory.db (SQLite)     │  runs/ 目录（JSONL 轨迹）         │
│  - runs: 每次执行元数据      │  reports/ HTML 评测报告           │
│  - steps: 每步详细           │  swe-bench-lite/ 数据集缓存       │
│  - tool_calls               │                                   │
│  code_index/ (Chroma+FTS5)  │  artifacts/ (patch文件等)         │
└─────────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Eval Harness (eval/)                         │
│  ├─ swe_bench.py    → SWE-bench-lite 一键跑                      │
│  ├─ metrics.py      → resolve_rate, avg_steps, cost             │
│  ├─ failure_classifier.py → 自动分类失败模式                     │
│  ├─ report.py       → 生成 HTML 对比报告                         │
│  └─ dpo_builder.py  → trajectory → DPO 偏好对（Phase 4）         │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 Agent Loop 详细设计（核心中的核心）

Plan-Act-Reflect 主循环，全程 streaming 输出：

```python
def run_agent(task: str, repo_path: str, config: RunConfig) -> RunResult:
    ctx = ContextManager(budget_tokens=config.max_tokens, budget_steps=config.max_steps)
    sandbox = create_sandbox(config.sandbox, repo_path)
    perm = PermissionEngine(policy=config.approval_mode)  # auto/confirm/deny
    tools = ToolRegistry(sandbox, ctx, perm)
    skills = SkillManager(skills_dir=config.skills_dir)
    hooks = HookManager()
    cost = CostTracker()
    trajectory = TrajectoryRecorder()
    sub_agents = SubAgentRunner(tools, ctx, config)

    hooks.register("post-tool", cost.on_tool_call)
    hooks.register("post-tool", trajectory.on_tool_result)
    hooks.register("pre-tool",  perm.check)
    hooks.register("on-finish", lambda r: memory_extractor.enqueue(ctx.thread_id))

    # Phase 1: Initial Planning
    plan = planner.initial_plan(task=task, repo_tree=sandbox.get_repo_tree())
    ctx.set_plan(plan)
    stream.event("plan", plan)

    # Phase 2: Execute Loop
    step = 0; consecutive_failures = 0
    while step < config.max_steps:
        step += 1
        messages = ctx.build_messages(task)
        # streaming LLM call
        try:
            response = llm.chat(messages=messages, tools=tools.get_schemas(),
                                stream=True, temperature=config.temperature)
            parsed = parse_response(response, stream=stream, error_repair=True)
        except ModelError as e:
            consecutive_failures += 1
            if consecutive_failures > 3: break
            continue
        trajectory.log_step(step, parsed)

        # Slash command 拦截（如 /compact /rewind）
        if parsed.is_slash_command:
            result = handle_slash_command(parsed, ctx=ctx, trajectory=trajectory)
            stream.event("slash_result", result)
            continue

        # finish 检查
        if parsed.action == "finish":
            verified = sandbox.verify(config.test_cmd) if config.test_cmd else True
            hooks.fire("on-finish", RunEvent(status="success"))
            return RunResult("success", trajectory, cost.summary())
            # 测试失败 → 继续

        # Tool 调用（带 hook + permission + streaming）
        for tc in parsed.tool_calls:
            # pre-tool hook：权限检查/审计
            decision = hooks.fire("pre-tool", ToolCallEvent(tc))
            if decision.denied:
                ctx.add_observation(f"Permission denied: {decision.reason}")
                continue
            if decision.needs_approval:
                ans = stream.approval_prompt(tc)  # human-in-the-loop
                if ans == "deny": continue
            try:
                result = tools.execute(tc.name, tc.args, timeout=config.tool_timeout,
                                       stream=stream)
                ctx.add_observation(format_result(result))
                hooks.fire("post-tool", ToolResultEvent(tc, result))
            except ToolError as e:
                ctx.add_observation(f"Tool error: {e}")
                consecutive_failures += 1

        # Sub-agent 支持：如果 parsed 调用了 agent-tool
        if parsed.has_sub_agent_call:
            sub_result = sub_agents.invoke(parsed.sub_agent_task,
                                           tools=parsed.sub_agent_tools,
                                           budget=parsed.sub_agent_budget)
            ctx.add_observation(f"Sub-agent result:
{sub_result.summary}")

        # Reflection 触发
        should_reflect = (step % config.reflect_every == 0
                          or consecutive_failures >= 2
                          or ctx.token_usage_ratio() > 0.8)
        if should_reflect:
            ref = reflector.reflect(task, plan, ctx.recent_steps(20), consecutive_failures)
            if ref.should_compress:
                hooks.fire("pre-compact", None)
                ctx.compress(ref.compact_level)  # micro/tool/auto
                hooks.fire("post-compact", None)
            if ref.should_replan:
                plan = planner.replan(task, ref, ctx); ctx.set_plan(plan)

        # Skill 动态加载：如果 LLM 请求 load_skill
        if parsed.load_skill:
            skill_prompt = skills.load(parsed.load_skill)
            ctx.inject_skill_prompt(skill_prompt)  # 下一轮可见

        if ctx.token_usage_ratio() > 0.95 or consecutive_failures > 5: break

    hooks.fire("on-error", None)
    return RunResult("incomplete", trajectory, cost.summary())
```

### 4.3 Permission Engine（权限控制，生产级 CLI 核心）

四种审批模式（用户可切换）：
| 模式 | 行为 | 场景 |
|------|------|------|
| `auto` | 安全命令自动执行，危险命令拒绝 | Docker 沙箱内/SWE-bench 评测 |
| `confirm`（默认）| 安全命令自动执行，危险操作需 y/n 确认 | 日常本地开发（Codex CLI 同款）|
| `edit-only` | 只允许读和编辑，不允许 exec | 高风险目录 |
| `always-deny` | 所有写/执行都拒绝 | 只读模式 |

危险操作判定维度：
- **dangerous-path**：`~/.ssh`、`/etc`、`.env`、`credentials`、`*id_rsa*` 等黑名单
- **dangerous-cmd 黑名单**：`rm -rf /`、`sudo`、`chmod -R 777`、`curl ... | sh`、`git push --force` 等
- **network**：默认 Docker 模式 network=none，本地模式 `curl/wget` 需要审批
- **scope 外写**：写入 repo 目录之外需要审批
- **资源滥用**：执行超过 timeout 的命令、单文件写入超过 5MB

审批交互支持 `y/n/a/d/e`：
- y = 仅这次允许
- a = 总是允许同类操作（本次会话内）
- n = 拒绝
- d = 拒绝并停止本次任务
- e = 编辑命令后再执行

### 4.4 Context Manager 设计（三级压缩，参考图中 micro/tool/auto-compact）

```
┌─────────────────────────────────────────────────────────────┐
│ L0 System Prompt (~800 tok, 固定)                           │
│   角色、工具规范、输出格式、REPOPILOT.md 项目指令            │
├─────────────────────────────────────────────────────────────┤
│ L0.5 Memory Injection（~2000 tok，新会话加载）              │
│   · memories.md 跨会话长期记忆（用户偏好/常见路径/经验）     │
│   · 当前 session 的已加载 skill prompt                      │
├─────────────────────────────────────────────────────────────┤
│ Task + Plan（~500 tok，随 replan 更新）                    │
│   原始任务 + 当前 plan（步骤清单 + 已完成/进行中标记）       │
├─────────────────────────────────────────────────────────────┤
│ Compacted Summary（L2，动态 ~1500 tok）                    │
│   已被压缩的早段历史摘要（micro-compact 或 auto-compact 产物）│
├─────────────────────────────────────────────────────────────┤
│ Recent Steps（L1，动态窗口 ≤ model_ctx - 3000 tok 预留）    │
│   最近 K 步 thought/action/observation 原文                 │
│   按 token budget 从新向旧取，超出靠压缩处理                │
├─────────────────────────────────────────────────────────────┤
│ Pending Tool Result（动态，截断处理）                       │
│   上一步 tool 返回（tool-compact 控制长输出截断）           │
├─────────────────────────────────────────────────────────────┤
│ Available Tool Schemas + Skill Schemas（~800 tok）         │
└─────────────────────────────────────────────────────────────┘
```

三级压缩策略（对应图中 micro/tool/auto-compact）：

| 级别 | 触发时机 | 压缩范围 | 成本 |
|------|---------|---------|------|
| **tool-compact** | 每次 tool 返回后自动应用 | 单条 tool 输出：超长 → head+tail 截断（默认 head 500 字 + tail 1500 字，中间 `...[truncated N lines]...`）；二进制/乱码自动过滤 | 0 LLM 调用，纯规则 |
| **micro-compact** | 最近 1-2 步的 observation 太长，或者 token 使用率达 70% | 只压缩最老的 3-5 步为 1-2 句摘要（不碰整体历史），用 chat_fast 小模型 | 1 次 fast LLM 调用，~200 tokens |
| **auto-compact** | token 使用率 >85%，或连续失败 2 次反思时触发 | 把整个 trajectory（除了最近 K 步和 summary 之外）压缩成结构化摘要：已完成动作、关键发现、文件位置、未解决问题 | 1 次 fast LLM 调用，~1000-2000 tokens |

关键细节：
- **文件分页**：`read_file` 默认每次 200 行，需要更多用 `offset` 继续读，避免一次塞整个文件。
- **Cost 实时统计**：每次 LLM/tool 调用后 post-tool hook 更新 `total_input_tokens`、`total_output_tokens`、`estimated_cost_usd`，CLI 底部实时显示。
- **Skill prompt 注入是临时的**：load_skill 加载的 skill 提示在当前任务有效，换会话需要重新加载（避免 prompt 污染）。

### 4.5 工具集设计

每个工具都有：
- **JSON Schema**（给 LLM 做 function calling）
- **Sandbox 执行逻辑**
- **结果格式化**（给 LLM 看的 observation）

#### 核心工具

| 工具名 | 功能 | 参数 | Phase |
|--------|------|------|-------|
| `read_file` | 读文件（分页，带行号）| `path`, `offset=0`, `limit=200` | 1 |
| `write_file` | 写新文件（覆盖）| `path`, `content` | 1 |
| `edit_file` | 精确 patch（old_string→new_string，Codex 同款）| `path`, `old_string`, `new_string` | 1 |
| `bash` | 执行 shell 命令（万能兜底+跑测试）| `command`, `timeout=30`, `workdir?` | 1 |
| `run_python` | 执行 Python 代码片段（隔离 subprocess，比 bash -c 更安全）| `code`, `timeout=10`, `deps?` | 1 |
| `grep` | **正则搜索（主力代码检索）** | `pattern`, `glob?`, `-i?`, `-n=True` | 1 |
| `glob` | 文件名通配匹配 | `pattern` | 1 |
| `list_dir` | 列目录树 | `path`, `max_depth=2` | 1 |
| `finish` | 宣告任务完成 | `summary`, `tests_passed?` | 1 |

#### Phase 2 工具

| 工具名 | 功能 | 参数 |
|--------|------|------|
| `run_tests` | 专用测试执行（自动识别 pytest/unittest/npm test，解析错误位置）| `path?`, `framework?` |
| `web_search` | 联网搜索（Brave/SearXNG）| `query`, `num=5` |
| `web_fetch` | 抓取网页内容（转 Markdown）| `url`, `timeout=10` |
| `git_diff` / `git_apply` | 查看/应用 patch | `path?` / `patch` |
| `ask_user` | 交互式澄清（CLI/TUI 询问）| `question`, `options?` |

#### Phase 3 工具（Skill/Sub-agent）

| 工具名 | 功能 | 参数 |
|--------|------|------|
| `load_skill` | 加载 skill 到当前会话（注入 SKILL.md 到 prompt）| `skill_name` |
| `spawn_agent` | 生成子 agent（隔离 context+tools+budget）| `task`, `tools?`, `budget_tokens?` |

**工具安全分级**（配合 Permission Engine）：
- **Tier 0（只读，自动放行）**：read_file / grep / glob / list_dir / web_search / web_fetch
- **Tier 1（写，confirm 模式下需审批）**：write_file / edit_file / git_apply
- **Tier 2（执行，需审批）**：bash / run_python / run_tests
- **Tier 3（高危，默认拒绝或显式 y）**：bash 含 sudo/rm -rf/curl|sh/写 ~/.ssh/外网访问


#### 进阶工具（Phase 2 加）

| 工具名 | 功能 |
|--------|------|
| `run_tests` | 专用测试执行（自动识别 pytest/unittest/npm test 等） |
| `git_diff` / `git_apply` | 查看 diff / 应用 patch |
| `ask_user` | 澄清需求（交互式 CLI 下用） |
| `search_docs` | 检索第三方库文档（Phase 3） |

### 4.6 Output Parser（格式修复重试）

LLM 返回 tool call 格式不稳定是 Code Agent 的大坑，做三层防护：

1. **Native Function Calling**：优先用模型的 tool_calls API（最可靠）。
2. **XML/JSON fallback**：模型不支持 function calling 时用 `<tool_call>{"name":"bash","args":{...}}</tool_call>` 格式（SWE-agent 经验：XML 比 markdown code block 更稳定）。
3. **错误修复**：解析失败时把错误信息追加到消息让模型自我修复，最多重试 3 次。

---

### 4.7 记忆设计：本地文件 + SQLite 异步提取（参考 Codex CLI）

单租户 CLI 的记忆不需要数据库服务端，完全用本地文件。参考 Codex `~/.codex/memories/` 的实际设计：

**存储位置**：
- **会话轨迹**：`~/.repopilot/sessions/<YYYY>/<MM>/<DD>/rollout-<ts>-<uuid>.jsonl`（每轮对话一条 JSONL）
- **会话元数据索引**：`~/.repopilot/state.sqlite`（threads 表，列：id/path/title/cwd/model/tokens_used/created_at/updated_at），便于快速列表/搜索历史会话
- **跨会话长期记忆**：`~/.repopilot/memories.md`（Markdown 文件，人工可读可编辑可 git 管理）
- **项目级指令**：`<project_root>/REPOPILOT.md`（或 `.repopilot.md`），Agent 进入项目时自动加载（类似 Codex 的 AGENTS.md / Claude 的 CLAUDE.md）
- **后台 Job 队列**：`~/.repopilot/jobs.sqlite`（记忆提取的异步任务，outbox 模式，复用你 RAG 项目的 SummarizerWorker 架构）

**三层记忆架构（简化版，适配单租户 CLI）**：

| 层 | 载体 | 触发 | 说明 |
|----|------|------|------|
| L1 工作上下文 | 当前对话 messages 列表 | 实时 | 最近 K 步 thought/action/observation 原文，受 context window 限制（256K tokens） |
| L2 上下文压缩 | `session_compact.py` LLM 压缩 | token 超 80% 时同步触发 | 把早期步骤压缩成摘要继续对话（类似 Claude `/compact`），不丢信息但降 token |
| L3 跨会话长期记忆 | `~/.repopilot/memories.md` | 会话结束后后台异步提取 | 从完整 trajectory 中提取：可复用知识、用户偏好、失败经验、常见路径；Markdown 结构化存储；新会话启动时自动注入 system prompt |

**L3 记忆提取流程（Codex 同款两阶段）**：
1. 会话结束（或用户显式 `/save-memory`）→ 在 jobs.sqlite 入队一个 `extract_memory` 任务
2. 后台 worker（复用 SummarizerWorker 架构）拿到任务：
   - Stage 1：调 `chat_fast` 分析整段 trajectory，提取结构化的 reusable knowledge / preference signals / failures / references（对应 Codex memories 库的 stage1_outputs 表）
   - Stage 2：把新提取的记忆和已有 memories.md 合并（去重+更新），写回 Markdown 文件
3. 下次启动新会话时，读取 memories.md 注入 system prompt（如果在 3000 tokens 以内全量注入，超长则按 cwd 过滤相关段落）

**为什么 Markdown 不用 SQLite/向量库存长期记忆**：
- Markdown 是**人类可读可编辑**的，用户可以直接打开改（像编辑 `.bashrc` 一样），也可以 git 版本控制
- 长期记忆的读取模式是"启动时一次注入"，不需要结构化查询，纯文本足够
- Codex 验证了这个设计，memories 目录就是个 git repo，用户可以 diff/rollback
- 面试加分：能解释为什么选择"朴素"的 Markdown 而不是"高大上"的向量库（YAGNI + 可解释 + 用户可控）

**项目级 REPOPILOT.md 约定**（类似 AGENTS.md / CLAUDE.md）：
- Agent 启动时自动向上递归查找 `REPOPILOT.md`，找到则把内容注入 system prompt
- 内容：项目约定、构建命令、测试命令、代码规范、目录说明
- Agent 可以在任务中建议更新这个文件（但不自动写，要用户确认）

## 5. 模块划分（新项目目录结构）

新项目名建议：**RepoPilot**（或你定）

```
RepoPilot/
├── pyproject.toml
├── README.md
├── .gitignore
├── .env.example
│
├── repopilot/                         ← 主包
│   ├── __init__.py
│   ├── cli.py                         ← Typer CLI 入口（含 slash-command 注册）
│   ├── tui/                           ← Textual TUI（Phase 2）
│   │   ├── __init__.py
│   │   ├── app.py                     ← TUI 主界面
│   │   └── widgets.py
│   │
│   ├── agent/                         ← Agent 核心
│   │   ├── __init__.py
│   │   ├── loop.py                    ← Plan-Act-Reflect 主循环（含 streaming/hook 调用）
│   │   ├── planner.py                 ← 初始规划 + Replan
│   │   ├── reflector.py               ← 反思触发与决策（含 micro/tool/auto 压缩决策）
│   │   ├── context.py                 ← Context Manager（三级压缩 + token 预算 + skill 注入）
│   │   ├── parser.py                  ← LLM 输出解析（function call / XML fallback / 格式修复）
│   │   ├── sub_agent.py               ← 子 agent runner（spawn/invoke/agent-team 并行）
│   │   ├── cost.py                    ← CostTracker（实时 $/token 统计）
│   │   └── prompts/                   ← Prompt 模板（YAML）
│   │       ├── system.md
│   │       ├── plan.md
│   │       ├── reflect.md
│   │       ├── compact_micro.md
│   │       ├── compact_auto.md
│   │       └── permission.md
│   │
│   ├── tools/                         ← 工具层
│   │   ├── __init__.py
│   │   ├── registry.py                ← ToolRegistry（Tier 分级 + schema 生成）
│   │   ├── base.py                    ← Tool 基类 / ToolResult
│   │   ├── file_tools.py              ← read_file/write_file/edit_file
│   │   ├── search_tools.py            ← grep/glob/list_dir
│   │   ├── exec_tools.py              ← bash/run_python/run_tests
│   │   ├── web_tools.py               ← web_search/web_fetch（Phase 2）
│   │   ├── git_tools.py               ← git_diff/git_apply（Phase 2）
│   │   └── meta_tools.py              ← finish/ask_user/load_skill/spawn_agent
│   │
│   ├── permission/                    ← 权限控制（核心）
│   │   ├── __init__.py
│   │   ├── engine.py                  ← PermissionEngine（auto/confirm/edit-only/deny 四模式）
│   │   ├── policy.py                  ← dangerous-path/cmd 黑名单 + safe-cmd 白名单
│   │   ├── approver.py                ← human-in-the-loop（CLI/TUI 审批弹窗 y/n/a/d/e）
│   │   └── patterns.py                ← 危险命令正则（rm -rf/sudo/curl|sh/写~/.ssh等）
│   │
│   ├── sandbox/                       ← 沙箱层
│   │   ├── __init__.py
│   │   ├── base.py                    ← Sandbox 抽象基类
│   │   ├── docker_sandbox.py          ← Docker 容器沙箱（默认）
│   │   └── local_sandbox.py           ← 本地 subprocess（trusted 模式，靠 permission 兜底）
│   │
│   ├── hooks/                         ← Hook 系统（插件化）
│   │   ├── __init__.py
│   │   ├── manager.py                 ← HookManager（register/fire）
│   │   └── builtin.py                 ← 内置 hook：审计日志/cost 统计/记忆提取触发
│   │
│   ├── skills/                        ← Skill 系统（Phase 3 基础+内置 Phase 2）
│   │   ├── __init__.py
│   │   ├── manager.py                 ← SkillManager（get/load/find）
│   │   ├── creator.py                 ← skill-creator（让 Agent 自己创建新 skill）
│   │   └── builtin/                   ← 内置 skills
│   │       ├── pdf/                   ← PDF 处理 skill
│   │       ├── code-review/SKILL.md
│   │       ├── web-search/SKILL.md
│   │       └── planning/SKILL.md
│   │
│   ├── llm/                           ← LLM 层（迁移+增强）
│   │   ├── __init__.py
│   │   ├── service.py                 ← LLMService（stream=True 支持 + LiteLLM + 熔断）
│   │   ├── tiers.py                   ← fast/default/strong 模型配置
│   │   ├── circuit_breaker.py         ← 滑动窗口熔断
│   │   ├── cache.py                   ← LLM Response Cache（同 prompt 缓存，Phase 2）
│   │   └── stream_handler.py          ← streaming 输出处理（Rich Live 渲染）
│   │
│   ├── code_index/                    ← 代码索引（纯符号，无向量）
│   │   ├── __init__.py
│   │   ├── repo_map.py                ← tree-sitter Repo Map 构建（常驻 prompt）
│   │   ├── symbol_index.py            ← 符号索引（文件→类/函数/变量位置映射，增量更新）
│   │   ├── ignore.py                  ← .gitignore 解析 + 默认跳过目录
│   │   └── tree_sitter_setup.py
│   │
│   ├── session/                       ← 会话管理（Codex 同款 JSONL+SQLite）
│   │   ├── __init__.py
│   │   ├── store.py                   ← JSONL rollout 读写
│   │   ├── index.py                   ← state.sqlite 元数据索引（threads 表 CRUD）
│   │   ├── rewind.py                  ← /rewind N（重放到第 N 步）
│   │   └── slash_commands.py          ← /compact/rewind/model/help/clear/approval-mode/web/cost/skills
│   │
│   ├── memory/                        ← 记忆
│   │   ├── __init__.py
│   │   ├── compact.py                 ← L2 三级压缩（micro/tool/auto）
│   │   ├── memory_extractor.py        ← 后台从 trajectory 提取 reusable knowledge
│   │   ├── memory_store.py            ← ~/.repopilot/memories.md 读写
│   │   └── project_context.py         ← 加载 REPOPILOT.md 项目指令
│   │
│   ├── eval/                          ← 评测 Harness（必须做）
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── swe_bench.py               ← SWE-bench-lite 一键跑（Docker per-task）
│   │   ├── metrics.py                 ← resolve_rate / steps / cost / latency
│   │   ├── failure_classifier.py      ← 6 类失败自动分类
│   │   ├── report.py                  ← HTML 报告（含 trajectory replay）
│   │   └── dpo_builder.py             ← trajectory → DPO 偏好对（Phase 4）
│   │
│   └── api/                           ← FastAPI（Phase 2，供 IDE/Web）
│       ├── __init__.py
│       └── server.py
│
├── tests/
│   ├── test_parser.py                 ← 输出解析 + 格式修复
│   ├── test_tools_unit.py             ← 工具单测（mock sandbox）
│   ├── test_permission.py             ← 权限引擎（黑白名单/模式/审批）
│   ├── test_context.py                ← token 窗口/三级压缩
│   ├── test_sandbox.py                ← Docker/local sandbox
│   ├── test_hooks.py                  ← Hook 系统
│   ├── test_loop_mock.py              ← Agent loop（mock LLM）
│   ├── test_repo_map.py               ← tree-sitter 代码地图
│   ├── test_session.py                ← JSONL/SQLite/rewind
│   ├── test_skills.py                 ← Skill 加载/创建
│   └── test_e2e_simple.py             ← E2E：在 fixture repo 完成简单任务
│
├── scripts/
│   ├── run_swe_bench_lite.py          ← 一键跑 SWE-bench-lite
│   └── generate_report.py             ← 生成 HTML 评测报告
│
├── skills/                            ← 全局 skill 目录（用户级）
│   └── (user-created SKILL.md dirs)
│
└── docs/
    ├── architecture.md
    ├── tools_reference.md
    ├── permission_guide.md
    ├── eval_guide.md
    └── resume_talking_points.md
```

总代码量估计：总代码量估计：
- Phase 1 核心：~4500 行 Python（含 Permission/Hook/Cost/Session/基础工具）
- Phase 2 评测+TUI+斜杠命令：~2500 行
- Phase 3 Skills/Sub-agent/Web：~2000 行
- Phase 4 DPO+Harness：~1500 行
- 总计约 10500 行（加测试约 15000 行）

---

## 6. 分阶段实施路线图

### Phase 1：核心骨架（~2 周）→ 能用，能跑通简单任务

| 子任务 | 预估 | 产出 |
|--------|------|------|
| 项目脚手架（uv init/pyproject/目录结构/Typer CLI 骨架）| 0.5 天 | `repopilot --help` 可用 |
| LLM 层：LiteLLM 封装 + 三档模型 + streaming + 熔断/重试 | 1 天 | 支持 qwen/deepseek/ark/openai，流式输出 |
| Sandbox 层：Docker + Local 双实现 | 1 天 | Docker 容器挂载 /workspace 能执行命令读写文件 |
| Permission Engine：4 种模式 + 黑白名单 + CLI 审批交互 | 1 天 | y/n/a/d/e 审批流 |
| Hook 系统：pre-tool/post-tool/on-finish/on-error 生命周期 | 0.5 天 | 可 register/fire 的 HookManager |
| 核心工具集（Tier0/1/2）：read/write/edit/bash/run_python/grep/glob/list_dir/finish | 2 天 | 每个工具有 JSON schema + 单测，grep 带行号，edit 精确匹配，bash 结果截断 |
| tree-sitter + Repo Map：符号提取 + 排序 + token 预算 | 1.5 天 | 中等 Python 项目生成 ≤5000 tok 代码地图 |
| Context Manager：L0/L0.5/L1 窗口构建 + tool-compact 截断 | 1 天 | 动态组装 messages，长输出自动 head+tail |
| Output Parser：function call + XML fallback + 格式修复重试 | 0.5 天 | 解析失败 3 次内自修复 |
| Agent Loop：Plan-Act-Reflect 主循环，带 streaming 输出 | 1.5 天 | 能跑完 thought→tool→obs→reflection 全流程 |
| Session 存储：JSONL rollout + SQLite 元索引 | 0.5 天 | 每次运行可 replay |
| Prompt 模板调优（system/plan/reflect） | 1 天 | 在 5 个 fixture 任务上手工验证 |
| 基础 E2E 测试（3-5 个简单任务）| 1 天 | 小任务 E2E 全绿 |
| Cost Tracker：实时 token/$ 统计（post-tool hook）| 0.5 天 | CLI 底部实时显示用量 |

**Phase 1 完成标准**：在一个 Python 项目里（<2000 行），给 Agent 简单任务（"fix the failing test_auth.py"、"add --verbose flag to cli.py"），Docker 沙箱内成功率 ≥60%，有 streaming 输出，有权限审批交互，JSONL trajectory 可 replay。

### Phase 2：生产化 + SWE-bench 评测（~1.5 周）→ 有数字写简历

| 子任务 | 预估 | 产出 |
|--------|------|------|
| 斜杠命令：/compact /rewind /model /help /clear /approval-mode /cost /web | 1 天 | CLI `/` 前缀命令全可用 |
| Session rewind：基于 JSONL 重放到第 N 步 | 0.5 天 | `/rewind 3` 可回到历史步骤 |
| L2 三级压缩：micro-compact/tool-compact/auto-compact | 1 天 | token 超限时自动压缩 |
| Phase 2 工具：run_tests/web_search/web_fetch/git_diff/ask_user | 1.5 天 | 测试自动识别，联网搜索可开关 |
| 项目级 REPOPILOT.md 自动加载（递归向上查找）| 0.5 天 | 类似 Codex AGENTS.md |
| L3 长期记忆：后台 memory_extractor + memories.md 写入 | 1 天 | 会话结束后台提取，下次启动注入 |
| LLM Response Cache（disk cache，相同 prompt 缓存）| 0.5 天 | 重复调用省钱 |
| Textual TUI：分屏（对话/文件/审批/进度）| 2 天 | 类 Codex CLI 终端体验 |
| SWE-bench-lite harness：Docker per-task，checkout→patch→test→判定 | 1.5 天 | `repopilot eval swe-bench-lite` 一键跑 |
| Failure classifier：6 类失败自动分类 | 1 天 | 自动标注失败原因 |
| HTML 评测报告（trajectory replay + 指标面板）| 1 天 | 可视化所有 task 结果 |
| Prompt + Repo Map 调优（SWE-bench 前 50 题迭代）| 2 天 | 初步 resolve rate（目标 15-25% 单模型基线）|

**Phase 2 完成标准**：SWE-bench-lite 跑出 resolve rate 数字，有 HTML 报告，CLI 有 TUI 界面，斜杠命令完备。

### Phase 3：Skill 系统 + Sub-agent + Web（~1.5 周）→ 生态完整

| 子任务 | 预估 | 产出 |
|--------|------|------|
| **Skill 系统核心**：load_skill/find_skills/get_skills 工具 + SKILL.md 加载器 | 1.5 天 | 动态注入/卸载 skill prompt |
| **skill-creator**：让 Agent 自己创建新 skill（成功 trajectory → 自动生成 SKILL.md） | 1 天 | Agent 能自生长技能库 |
| 内置 skills：pdf、code-review、web-search（包装已有工具为 skill）、planning | 1 天 | 3-5 个内置 skill |
| **Sub-agent**：spawn_agent 工具 + 隔离 context/toolset/budget | 1.5 天 | 主 agent 可派生子任务 |
| agent-team 并行：多个 sub-agent 并行执行后汇总结果 | 1 天 | 并行调研/测试等场景 |
| FastAPI HTTP API（迁移自 RAG 项目）| 1 天 | REST API 供 IDE 插件调用 |
| 轻量 Web UI：trajectory replay + 评测报告 + skill 管理 | 1.5 天 | 浏览器控制台 |
| Git 集成：自动建 branch + 生成 commit message | 0.5 天 | `repopilot commit` 一键提交 |
| 用户级 skills 目录：~/.repopilot/skills/ 支持自装 skill | 0.5 天 | 类 Codex skills 生态 |

**Phase 3 完成标准**：可加载/创建 skill，可 spawn 子 agent，有 Web UI。

### Phase 4：反馈闭环 DPO + Harness 工程化（~1.5 周）→ 命中"评测+训练"JD

| 子任务 | 预估 | 产出 |
|--------|------|------|
| **Harness 工程化**：可插拔 benchmark（SWE-bench-lite + 自定义 eval set）| 1 天 | 多 benchmark 支持 |
| LLM-as-Judge 自动评分：对 completion 质量做自动打分 | 1 天 | 除 execution-based 之外增加质量分 |
| Regression eval：模型/prompt 变更自动跑回归，避免能力退化 | 1 天 | CI 友好的 eval 命令 |
| trajectory 分析与筛选：提取高/低质量 trajectory，分类 bad case | 1 天 | 从 Phase 2 数据中提取偏好对 |
| DPO 数据构造：chosen/rejected 对 + prompt 模板 | 1 天 | DPO 数据集（ShareGPT 格式）|
| TRL DPO 微调（Qwen2.5-Coder-1.5B/3B，Colab A100 或本地 4090）| 2 天 | 微调后 checkpoint |
| 再跑 SWE-bench 对比 DPO 前后 resolve rate | 1 天 | 有 delta 数字证明闭环 |
| 总结训练 pipeline 文档 + 面试话术 | 0.5 天 | 面试能讲清数据闭环 |

**Phase 4 完成标准**：DPO 微调前后 resolve rate 有量化对比，eval 命令可 CI 化，完整"trajectory→eval→DPO→再评测"闭环。

---

## 7. 关键工程细节（面试能展开讲的点）

### 7.1 Repo Map 构建（tree-sitter）

```python
def build_repo_map(repo_path: str, max_tokens: int = 4000) -> str:
    """扫描 repo → tree-sitter 提取符号 → 紧凑代码地图。
    输出示例（类似 IDE 大纲）：
        src/auth.py:
          class AuthManager:
            def login(username, password)    # 处理用户登录
            def logout(session_id)
            def refresh_token(refresh_token) # 刷新过期token
            def _hash_password(pwd)         # (private) bcrypt哈希
        src/api/routes.py:
          def register_user(request)        # POST /api/register
          ...
    """
```
实现要点：
- **只提取签名和一行 docstring**，不提取函数体（控制 map 大小在预算内）
- 优先包含：含 TODO/FIXME 的文件、最近 git 改动的文件、与 task 关键词匹配度高的文件
- 按目录树分组输出，Agent 能快速定位文件
- 大 repo 用 ranked 排序（类似 CTags 的引用频率），只保留最相关的符号在 token 预算内
- 写操作后**增量更新**对应文件的符号，不重 parse 整个 repo

### 7.2 Docker 沙箱

```python
# 每个 task 一个容器，启动后挂载 repo 到 /workspace
container = docker.containers.run(
    image="repopilot-sandbox:python3.10",  # 预装 git/pytest/常用工具
    mounts=[docker.types.Mount("/workspace", repo_host_path, type="bind")],
    working_dir="/workspace",
    mem_limit="2g",
    cpu_period=100000, cpu_quota=200000,    # 2 CPU
    network_mode="none" if config.offline else "bridge",
    detach=True,
)
# bash 执行用 container.exec_run(cmd, timeout=30)
# 文件读写通过 docker API 的 put_archive/get_archive
```
- 跑 SWE-bench 时：每个 task 启动一个新容器，`git checkout <base_commit>`，然后让 Agent 工作，最后 `git diff` 生成 patch 跟 gold patch 对比。
- 安全：默认 network=none（防止 Agent 乱访问网络），cgroup 限制 CPU/内存，read-only 根文件系统（只挂载 /workspace 可写）。

### 7.3 SWE-bench-lite 评测流程

```
for each instance in SWE-bench-lite (300 条):
    1. 启动 Docker 容器，git clone repo，checkout base_commit
    2. 写入 problem_stmt（issue 描述）到 task.txt
    3. 先跑 FAIL_TO_PASS tests，确认它们确实 fail（验证基线）
    4. 调用 run_agent(task=problem_stmt, repo=/workspace)
    5. 跑 FAIL_TO_PASS tests + PASS_TO_PASS tests
    6. 如果 FAIL_TO_PASS 全 pass 且 PASS_TO_PASS 仍 pass → resolved
    7. 记录 trajectory 和结果
    8. 销毁容器

汇总：resolved / 300 = resolve_rate
```

### 7.4 edit_file 工具的精确匹配

```python
def edit_file(path, old_string, new_string):
    content = read(path)
    if old_string not in content:
        # 给出错误+上下文，让 LLM 修正参数
        # 用 difflib 找最接近的匹配，建议给 LLM
        return ToolResult(error="string not found", closest_matches=...)
    # 只替换第一个匹配（要求 old_string 包含足够上下文，至少 3 行）
    new_content = content.replace(old_string, new_string, 1)
    write(path, new_content)
    return ToolResult(ok=True, diff=unified_diff(old, new))
```
这是 Codex CLI 同款 edit 设计，比"全文重写"省 token 且冲突率低。

### 7.5 Trajectory 数据格式（为 DPO 准备）

每条 trajectory 存为：
```jsonl
{"role":"system", "content":"...", "ts":"..."}
{"role":"user", "content":"<task>", "ts":"..."}
{"role":"assistant", "tool_calls":[{"name":"bash","args":{"command":"ls"}}], "content":"<thought>", "ts":"..."}
{"role":"tool", "name":"bash", "result":{"stdout":"...", "exit_code":0}, "ts":"..."}
...
{"role":"assistant", "tool_calls":[{"name":"finish","args":{"summary":"..."}}], "ts":"..."}
{"meta":{"task_id":"...", "status":"resolved", "steps":12, "tokens_in":8500, "tokens_out":1200, "cost":0.02}}
```
这个格式和 SWE-agent/OpenHands 兼容，方便后续喂给 TRL DPO。

---

### 7.6 Harness 工程（评测基础设施，必须做）

评测 harness 是项目的"度量衡"，也是面试里讲"如何评估 Agent 能力"的核心。

**核心抽象（Evaluation Harness 设计）**：
```python
class Benchmark(ABC):
    @abstractmethod
    def list_tasks(self) -> list[Task]: ...
    @abstractmethod
    def setup(self, task: Task, sandbox: Sandbox) -> Sandbox:  # checkout + 验证基线
        """准备环境：启动容器，git checkout base_commit，确认 FAIL_TO_PASS 确实 fail"""
    @abstractmethod
    def verify(self, task: Task, sandbox: Sandbox) -> EvalResult:
        """跑完 agent 后验证：执行 FAIL_TO_PASS + PASS_TO_PASS tests，判定 pass/fail"""

@dataclass
class Task:
    task_id: str
    repo: str
    base_commit: str
    problem_stmt: str         # issue 描述
    fail_to_pass: list[str]   # 应当从 fail 变 pass 的测试
    pass_to_pass: list[str]   # 应当继续 pass 的测试
    gold_patch: str           # ground truth patch（用于对比/分析）

@dataclass
class EvalResult:
    task_id: str
    status: str               # resolved / unresolved / error / timeout
    fail_to_pass_results: dict
    pass_to_pass_results: dict
    trajectory: Trajectory
    steps: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_sec: float
    generated_patch: str
    failure_category: str     # 见下
```

**6 类失败模式分类（failure_classifier.py）**：
1. `location_failed`：错误定位失败（没找到相关文件/函数）
2. `context_insufficient`：找到了但读的上下文不够（漏读了相关文件）
3. `tool_error`：工具调用错误（参数错/命令语法错/edit 字符串不匹配）
4. `patch_incorrect`：修改了代码但逻辑不对（测试仍 fail）
5. `loop_failed`：陷入循环（重复相同动作/反复改同一处）
6. `budget_exceeded`：token/step/时间预算耗尽

**可插拔设计**：Benchmark 抽象支持 SWE-bench-lite、自定义 eval set、HumanEval 等未来扩展；report.py 支持多 run 对比（DPO 前后、prompt 版本前后、模型前后）。

**命令行入口**：
```bash
repopilot eval swe-bench-lite   --model qwen3.6-flash   --subset lite   --max-tasks 50   --parallel 2   --output reports/run-001/
```

**产物**：
- `results.jsonl`：每个 task 的 EvalResult
- `report.html`：汇总指标 + 每个 task trajectory replay（可点击展开每步 thought/action/obs）
- `summary.json`：resolve_rate / avg_steps / avg_cost / P50/P95 latency / failure 分布

## 8. 依赖清单

```toml
[project]
name = "repopilot"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    # LLM
    "litellm>=1.40",            # 统一 LLM 接口（100+ 提供商，支持 stream）
    "openai>=1.30",
    # CLI/TUI
    "typer>=0.12",              # CLI 框架
    "rich>=13",                 # 终端美化（Live 刷新、Markdown、进度条、spinner）
    "textual>=0.80",            # TUI（Phase 2，分屏/弹窗）
    "prompt-toolkit>=3.0",      # CLI 交互输入（审批弹窗）
    # 沙箱
    "docker>=7.0",              # Docker SDK
    # 代码解析（无向量/无 embedding）
    "tree-sitter>=0.21",
    "tree-sitter-python>=0.21",
    "tree-sitter-javascript>=0.21",
    "pathspec>=0.12",           # .gitignore 解析
    # 存储
    "structlog>=24.0",          # 结构化 JSON 日志
    "pyyaml>=6.0",              # prompt/skill YAML 模板
    "jinja2>=3.1",              # HTML 报告
    "diskcache>=5.6",           # LLM response cache（Phase 2）
    # 网络工具（Phase 2 web_search/web_fetch）
    "httpx>=0.27",
    "beautifulsoup4>=4.12",
    "readability-lxml>=0.8",    # HTML→Markdown
    # 评测
    "datasets>=2.18",           # HuggingFace datasets（SWE-bench）
    "pandas>=2.0",
    "pygments>=2.17",           # 代码高亮（HTML 报告）
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio",
    "pytest-cov",
    "pytest-timeout",
    "ruff>=0.5",
    "ipython",
]
train = [                       # Phase 4 DPO
    "trl>=0.9",
    "transformers>=4.40",
    "accelerate>=0.30",
    "peft>=0.10",
]
```

---

## 9. 简历项目描述（预写，Phase 2 完成后用）

> **RepoPilot：生产级自主代码智能体（Code Agent）系统**
>
> - 参考 OpenAI Codex CLI / Anthropic Claude Code 架构设计，实现 Plan-Act-Reflect 单租户本地 Code Agent，支持任务分解、10+ 工具调用、自我反思、流式输出，可在 Docker 沙箱中自主完成真实 GitHub Issue 修复任务
> - **工程化完整度对标生产级产品**：实现 Permission Engine（4 种审批模式 + 危险路径/命令黑名单 + human-in-the-loop 交互）、Hook 生命周期系统（pre/post-tool 插件点）、三级上下文压缩（micro/tool/auto-compact）、Cost 实时追踪、斜杠命令（/compact /rewind /model 等）、Session rewind（基于 JSONL 天然支持）
> - **无向量 RAG 设计决策**：逆向 Codex 本地目录后确认其无向量库，采用 tree-sitter Repo Map（常驻 system prompt 的代码符号地图）+ grep/glob/read 精确工具作为代码导航主力，零向量依赖、零幻觉、秒级响应；SWE-agent 纯 grep 方案验证可行
> - **三层记忆体系**：L1 工作窗口 + L2 三级压缩（micro/tool/auto）+ L3 跨会话 Markdown 长期记忆（后台异步提取，人类可读可编辑可 git 版本控制，类 Codex raw_memories.md）；项目级 REPOPILOT.md 自动加载
> - **沙箱双层设计**：Docker 沙箱默认隔离（cgroup CPU/内存限制、network 可开关、read-only 根 FS、/workspace 挂载）+ Local 模式下靠 Permission Engine 审批兜底
> - **Skill 系统 + Sub-agent**：支持 SKILL.md 动态加载/创建（Agent 可自创 skill），spawn_agent 工具支持派生子 agent 隔离 context 并行执行任务
> - **SWE-bench-lite 评测 Harness**：Docker per-task 隔离执行 + execution-based patch 验证 + 6 类失败自动分类 + LLM-as-Judge 评分 + HTML trajectory replay 报告 + regression eval
> - **Trajectory→Eval→DPO 反馈闭环**：JSONL+SQLite 全量轨迹采集，筛选高质量轨迹构造 chosen/rejected 偏好对，用 TRL 跑 DPO 微调 Qwen2.5-Coder-3B，量化对比微调前后 resolve rate 变化
> - **多模型支持**：基于 LiteLLM 接入 100+ 模型提供商（OpenAI/Anthropic/DashScope/ARK），带 fast/default/strong 三档分级超时、指数退避、full-jitter 抖动、滑动窗口熔断、多后端自动降级、disk cache 省钱
> - 提供 Typer CLI + Rich 流式渲染 + Textual TUI 分屏界面 + HTML 评测报告

---

## 10. 风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| Docker 在 Windows 上配置麻烦 | 早期开发受影响 | Phase 1 开发先在 LocalSandbox 跑通，Docker 在 Linux/WSL2 上验证；SWE-bench 评测用 WSL2 或云服务器 |
| 模型 API 成本（跑 SWE-bench 300 题） | 可能花几百块 API 费用 | 先用 fast 档（doubao-flash/qwen-flash）跑通流程；SWE-bench 可以先跑 50 题小样本调参，再跑全量；本地跑 Ollama 也可以（慢但免费） |
| Prompt 工程需要大量调优 | Phase 2 可能卡住 | 参考 SWE-agent/OpenHands 开源 prompt（MIT 协议）作为起点，在 fixture 上小步迭代；不要从零写 |
| tree-sitter 多语言支持复杂 | 时间消耗 | Phase 1 只支持 Python，Phase 2 加 JS/TS，其他语言后续再说 |
| 一个人做时间不够 | —— | 严格按 Phase 切分，Phase 1+2 做完就有完整故事和数字；Phase 3/4 是加分项，时间不够可以只做部分 |

---

## 11. 关键设计取舍（面试高频）

### 11.1 为什么不用向量数据库/Chroma/RAG 做代码检索

详见 §1.2 和 §4.4，核心论据：
1. **竞品验证**：Codex CLI、Claude Code、SWE-agent、Aider 都不用向量检索做代码导航
2. **代码是精确符号系统**：grep 零幻觉，向量的"语义近似"在代码里是 bug 不是 feature
3. **单租户本地场景**：仓库 10 万文件以下 grep 秒级返回，不存在"百万文档检索"规模问题
4. **依赖成本**：向量库引入 embedding 模型（~100MB 下载）、索引构建时间、近似匹配误差，ROI 极低
5. **Repo Map 替代全局语义**：tree-sitter 符号表常驻 system prompt，Agent 不搜索就知道全局结构
6. **面试话术**："RAG/向量检索是服务端多租户文档检索场景的方案（百万文档），单租户本地 CLI 下代码是精确符号系统，grep+Repo Map 更准更快更简单，Codex 和 Claude Code 都是这么做的。我曾经想过用向量 RAG（因为上个项目就是做这个的），但逆向 Codex 本地目录后发现人家根本不用，SWE-bench 上纯 grep 也能跑通，所以做了 YAGNI 取舍。"

### 11.2 为什么不用 LangChain/LangGraph/AutoGen 等框架

面试经常问，准备好答案：

1. **LangGraph**：StateGraph 抽象对固定流程（如你 RAG 项目的 vision→search→evaluate→answer）很好用，但 Code Agent 的循环控制非常精细——什么时候 reflect、什么时候 replan、什么时候压缩上下文、格式失败如何修复——这些都是 if-else 状态机，硬塞进 LangGraph 的节点+边抽象反而增加复杂度和调试难度。核心 loop 自研 500 行内能完全掌控。
2. **LangChain Tool 抽象**：太重，Tool 接口和 callback 系统对 Code Agent 这种 tight loop 增加不必要的 overhead。自研 ToolRegistry 200 行搞定。
3. **AutoGen/CrewAI Multi-Agent**：Phase 1-2 用单 Agent 就够了（SWE-agent 证明）。Multi-Agent 作为 Phase 3 加分项，用简单的消息传递实现即可，不需要框架。
4. **LiteLLM** 是唯一引的第三方 LLM 库，只用于统一各家 API 格式；熔断/重试/分级超时这些工程化逻辑自研。

**回答话术**："我参考了 SWE-agent 的经验，他们也是自研 loop 不用框架。Code Agent 的核心复杂度在 loop 控制和工具设计上，不是在图编排上——这也是为什么 SWE-agent 的 500 行核心 loop 能跑 SWE-bench。"

---

## 12. 与现有 RAG 项目的关系

- **代码复用**（估计 20-25% 代码可迁移，主要是工程基建）：
  - `src/agent/llm.py` → `repopilot/llm/`：多后端、分级超时、熔断、重试（底层换 LiteLLM，外层工程逻辑迁移）
  - `src/memory/summarizer.py` → `repopilot/memory/memory_extractor.py`：后台 daemon worker + outbox jobs 表 + 指数退避重试的设计模式直接复用（具体 SQL/prompt 重做）
  - `src/memory/conversation_store.py` → `repopilot/memory/session_store.py`：SQLite DAO 模式（RLock + WAL + 可重入事务）复用，schema 针对 trajectory 重做
  - `src/api/server.py` 的 lifespan 模式 → CLI 启动/关闭钩子

- **不迁移的部分**（重要！）：
  - **整个 `src/retrieval/` 检索引擎（Chroma/BM25/RRF/reranker）完全不迁移**——单租户 CLI Code Agent 不需要向量 RAG，grep+read+Repo Map 足够
  - LangGraph 图（graph.py）：Code Agent 自研 Plan-Act-Reflect loop 替代
  - PaddleOCR/文档解析/pipeline/cleaning/chunking：Code Agent 不处理 PDF 文档入库
  - 前端 app.html：新项目 CLI/TUI 优先
  - embedding 模型相关依赖（sentence-transformers 等）：不引入

---

## 附录：推荐参考资料

1. **SWE-agent** (Princeton)：https://github.com/princeton-nlp/SWE-agent —— 核心参考，prompt 设计和工具设计直接借鉴
2. **OpenHands**：https://github.com/All-Hands-AI/OpenHands —— sandbox 设计参考
3. **SWE-bench**：https://www.swebench.com/ —— 评测基准
4. **Aider**：https://aider.chat/ —— repo map 和 edit 工具设计参考
5. **Codex CLI**（你正在用）：体验 Codex 的 approval 机制、edit 工具设计
6. **Claude Code**：体验 Plan 模式和 Bash 集成
7. **tree-sitter 代码切分**：参考 Aider 的 repomap 和 tree-sitter 实现
