# PaperPilot Architecture & Design Decisions

> 2026-10-16 文件上下文契约 v3：读取按行及字符列分页，编辑显式携带版本并校验本会话已读范围；harness 只通知过期，不恢复源码。轮内只在用量水位批量折叠。见 [文件版本与上下文](TOOL_CONTEXT_AND_EDIT_RECEIPTS.md)。

## 当前执行内核（2026-10-14）

当前内核以 `AgentKernel.run(KernelHost, KernelRequest) -> KernelResult` 为策略入口，默认实现为 `FunctionCallingKernel`。工厂长期持有稳定策略实例；每次请求创建宿主上下文。`AgentRuntime` / `FunctionCallingRuntime` 保留为兼容适配，可用 `MeteredRuntime` 包装。详细契约、循环行为、失败语义和剩余耦合见 [内核架构笔记](KERNEL.md)。

已落地：`kernel.AgentKernel` 通过 `KernelHost` 类型化端口与 `runtime.RuntimeHost` 交互，`run_agent(..., kernel=...)` 可注入替代思考策略。上下文、参数校验、模型传输和工具执行均由宿主承担；旧运行时接口保留为兼容适配。

当前模块职责见 [MODULE_GUIDE.md](MODULE_GUIDE.md)；本页包含历史设计，部分模块已移除。

2026-10-16：工具完整结果和已完成调用参数独立归档；文件工具声明最新读取片段及历史摘要策略，宿主整理消息，Kernel 保持通用。代码实现前检索读取当前实现由工具 usage 注入，不新增读取许可或人工确认；编辑回执包含完成状态、读回核对及变更范围。详见[工具上下文与编辑回执](TOOL_CONTEXT_AND_EDIT_RECEIPTS.md)。

2026-10-16：恢复执行段通过持久交接记录绑定新 S 与执行日志，先提交新指针再释放旧引用；取消状态同步落盘。工具可确认的网络失败由工具层转为失败观察并回灌上下文，模型自身断网才进入宿主等待恢复；副作用未知仍保留核对屏障。细节见[网络恢复](NETWORK_RECOVERY.md)。

已实施基线简化（2026-10-15）：新轮在取得工作区凭证后，以当前文件与 index 为基线，人工轮间修改作为有效输入。旧 E 缺失和未应用的冲突不再阻断安全新轮；新 S 成功后清除旧计划、令牌及消息，独立保留冲突产物。实际残留写入、applying / partial 撤销及隔离标记仍受保护。默认内核现支持网络中断保存、有限自动续跑及重启后的步骤恢复；未知工具副作用仍保守核对。契约见[网络恢复](NETWORK_RECOVERY.md)及[分批计划](WORKSPACE_BASELINE_AND_RECOVERY_PLAN.md)。

范围收敛：默认共享工作区串行执行，在本轮实际修改前取快照，结束后保留文件结果；打开窗口不固定基线。独立 worktree 作为显式可选隔离，既有绑定维持；多窗口同位置并发不作为本阶段核心需求。“并入结果”不隐含自动 Git 提交或跨分支合并。

2026-10-15：工作区操作在 Git 前保存完整意图，中断后通过证据审阅恢复；整轮快照按实际 worktree 隔离，回退逐项还原文件及原始暂存区，强制审阅令牌并记录可核对的中断进度。职责、入口与限制见[工作区创建与回退](WORKSPACE_RECOVERY_AND_ROLLBACK.md)。

2026-10-15：快照改为最近一轮保留，下一轮新基线成功建立后释放旧引用；正在应用/部分失败的撤销阻止换轮，未应用冲突可自动过期。宿主 rollback 服务以执行结束 E 为祖先，将当前 C 与执行前 S 做反向三方合并，按文件恢复并独立处理 index；proposal、CLI 和 HTTP 共用审阅/应用契约，严格快照 restore 继续独立。详见[三方合并与选择性撤销](SELECTIVE_UNDO_PLAN.md)。

2026-10-15：旧引用释放后，git_maintenance 按 common Git 目录归并持久化维护任务，独立隐藏进程保留宽限期执行 GC。服务归属应用宿主，跨会话共享 worker，不占用内核循环或会话任务的排空流程；失败退避并可启动恢复，不强制清理其他窗口资源。见[Git 后台回收](STORAGE_POLICY.md)。

2026-10-15：Git 回收入队/执行共用宿主环境隔离，避免继承仓库选择变量导致目标偏移。后台结束封存接入 checkpoint_events 持久化 outbox：前台关闭、任务终态和 ready 消息独立关联；最后一个受控 writer 解锁前固定结束证据，消费者幂等发布，接入/启动可重放。缺少可信捕获证据时保留并拒绝，不以当前人工文件猜测 E。见[后台快照消息](BACKGROUND_TASKS.md)。

