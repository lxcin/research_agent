# PaperPilot V4 — 记忆分层重构 + 故障检测与开发者报告 (PLAN)

> 版本: V4 | 状态: planning | 日期: 2026-09
> 目标:
> 1. 论文/项目检索**不再建向量库**，统一为工作记忆 **grep 读取**（workspace 内 `.md`）；
> 2. 新增 Tier B **个人/长期记忆**：向量数据库，内容来自**对话后检索+摘要**提炼的持久信息，**工具调用不计入 B**；
> 3. 故障检测 + **面向开发者/维护者的报告**：统一事件流 + `/api/diagnostics` + `research-agent diagnose` CLI。

---

## 0. 目标架构

```
┌──────────────────────────────────────────────────────────────┐
│ Tier A  项目/论文 · 工作记忆 (grep 模式，不建向量库)            │
│   workspace/papers/*.md   ← 论文唯一事实源                     │
│   search_papers: arXiv 检索 → 落盘 md                          │
│   read_paper: 读 md 全文                                       │
│   retrieve: 对 papers/*.md 做 grep/关键词检索（复用沙箱）        │
│   progress.md / notes / conversations 仍在 workspace 层        │
├──────────────────────────────────────────────────────────────┤
│ Tier B  个人/长期记忆 (agentic RAG · 向量数据库)               │
│   内容 = 对话后 "检索+摘要" 提炼的 MemoryUnit                   │
│         来源仅限 用户消息 + 助手最终答复 + 结论/偏好/坑          │
│         ⛔ 不含工具调用记录 / 工具结果 / 论文全文               │
│   存储 = Chroma collection "memory_units"（唯一向量库用途）      │
│   写入 = 回合后异步提炼 + memorize 显式工具                     │
│   读取 = ROUTE → RETRIEVE → RANK → INJECT                     │
├──────────────────────────────────────────────────────────────┤
│ 故障检测 + 开发者报告（面向维护者，非聊天界面）                  │
│   所有 emit → EventRecorder 落 JSONL (data_dir/logs/{trace})   │
│   monitor.py: 收敛/重复/空转检测 → fault 事件                   │
│   回合末语义自评（轻量）                                       │
│   CLI  `research-agent diagnose` → data_dir/diagnostics/       │
│   API  `GET /api/diagnostics`                                  │
└──────────────────────────────────────────────────────────────┘
```

分层原则：
1. **论文 = 工作记忆，grep 读**。任何新代码不得为论文建立向量/嵌入索引；现有 Chroma 论文检索链路退役。
2. **向量库只有一个用途 = B 记忆**。B 的记忆材料是"对话后提炼的摘要性事实"，**原始工具调用/结果/论文全文一律不进 B**。
3. **报告对象是开发者/维护者**：入口为 API + CLI，不做聊天页内报告；事件 JSONL 是唯一诊断原始数据源。
4. 一切可 Mock：新链路全部在 `MockLLMProvider` 下确定性测试，CI 可跑、无网络/真实 key 依赖。

---

## Phase M: 论文检索 grep 化 + 沙箱两阶段落地（改动最大，先行）

**已确认的论文落地模型（2026-09 拍板）**：

```
search_papers(query)          # 只拉 arXiv 摘要，不落文件（现状保留）
   ↓ LLM 依据摘要判断相关性
read_paper(paper_id)          # 阶段1：同步下载 PDF → 解析 → 写入隔离临时区
                              #        workspace/.tmp/papers/{id}.md （不碰正式区）
   ↓ LLM 在临时区读全文，确认"是否真是要长期检索的论文"
read_paper(paper_id, persist=true)   # 阶段2：临时区 → workspace/papers/{id}.md
                                     # = 正式持久化（唯一 grep 检索范围）
[未 persist]                  # 丢弃临时区，系统内零残留
```

| # | Task | 文件 | 验证 |
|---|------|------|------|
| M.1 | 论文目录语义：正式区 `workspace/papers/*.md` 为唯一可 grep 事实源；临时区 `workspace/.tmp/papers/`（gitignore）；`read_paper` 幂等（正式→临时→下载） | `tools/builtin/retrieve.py`、`tools/arxiv_pdf.py` | 单测：临时不落正式 / 二次读幂等 |
| M.2 | `read_paper` 支持 `persist: bool=false`：false=同步下载写临时区并返回全文；true=临时区晋升正式区并返回 | `tools/builtin/retrieve.py`、`arxiv_pdf.py` | 单测：persist=true 后正式区存在；未 persist 则无残留 |
| M.3 | `retrieve` 改为对 `workspace/papers/*.md` 的 grep/关键词检索（jieba 中英分词），返回 paper_id+行片段+评分 | `retrieval.py` 新增 `grep_papers()`；`tools/builtin/retrieve.py` | 单测：关键词命中/中文/评分排序 |
| M.4 | 旧论文向量链路闲置：retrieve/read_paper 不再 import Chroma/hybrid/BM25/`ingestion` 入库；论文主链路零向量依赖 | `retrieval.py`、`ingestion.py`、`vector_store.py`（import 面） | CI 全绿；主链路无向量 import |
| M.5 | 沙箱边界文档化：论文 grep 只读正式区 md；临时区与用户文件操作隔离 | `guardrail.py`、`tools/builtin/filesystem.py` | 单测：路径/边界 |
| M.6 | 旧测试改语义 + 新增 grep/persist 测试；`test_retrieval/test_ingestion/test_vector_store` 与主链路解耦 | `tests/` | pytest 全绿 |

