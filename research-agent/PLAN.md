# PLAN.md — PaperPilot Implementation Plan

## V1 Tasks (已完成 via Superpowers, 2026-07-10)

| # | Task | Status |
|------|------|------|
| T1 | 项目脚手架 (models, config, tests) | ✅ `45741b9` |
| T2 | SQLite 存储层 | ✅ `236bc6b` |
| T3 | ChromaDB 向量库 | ✅ `9f0719e` |
| T4 | PDF 摄入管线 | ✅ `15f95ce` |
| T5 | 混合检索 (vector+BM25+RRF) | ✅ `4d041f8` |
| T6 | Semantic Scholar 搜索 | ✅ `3ba018e` |
| T7 | 消融评估 | ✅ `a231274` |
| T8 | 对话压缩 | ✅ `830e4cd` |
| T9 | 项目路由 | ✅ `2b48ff8` |
| T10 | LangGraph Agent | ✅ `5094255` |
| T11 | Agentic Loop (自实现) | ✅ `772c952` |
| T12 | CLI 入口 | ✅ `10e8231` |
| T13 | Skill + MCP 系统 | ✅ `43dd41d` |
| T14 | 集成测试 | ✅ `e6c60ec` |
| T15 | README | ✅ `82f80ab` |

## V2 Tasks (本次迭代)

| # | Task | 涉及文件 | 验证 |
|------|------|------|------|
| T16 | Function Calling 替代 JSON 解析 | `agent.py`, `context.py` | 端到端测试: tool_choice="auto" |
| T17 | ToolRegistry 可插拔架构 | `tools/__init__.py`, `tools/schema.py`, `tools/builtin/` | 注册 10 个工具，load_from_dir 加载 |
| T18 | 内联工具调用（replace 独立卡片） | `App.tsx`, `ChatArea.tsx` | 工具调用出现在回答文本内 |
| T19 | 侧边栏 + 多对话 + ChatTabs | `ProjectSidebar.tsx`, `ChatTabs.tsx` | 项目切换 + 对话窗口管理 |
| T20 | 工作区侧边栏（opencode 风格） | `WorkspaceSidebar.tsx` | 可拖拽、文件预览、自动刷新 |
| T21 | 论文库存入 workspace（双写） | `tools/builtin/retrieve.py` | search_papers 后 workspace/papers/ 有 .md |
| T22 | context injection skill（SURVEY_WORKFLOW） | `context.py` | survey 关键词 → 注入工作流 |
| T23 | read_paper 元数据 + auto-notes | `tools/builtin/retrieve.py` | 返回 title/authors/year |
| T24 | 后台任务 + check_tasks | `tools/builtin/filesystem.py` | background=true 不阻塞 |
| T25 | 语义切块 | `ingestion.py` | TF-IDF 主题边界检测 |
| T26 | 上下文架构清理 | `context.py`, `agent.py`, `config.py` | 模型自适应 token 上限 |
| T27 | 文档恢复 (SPEC/PLAN/SPEC_PROCESS) | `*.md` | 当前文件 |
| T28 | CI 修复 + 部署 | `.github/workflows/ci.yml` | GitHub Actions pass |

## V2 验证步骤

1. `python -m pytest tests/ -v` — 核心工具单元测试
2. 手动端到端：检索 → 读论文 → 写代码 → 执行 → 记录
3. 手动端到端：综述关键词 → SURVEY_WORKFLOW 注入 → 生成综述
4. `shell_exec(background=true)` → `check_tasks` 查询状态
5. CI: `make test` 通过