2026-10-15：内置计划和沙箱状态已迁为插件声明的类型化字段；会话保存、任务恢复、子代理隔离与资源清理的现行约定见 [工具与 State 契约](TOOL_STATE_CONTRACTS.md)。

2026-10-15：Docker 统一按单轮任务持有；宿主通过槽位 OS 锁、创建意图记录和容器标签管理崩溃回收，Git 查询通过 filesystem 插件的 git_inspect 注入，不向容器映射共享 Git 元数据。资源归属、恢复入口和真实验收记录见 [Docker 归属与验收](DOCKER_OWNERSHIP_AND_ACCEPTANCE.md)。

2026-10-15：后台命令由宿主 BackgroundTasks 服务持有，通过 Shell 插件注入查询/取消工具，运行时数据库记录任务归属和取消请求；会话关闭、插件停用及应用退出先停止接入、取消再排空，契约和控制入口见 [后台任务生命周期](BACKGROUND_TASKS.md)。

> **注（最新迭代）**：V4-B 的 **Tier B 语义记忆已整体移除**；自动记忆写入改为**任务情景记忆**（`plan.py` / `plan_pipeline.py`，plan-as-memory）。下文 V4-B 保留为历史设计记录。

## V4 变更摘要 (2026-09)

> 本页主体记录 V3 架构与 10 个决策。V4 在记忆与诊断两处做了结构变更，增量记录于此，正文历史决策保留不动。

### V4-A 论文检索：向量 RAG → grep 工作记忆

| 维度 | V3 | V4 |
|------|----|----|
| 论文事实源 | ChromaDB 论文chunks + workspace md 副本 | **仅** `workspace/papers/*.md`（唯一 grep 事实源） |
| 本地检索 | hybrid_search（向量+BM25+RRF） | `retrieval.grep_papers()` 关键词/jieba，复用文件沙箱 |
| 落地时机 | read_paper 即自动摄入 | **两阶段**：先入 `.research-agent/tmp/papers/`（隔离临时区）→ LLM 确认后再 `persist=true` 晋升正式区 |
| 向量库用途 | 论文检索 | **仅服务 Tier B 记忆**（`memory_units` collection） |

相关文件：`paper_store.py`, `retrieval.py`, `tools/arxiv_pdf.py`, `tools/builtin/retrieve.py`

### V4-B 记忆分层（工作记忆 vs 长期记忆）

```
Tier A  项目/论文 · 工作记忆 (grep)
  workspace/papers/*.md + progress.md + conversations/*.json  (per-project)
Tier B  个人/长期记忆 (agentic RAG · 向量 + 关键词)
  data_dir/memory/memory_units.db + Chroma "memory_units"
  内容 = 对话后"检索+摘要"提炼的 MemoryUnit（fact/preference/decision/
         task/dead_end/insight/reference/style）
  ⛔ 工具调用/工具结果/论文全文 不计入 Tier B
  写入: _maybe_distill 异步管道 + memorize 显式工具
  读取: agentic —— LLM 主动调用 search_memory 工具（不再自动注入记忆内容）；
        context 仅注入一行"何时用 search_memory/memorize"的元指引
```

相关文件：`memory/` 包（models/storage/vector/source/extractor/pipeline/retrieve）、`memory_tool.py`

### V4-C 故障检测与开发者报告

```
所有 emit() → EventRecorder 落 data_dir/logs/{trace_id}.jsonl（SSE 旁路）
  → RunMonitor 产出 fault（empty_streak/tool_loop/error_streak/...）
  → research-agent diagnose CLI / GET /api/diagnostics
  → 报告 data_dir/diagnostics/report-{ts}.md/.json
  → 高频故障回写 Tier B (kind=dead_end)
  → 回合末语义自评（RESEARCH_AGENT_SEMANTIC_CHECK=1，规则+轻量LLM）
```

相关文件：`diagnostics/` 包（recorder/monitor/summary/scan/report/feedback）、`validate.py`

### V4-D 可选功能 = 可插拔 Feature（轻量注册 + 静态一键卸载）

```
features/registry.py   # Feature 目录（id/label/core/depends/owned 文件+测试+数据目录/接线点）
CLI: research-agent feature list | enable <id> | disable <id> | uninstall <id>

机制:
  - core（harness/工具/治理/工作区/论文grep）不可卸载
  - 可选 Feature: memory_tier_b / diagnostics / mcp / knowledge_graph
  - enable/disable → config.yml features.<id>.enabled，core 接线点据此门控跳过
  - uninstall → 顶层引用扫描（AST，仅模块级 import）→ 无残留才删 owned 文件/测试/数据目录
  - 安全保证: 卸载前 AST 扫描，core 仍顶层 import 该 feature 模块则拒绝并列出引用点
```