> 备注：搜索质量由"向量语义"变为"grep 精确/关键词"。若后续要语义扩展，只能加到 B 记忆层（个人库），不再回论文层。SQLite papers 表/KG/upload API 属历史特性，M 阶段保持闲置不删除，以免破坏绿测与前端；其重构列入 V4 后续（Phase F.6+）单独评估。

---

## Phase A: Tier B 存储基础设施（向量库）

| # | Task | 文件 | 验证 |
|---|------|------|------|
| A.1 | `MemoryUnit` dataclass（scope/kind/text/source/importance/superseded_by/created/updated/embedding_id） | `src/research_agent/memory/models.py` | 单测 |
| A.2 | SQLite 后端：CRUD、supersede 链、importance/时间过滤、kind 枚举 | `src/research_agent/memory/storage.py` | 单测 |
| A.3 | Chroma collection `memory_units` 封装（懒嵌入、embedding 不可用降级 BM25/关键词，沿用现有降级模式） | `src/research_agent/memory/vector_store.py` | 单测：优雅降级 |
| A.4 | `MemoryManager` 门面（write/retrieve/consolidate/list_kinds） | `src/research_agent/memory/__init__.py` | 集成单测 |

产出：`data_dir/memory/memory_units.db` + Chroma `memory_units` collection，独立于任何 workspace。

---

## Phase B: Tier B 写路径（对话后检索+摘要，排除工具调用）

| # | Task | 文件 | 验证 |
|---|------|------|------|
| B.1 | 提炼源构造：回合结束收集 **仅** user 消息 + 助手最终答复 + progress/notes 增量，**显式过滤 tool/tool_result/工具动作** | `memory/source.py` | 单测：构造结果不含工具消息 |
| B.2 | EXTRACT：小模型"对话后检索+摘要"——先扫描对话选值得记的片段，再摘要为 MemoryUnit（kind: fact/preference/decision/task/dead_end/insight/reference/style） | `memory/extractor.py` | MockLLM：断言只提炼持久信息、不含工具细节 |
| B.3 | VERIFY：去重 + 冲突检测（SequenceMatcher 预筛 + 小模型定夺 duplicate/opposite/supersede，复用 `knowledge_graph.py` 模式） | `memory/extractor.py` | MockLLM：冲突链正确 |
| B.4 | 异步写队列（submit → 后台消费 → 入库），失败不炸主线程、可重试 | `memory/pipeline.py` | 单测 |
| B.5 | 接入点：`_save_turn` 后投递提炼任务；`config.memory.enabled` 整体开关 | `agent.py`、`config.py` | 集成测试：关闭时零行为差异 |
| B.6 | `memorize` 显式工具（category="memory"）：LLM/用户明确"记住…"时直接写入，同样只收陈述内容 | `tools/builtin/memory.py`、`builtin/__init__.py` | 单测：注册+dispatch |

> 与 `_maybe_compress` 解耦：提炼按轮/按 token 预算触发，压缩仍管上下文瘦身。

---

## Phase C: Tier B 读路径（agentic RAG 漏斗 + 注入）

| # | Task | 文件 | 验证 |
|---|------|------|------|
| C.1 | ROUTE：小模型一次调用判断是否需全局记忆并生成 queries（拦截"我记得…/你偏好…"类） | `memory/retrieve.py` | MockLLM |
| C.2 | RETRIEVE：向量+BM25 混合、RRF 融合（复用原 `retrieval.py` 算法），+ 时间衰减/importance 加权 | `memory/retrieve.py` | 单测 |
| C.3 | RANK + 预算裁剪（默认 ~1500 tok，可配） | `memory/retrieve.py` | 单测：超预算截断 |
| C.4 | 注入 `<Global Memory>` 块到 `build_context`（分层注入点 3~4 之间），带来源回链 | `context.py` | MockLLM 集成：注入顺序断言 |
| C.5 | `AgentState.memory_units` 记录命中结果 | `models.py` | 单测 |

---

## Phase D: 故障检测地基（统一事件流 + 监控）

