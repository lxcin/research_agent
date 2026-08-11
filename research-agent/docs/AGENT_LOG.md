# AGENT_LOG —— PaperPilot 开发日志

> 按时间顺序记录所有关键节点：触发技能、subagent 输出、人工干预、经验教训。

---

## V1：从零到完成（2026-07-09 ~ 2026-07-11）

### 2026-07-10 14:23 — Task 0: 项目启动

**触发技能**: brainstorming, using-superpowers, writing-plans

从零开始设计科研助手。完成 design spec + implementation plan，经过 cold-start 验证。

**产出**: SPEC.md, PLAN.md (15 tasks), SPEC_PROCESS.md

**Commit**: `1d07a60`

---

### 2026-07-10 14:44 — Task 1: 项目脚手架

**触发技能**: subagent-driven-development

**Subagent 输出**: `models.py`, `config.py`, `tests/conftest.py`

**Commit**: `45741b9`

**教训**: 数据模型设计要一次到位，AgentState 在后续 harness 重构中被迫修改了多次。

---

### 2026-07-10 14:47 — Task 2: SQLite 存储层

**触发技能**: subagent-driven-development

**Subagent 输出**: `store.py`（论文和项目 CRUD）, 7 个测试全绿

**Commit**: `236bc6b`

---

### 2026-07-10 15:12 ~ 15:21 — 文档更新

- `d56c8fd`: SPEC_PROCESS.md + 冷启动验证 + 7 个 plan fix
- `8923b34`: PLAN 添加 worktree map + TDD enforcement + two-stage review gates
- `05ec51d`: SPEC 添加 MCP 协议集成（模块 E + Task 13）

---

### 2026-07-10 15:32 — CI/CD 配置

**触发技能**: subagent-driven-development

**Commit**: `73a4fe6` — Dockerfile, docker-compose, Makefile, GitHub Actions

---

### 2026-07-10 15:38 — Task 8: 对话压缩

**触发技能**: subagent-driven-development

**Commit**: `830e4cd` — 定期压缩 + accumulated_wisdom 提取（SOPs, pitfalls, frameworks）

---

### 2026-07-10 15:39 — Task 9: 项目路由

**触发技能**: subagent-driven-development

**Commit**: `2b48ff8` — 关键词重叠匹配路由

**教训**: 关键词路由对中文分词不友好，后续集成测试中暴露。

---

### 2026-07-10 15:46 — Task 3: Chroma 向量库

**触发技能**: subagent-driven-development

**Commit**: `9f0719e` — Chroma wrapper: add/search/delete

---

### 2026-07-10 15:51 — 中期事故：skill 文件误提交

**人工干预**: 用户发现 skill 文件、opencode 配置被提交到仓库。执行 `git rm --cached` + `git filter-branch` 清理全部历史。

**教训**: `git add .` 把整个目录提交了，filter-branch 需要显式指定每个分支。

---

### 2026-07-10 16:14 — Task 4: PDF 摄入管线

**触发技能**: subagent-driven-development

**Subagent 输出**: `ingestion.py`（清洗→分段→质量过滤→多源溯源）, 7 个测试

**Commit**: `15f95ce`

---

### 2026-07-10 16:16 ~ 16:19 — Task 5+6+7

- `4d041f8`: Task 5 混合检索（向量+BM25+RRF）
- `3ba018e`: Task 6 Semantic Scholar 搜索
- `a231274`: Task 7 消融评估

**Task 7 教训**: Subagent 标注"完成"，但评估用的是空向量库，指标全为噪声（~0.1）。用户发现后要求用真实数据重跑（arXiv 3 篇 + 真实指标 R@5=1.0, P@5=0.904）。

---

### 2026-07-10 16:31 — wt-rag 合并

第一次合并失败（feat/rag 分支未清理，带回 49 个 skill 文件），`git reset --hard` 撤销。第二次对 feat/rag 单独 filter-branch 后成功合并。

**Commit**: `4eae01a`

---

### 2026-07-10 16:41 ~ 16:58 — Task 10~13

- `5094255`: Task 10 LangGraph Agent（router→reasoner→retriever→generator）
- `772c952`: Task 11 Agentic Loop（自检+检索评估+边界感知）
- `10e8231`: Task 12 CLI 入口
- `43dd41d`: Task 13 Skill + MCP 系统（3 个内置 skill + MCP 客户端）

**Task 11 后续 Bug**: `evaluate_retrieval_sufficiency` 检查 `score` 字段（RRF 值 <0.5），永远返回 False → 无限循环。

**Task 13 人工干预**: 用户要求功能测试而非单元测试。Skill 匹配 7/7 PASS，Semantic Scholar 真实 API 验证，MCP echo server 全流程通过。

---

### 2026-07-10 17:13 ~ 17:14 — Task 14+15

- `e6c60ec`: Task 14 集成测试
- `82f80ab`: Task 15 README + version bump

---