相关文件：`features/` 包、`cli.py`（feature 子命令）；core 接线点均以 `features.is_enabled()` 门控。

---

## V3 整体架构（历史）

```
┌──────────────────────────────────────────────────────────┐
│                    用户界面层                              │
│  React (Vite) WebUI  │  pywebview Desktop                 │
│  ChatArea + Sidebar + WorkspaceSidebar + Graph            │
├──────────────────────────────────────────────────────────┤
│                    API 网关层                              │
│  FastAPI (SSE Streaming)                                  │
│  /api/chat  /api/projects  /api/papers  /api/tools        │
│  /api/workspace  /api/upload  /api/graph                  │
├──────────────────────────────────────────────────────────┤
│                    Agent 核心层                            │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Agent Loop (function calling)                     │  │
│  │    while True:                                     │  │
│  │      messages = build_context(state)               │  │
│  │      response = _call_llm_with_tools(messages)     │  │
│  │      if tool_calls:                                │  │
│  │        for each: dispatch → store result           │  │
│  │        continue                                    │  │
│  │      else: generate response → break               │  │
│  └────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────┤
│                    治理/反馈层                             │
│  guardrail(action) → 拦截危险动作                          │
│  validate_result(tool, data) → 确定性反馈                  │
│  validate_response(state) → 幻觉检测                      │
├──────────────────────────────────────────────────────────┤
│                    工具系统层                              │
│  ToolRegistry (可插拔: builtin/user/MCP/subagent)        │
│  ┌──────┬──────┬──────┬──────┬──────┬──────┐                    │
│  │论文层  │执行层  │文件层  │编排层  │记忆层  │MCP │                    │
│  │retrieve│shell_ │read   │spawn  │memorize│外部│                  │
│  │search  │exec   │write  │sub-   │(TierB) │工具 │                  │
│  │read    │       │ edit  │agent  │        │    │                  │
│  │update  │       │ glob  │       │        │    │                  │
│  │notes   │       │ grep  │       │        │    │                  │
│  │delete  │       │check_ │       │        │    │                  │
│  │paper   │       │tasks  │       │        │    │                  │
│  └──────┴──────┴──────┴──────┴──────┴──────┘                    │
├──────────────────────────────────────────────────────────┤
│                    存储层                                  │
│  SQLite (论文元数据/KG) │ Filesystem (项目/对话/工作区)     │
│  memory_units.db (Tier B, data_dir)  │ workspace/papers/*.md │
│  data_dir/logs/*.jsonl (诊断事件流)                        │
└──────────────────────────────────────────────────────────┘
```

## 10 个关键设计决策

### 决策 1: LangGraph → 自实现 while 循环 → Function Calling

| 阶段 | 方案 | 问题 | 决策 |
|------|------|------|------|
| V1 | LangGraph StateGraph | 调试困难，黑盒循环，不符合 harness 要求 | 重写 |
| V1.5 | 自实现 while + JSON 解析 | LLM 输出不稳定（markdown fence） | 升级 |
| V2 | litellm function calling | — | 当前 |

**理由**: Function calling 是 OpenAI 标准协议，Litellm 统一多供应商接入。不再需要解析 LLM 的自由文本 JSON。

### 决策 2: 硬编码 Actions → ToolRegistry 可插拔

**之前**: 4 个 if/elif 硬编码 action  
**现在**: 11 个 ToolSchema 注册表，`load_from_dir()` 自动导入用户工具

**理由**: 工具可增删改而不触 Agent 核心循环。功能去重：注册时检查 description 相似度 >80% 警告。

### 决策 3: Skill = Python Handler → Skill = Context Injection

**之前**: `literature_review_skill` 是 Python 函数，包揽搜索→阅读→生成全流程，LLM 变成传话筒。  
**现在**: `SURVEY_WORKFLOW` 注入到 system prompt，LLM 用自己的工具执行每一步。

**理由**: LLM 保留流程控制权。工具描述引导行为选择，不是代码预编排。

### 决策 4: 上下文固化 4000 tokens → 模型自适应

**之前**: `max_tokens=4000` 硬编码，`trim_messages` 一刀切截断  
**现在**: 按模型自适应上限（DeepSeek 64K / GPT-4o 128K / Claude 200K / Gemini 1M）