| # | Task | 文件 | 验证 |
|---|------|------|------|
| D.1 | `EventRecorder`：所有 `emit()` 经其转发——实时推 SSE + 追加写 `data_dir/logs/{trace_id}.jsonl` | `trace_log.py`、`agent.py`、`server.py` | 单测：事件字段完整落盘 |
| D.2 | 会话 health summary：rounds/工具成功率/重试次数/连续 empty 峰值/注入 tokens/耗时 | `diagnostics/summary.py` | 单测 |
| D.3 | `monitor.py`：收敛检测上收——同名工具+相似 params 连续 N 次、空转、超限、自愈重试失败 → `fault` 事件 | `diagnostics/monitor.py`、`agent.py` | MockLLM 集成：转圈被识别 |
| D.4 | 事件模型规范：`event`/`fault`/`metric` 类型 | `models.py`、`trace_log.py` | 单测 |

---

## Phase E: 语义自评 + 开发者报告（API + CLI）

| # | Task | 文件 | 验证 |
|---|------|------|------|
| E.1 | 回合末语义自评（单次轻量调用 JSON）：引用是否来自实际 read_paper、答复与工具结果是否矛盾、是否答非所问 | `validate.py` 扩展 | MockLLM 确定性测试 |
| E.2 | `research-agent diagnose` CLI：扫最近 N 个 `.jsonl` → 小模型批量标注问题轮 → 聚合分类 | `cli.py`、`diagnostics/scan.py` | 构造日志样本测试 |
| E.3 | 报告产物：`data_dir/diagnostics/report-{ts}.md` + `.json`（故障列表/频率/health/建议） | `diagnostics/report.py` | 单测：报告生成 |
| E.4 | `GET /api/diagnostics`：最近故障 + 会话 summary + 报告路径 | `server.py` | 集成测试 |
| E.5 | **故障→记忆回写**：高频/典型故障按 `kind=dead_end` 写入 B（如"别在 X 场景用 Y"），下次 ROUTE 命中提醒 | `diagnostics/monitor.py` + `memory/` | MockLLM：dead_end 入库+召回 |

> 报告对象为开发者/维护者，入口：CLI + API。前端"开发者后台"留待后续，不在本计划 UI 范围。

---

## Phase F: 打磨与一致性

| # | Task | 文件 | 验证 |
|---|------|------|------|
| F.1 | 修订 `ARCHITECTURE.md`：论文检索 grep 化、向量库仅 B、记忆分层图（修正 `accumulated_wisdom` 与实现不符） | `ARCHITECTURE.md` | 文档审查 |
| F.2 | `_maybe_compress` 由">10 轮"改为按 token 预算 | `agent.py` | 集成测试 |
| F.3 | `_detect_pending_task` 字符串检测改为结构化（task 写入 B 的 decision/task unit） | `agent.py`、`models.py` | MockLLM |
| F.4 | `diagnose` CLI + 记忆测试加入 CI job | `.github/workflows/ci.yml` | CI pass |
| F.5 | 回归：原 30+ 测试 + V4 新增全部全绿，无真实 LLM/网络依赖 | `pytest` | 全绿 |

---

## 依赖关系

```
Phase M ──(并行)── Phase D
   │                  │
   ▼                  ▼
Phase A → Phase B → Phase C          (记忆线)
Phase D → Phase E ──┐                (诊断线)
Phase E.5 ──────────┼→ 依赖 B 已入库
                    ▼
                 Phase F
```

- M 先行且独立于记忆线，先移除论文向量库，避免新旧检索并存。
- A→B→C 串行（能写才能读）；D→E 可与记忆线并行。
- E.5 需 B 入库可用（依赖 Phase B）；F 收尾回归。

---

## 验收标准

1. 论文检索零向量库：代码中无论文 embedding/向量索引路径，`retrieve` 全部基于 `papers/*.md` grep。
2. Tier B 只含"对话后检索+摘要"提炼的持久信息；**工具调用、工具结果、论文全文在 B 中可被测试断言为不存在**。
3. `config.memory.enabled=false` 时，行为与 V3 一致（feature flag 隔离，回归即断言点）。
4. 记忆全链路（提炼→校验→入库→route→召回→注入）在 MockLLM 下可确定性测试。
5. 开发者报告：一次转圈/重复/语义矛盾 → JSONL 有 fault 事件 → `research-agent diagnose` 报告可见 → 高频故障可回写 `dead_end`。
6. `/api/diagnostics` 返回最近故障与 summary；全部测试 CI 全绿、无网络依赖。

---

## 计划任务状态

| Phase | 状态 | 关键 commit |
|-------|------|-------------|
| M | ✅ done | uncommitted (grep 化两阶段落地, 161 tests) |
| A | ✅ done | uncommitted (memory 包 + SQLite + 向量降级 + facade, 175 tests) |
| B | ✅ done | uncommitted (提炼源/EXTRACT/VERIFY/异步管道/memorize, 191 tests) |
| C | ✅ done | uncommitted (route/retrieve/trim/context注入, 204 tests) |
| D | ✅ done | uncommitted (recorder/monitor/summary + agent 接入, 220 tests) |
| E | ✅ done | uncommitted (语义自评/scan/report/CLI/API/dead_end 回写, 233 tests) |
| F | ⬜ todo | — |