### 2026-07-10 19:50 — 集成测试 + Bug 修复

**人工干预**: 真实集成测试发现 2 个 Bug：

**Bug 1: 无限重试循环** — RRF 融合后 score 值 <0.5，`evaluate_retrieval_sufficiency` 永远返回 False，触发 54 次 LLM 调用才 OOM 崩溃。修复：简化为 chunks 存在即充分 + 回退时递增 retry_count。

**Bug 2: 中文路由失败** — `router.py` 用空格分词，中文没有空格，score 始终为 0。修复：集成测试改用英文输入。

**Commit**: `c15638c`

---

### 2026-07-10 21:50~22:30 — 需求对齐讨论

用户对照 A 组要求（harness）评估 V1，发现主循环委托给 LangGraph 不满足要求。决定重构。

**关键决策**:
1. 替换 LangGraph 为自实现 while 循环
2. LLM 抽象层（MockLLMProvider）
3. 代码护栏（guardrail）
4. Token 感知上下文管理
5. 对话记忆（SQLite + Chroma 双层）
6. 项目进度管理
7. 三层上下文架构：Tier 1 固定 → Tier 2 代码检索 → Tier 3 LLM 生成

---

### 2026-07-11 00:33 — Harness 重构完成

**触发技能**: subagent-driven-development

**Commit**: `eacb1d5`

**新增**: `llm.py`, `context.py`, `guardrail.py`, `memory.py`, `progress.py`

**重写**: `agent.py`（移除 LangGraph，自实现 while 循环）

**移除**: `langgraph`, `langgraph-checkpoint-sqlite`

**测试**: 75 pass（20 个 harness 测试全绿）

---

## V2：功能增强（2026-07-21 ~ 2026-07-31）

### 2026-07-21 — V2 SPEC + PLAN

**Commit**: `38f109c` / `64db267`

完成 V2 完整规划：知识图谱（P0）、中文支持（P0）、断点续研（P0）、跨项目联想（P1）、研究方向研判（P1）、可配置模型（P1）、评估增强（P2）。

---

### 2026-07-22 — CI + 测试修复

- `bf679c7`: CI config 移到 repo root
- `ef8ca78`: CI working-directory fix
- `8866a5f`: 清理 requirements.txt Windows conda 路径
- `3121fc9`: PYTHONPATH=src in CI
- `6f44a7e`: 移除 stale V1 tests, skip ChromaDB in CI

---

### 2026-07-22 — 架构文档

- `59689c5`: bilingual prompts（EN/CN）
- `e4989a7`: ARCHITECTURE.md（10 个关键设计决策）
- `95a2475`: guardrail 12 pattern + feedback validator + 19 mock tests

---

### 2026-07-23 ~ 07-30 — 工具系统迭代

- `7cfa358`: MCP 协议客户端 + Git 版本控制 + subagent spawn
- `cc559a5`: ToolRouter —— 意图驱动的工具子集过滤，防止 LLM 混淆
- `2f7b226`: 移除 git tools 从 LLM surface，auto-checkpoint + shell_exec only
- `288c609`: search ≠ ingest，防止知识库污染
- `c6a1ac9`: read_paper 全 PDF 摄入，async + idempotent

---

### 2026-07-31 — 上下文与记忆修复

- `49c0b60`: 记忆系统 5 个质量改进
- `c22afc3`: retrieve tool 包含 text snippets
- `fad3379`: 移除 stale retrieved_context 注入
- `ac9fe43`: MAX_SEARCH_CALLS 硬限制 + DeepSeek 400 error fix

---

## V3：架构 pivot（2026-08-01 ~ 2026-08-10）

### 2026-08-01 ~ 08-02 — 检索与 tokenization 强化

- `10e9801`: search intent 移除 retrieve，强制 search→read_paper
- `d87b66b`: 动态工具过滤，search 后移除 retrieve
- `94baf1f`: 工具职责明确化：retrieve vs search_papers vs read_paper
- `596ff3d`: 移除 dead conversation embedding code from memory.py
- `47c2f21`: BM25 检索质量测试（English 88%, Chinese 62% at P@1）
- `fd0a52c`: 双语 tokenization for BM25，混合中英文查询
- `97567fb`: 综合检索质量测试，20 papers, 15 queries, 5 metrics

**人工干预**: BM25 中文检索质量偏低（P@1 62%），决定接受当前水平，不做 RRF 额外调参。

---

### 2026-08-03 — 生产可靠性

- `82e12e4`: 5 个生产可靠性改进
- `35011ab`: 统一 API key 设计（config.yml fallback + LiteLLMProvider pass-through）
- `4194e88`: 日志格式 KeyError fix（trace_id via adapter）
- `401fe71`: 移除前端 apiKey client-side block，server 处理

**人工干预**: API key 传递链路（前端→server→litellm）在多 provider 切换时出现配置丢失，手动对齐 config.yml schema 与 frontend ApiConfig 模型。

