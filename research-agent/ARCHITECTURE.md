# PaperPilot Architecture & Design Decisions

## 整体架构

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
│  ToolRegistry (11 tools, 可插拔)                          │
│  ┌──────┬──────┬──────┬──────┬──────┐                    │
│  │论文层  │执行层  │文件层  │管理   │用户扩展│                  │
│  │retrieve│shell_ │read   │check_ │my_tools/             │
│  │search  │exec   │write  │tasks  │自动导入               │
│  │read    │       │ edit  │       │                     │
│  │update  │       │ glob  │       │                     │
│  │notes   │       │ grep  │       │                     │
│  └──────┴──────┴──────┴──────┴──────┘                    │
├──────────────────────────────────────────────────────────┤
│                    存储层                                  │
│  ChromaDB (向量)  │  SQLite (结构化)  │  Filesystem (工作区) │
│  论文全文+元数据    │  项目/对话/论文    │  代码/结果/日志      │
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

**分层注入顺序**:
```
系统记忆 (BASE_PROMPT + 工具列表)
  → 项目记忆 (accumulated_wisdom)
  → 对话历史 (压缩摘要 + 最近 10 轮)
  → 检索数据
  → Skill/Workflow 注入
  → 用户输入 (最后，最新鲜)
```

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
| 输入验证 | `guardrail.py`: 10+ 危险模式正则 + 路径穿越拦截 | ✅ mock 可测 |
| 反馈校验 | `validate.py`: 工具输出结构化校验 + 失败回灌 | ✅ mock 可测 |
| 凭据安全 | localStorage (Web) / env (Desktop) + CI 硬编码检查 | ✅ |

**理由**: 每层可用 MockLLM 确定性测试。移除 LLM 后剩 19 个 pass。

---

## 数据流

```
用户消息 → FastAPI SSE
  → build_context(state) → 注入分层上下文
  → _call_llm_with_tools(messages, tools, "auto")
  → LLM 返回 tool_calls:
      dispatch(name, params, llm, state, emit)
        → guardrail(action) → 拦截? return blocked
        → handler(params, llm, state, emit) → ToolResult
        → validate_result(name, data) → 反馈回灌
        → 结果追加到 messages
  → LLM 返回 text (无 tool_call):
      _generate_msgs(messages) → 清理上下文
      _stream_response → token-by-token → 前端
```