**分层注入顺序** (V4，见顶部 V4-B 记忆分层):
```
系统记忆 (BASE_PROMPT + 工具列表)
  → 项目记忆 (progress.md，即早期设计所称 accumulated_wisdom)
  → Tier B 全局记忆 (<Global Memory>，ROUTE 触发时注入，见 V4-B)
  → 对话历史 (压缩摘要 + 最近 10 轮)
  → Skill/Workflow 注入
  → 用户输入 (最后，最新鲜)
```
> 注：V1 文档/代码中的 `accumulated_wisdom` 字段未落地，实际项目记忆为 `progress.md`（`context.py` 读取最近 20 行）。

### 决策 5: `build_chat_context` + `messages` 分离 → 统一 messages

**之前**: 生成阶段用 `build_chat_context` 重建上下文，**丢失所有 read_paper 全文**  
**现在**: 生成阶段用 `messages`（包含所有工具调用结果）

**理由**: 发现"读 13 篇引用 4 篇"的根因是 `build_chat_context` 不包含 tool results。修复后 17 read 17 cited。

### 决策 6: Semantic Scholar → arXiv API

| 问题 | Semantic Scholar | arXiv |
|------|------|------|
| 搜索质量 | "RLHF 2025" → 过敏会议/印尼性别平等 | RLHF 相关论文 |
| 摘要完整性 | 大量论文无摘要 | 几乎所有论文有摘要 |
| API 稳定性 | 429 限流频繁 | 301 redirect (已处理) |

### 决策 7: 固定切块 → TF-IDF 语义切块

**之前**: 固定 200-800 tokens 切割，段落边界随机  
**现在**: 段落间 TF-IDF 余弦相似度检测主题边界，相关段落合并不分开

### 决策 8: 论文三地存储 → ChromaDB 统一 + workspace 副本

```
ChromaDB:
  {paper_id}_summary  → doc + metadata (title/authors/year/doi)
  {paper_id}_chunk_0  → doc + metadata (paper_id, chunk_index)

workspace/papers/{arxiv_id}.md  → 可读副本（Markdown）
```

**理由**: ChromaDB 是检索真相源，workspace .md 是用户可读副本。删除论文时 ChromaDB + workspace 同步清理。

### 决策 9: 前端架构 — 浮动窗口 → Claude 风格侧边栏

| 组件 | 功能 |
|------|------|
| ProjectSidebar | 左侧滑出，项目 CRUD + 工作区路径设置 |
| WorkspaceSidebar | 右侧可拖拽（180-600px），文件列表 + 预览 |
| ChatTabs | 多对话窗口切换 |
| PlanBar | 底部执行计划，自动打勾 |

**理由**: 浮动窗口遮挡聊天区、不可调大小。侧边栏 + 可拖拽是 opencode/Claude 的标准交互模式。

### 决策 10: 安全模型 — 三层纵深

| 层 | 机制 | 确定性 |
|------|------|------|
| 输入验证 | `guardrail.py`: 12-pattern 危险正则 + 路径穿越拦截 | ✅ mock 可测 |
| 反馈校验 | `validate.py`: 工具输出结构化校验 + 失败回灌 | ✅ mock 可测 |
| 凭据安全 | localStorage (Web) / env (Desktop) + CI 硬编码检查 | ✅ |

**理由**: 每层可用 MockLLM 确定性测试。移除 LLM 后剩 19 个 pass。

---

## 数据流

```
用户消息 → FastAPI SSE
  → build_context(state, registry, model_name) → 注入分层上下文
  → _call_llm_with_retry → _call_llm_with_tools(messages, tools, "auto")
  → LLM 返回 tool_calls:
      对每个 tool_call:
        → guardrail(action) → 拦截? HITL confirm_required → 60s 超时默认拒绝
        → validate_tool_params → 参数无效则注入错误
        → registry.dispatch(name, params, llm, state, emit) → ToolResult
        → 结果追加到 messages
        → file_write/file_edit: _auto_validate → py_compile/pytest/javac
  → 无 tool_calls:
      _stream_response → token-by-token → 前端
      _save_turn → _maybe_compress → _mark_waiting_if_needed
```


### 文件上下文最终收敛（2026-10-16）

不引入索引、缓存或片段放行。正常读取一次流式扫描获得正文与哈希，harness 仅用元数据提示过期；编辑校验真实内容版本、唯一 old_string 与已读范围，写前复检。拒绝区分 FILE_CHANGED / NO_MATCH / MULTI_MATCH，指导局部重读。现有执行恢复和工具证据存储保留，不增加文件缓存数据库。