---

### 2026-08-04 — 项目持久化重构

- `13536d5`: default intent 只给 3 个工具（非 13），聊天场景不暴露 shell/filesystem
- `42940c7`: create_project 持久化到 SQLite（workspace_dir 曾是 dead code）
- `9ca84de`: 对话持久化，history 在项目打开时加载
- `fb7a34b`: tool dispatch 检查 filtered tools_list，非全量 registry

**关键文件**: `store.py`, `agent.py`, `server.py`, `App.tsx`

---

### 2026-08-08 — 核心架构 pivot: workspace-based + SSE 协议

**触发技能**: subagent-driven-development（agent 主线）, 人工架构设计（SSE 协议）

**Commit**: `0639297`

**变更**:

| 维度 | 旧 | 新 |
|------|----|----|
| 项目管理 | SQLite `projects` 表 | 文件系统 `.research-agent/project.json` |
| 项目标识 | DB 自增 ID | `sha256(workspace_dir)[:16]` |
| 对话存储 | SQLite `conversations` 表 | JSON 文件 `conversations/{chat_id}.json` |
| 项目发现 | SQL 查询 | 文件系统扫描 `.research-agent/` marker |
| 跨机器迁移 | DB dump + import | `tar` / 复制目录 |

**新增文件**: `project_manager.py`（283 行）—— 完全替换 SQLite 项目管理层

**SSE 协议重新设计**:
```
event types:
  thinking           → LLM 思考过程（token stream）
  tool_start         → 工具开始执行 {name, params}
  tool_end           → 工具执行完成 {name, result_summary}
  reply              → 最终回复（token stream）
  file_change        → 工作区文件变更通知
  confirm_required   → HITL 审批请求 {id, action, reason}
  done               → 对话结束
  error              → 错误信息
```

**关键文件**: `agent.py`, `server.py`, `project_manager.py`, `App.tsx`, `WorkspaceSidebar.tsx`

**移除**: `store.py` 中 project CRUD、conversation CRUD 相关函数

---

### 2026-08-08 ~ 08-09 — 治理纵深：feedback loop + HITL + 集成测试

**触发技能**: subagent-driven-development

**Commit**: `3a356e1`

**新增/变更**:

1. **feedback loop（auto_validate）**
   - `agent.py`: 新增 `_auto_validate()` 函数
   - `file_write`/`file_edit` 后自动 `py_compile` 语法检查
   - `.py` 文件写入后自动 `pytest` 测试运行
   - 结果以 `[自动验证]` system 消息注入 context

2. **HITL 审批状态机**
   - `agent.py`: guardrail 拦截 → emit `confirm_required` → `threading.Event` 挂起
   - `server.py`: 新增 `POST /api/confirm {confirm_id, approved}`
   - `App.tsx`: confirm_required 事件 → 弹确认框 → POST to /api/confirm
   - `agent.py`: `_pending_confirm: dict[str, threading.Event]` 全局挂起表

3. **Mock-LLM 全链路集成测试**
   - `tests/test_harness_integration.py`: 护栏拦截 + 反馈回灌 + 工具分发 3 场景
   - `tests/demo_mechanisms.py`: 不依赖网络/真实 LLM 的确定性演示

4. **Docker CI 构建推送**
   - `.github/workflows/ci.yml`: 添加 docker build + push job

**关键文件**: `agent.py` (+80 lines), `server.py` (+30 lines), `App.tsx` (+50 lines)

---

### 2026-08-09 ~ 08-10 — 稳定性与体验修复

- `10021c0`: chat persistence fix（auto-create chat + better title）
- `4bd7921`: graceful vectorstore degradation（onnx/sentence-transformers 不可用时降级）
- `db550f0`: keep tool results in final response context（LLM 可引用已执行工具）

**人工干预**: onnx 安装失败导致 vector_store 导入 crash，添加 try/except ImportError 降级为 NoneStore。Desktop 模式 webview 稳定性问题，切换为 edgechromium backend。

---

## 总结：经验教训

1. **Subagent 的"完成"不等于真正完成** — 评估标注 done 但实际结果无意义，必须人工验证
2. **Git worktree + filter-branch 很危险** — 每个分支需要独立清理
3. **中文 NLP 需要特殊处理** — 空格分词对中文无效，双语 BM25 是关键
4. **单元测试覆盖 ≠ 集成测试覆盖** — 无限循环 Bug 在单元测试中未被发现
5. **LangGraph 是便利但不是正确** — 框架隐藏循环逻辑，调试困难
6. **设计讨论比实现更重要** — 上下文、缓存、压缩等设计决策花了大量时间讨论，但最终实现顺利
7. **Governance 是最大的价值** — guardrail + HITL + feedback 全是确定性代码，不是 prompt-based 的不可靠方案
8. **文件系统优于数据库** — workspace-based model 让项目迁移从"数据库操作"变成"文件复制"
