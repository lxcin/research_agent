# PaperPilot Harness — 实现计划 (PLAN.md)

> 对应 SPEC: `specs/2026-08-09-harness-spec.md` | 版本: V3 | 日期: 2026-08-09

## 总体架构

```
Phase 1: 基础设施 (agent loop + tool registry)
Phase 2: 工具系统 (13 tools + subagent)
Phase 3: 治理与反馈 (guardrail + HITL + auto_validate) ← 主要贡献
Phase 4: 上下文与记忆 (context + project_manager)
Phase 5: 前端 (React SSE sections + chat management)
Phase 6: 分发与文档 (Docker + CI + SPEC/README)
```

---

## Phase 1: 基础设施

| # | Task | 文件 | 验证 |
|---|------|------|------|
| 1.1 | 实现 AgentState, Project, Action 等数据模型 | `models.py` | 单元测试导入 |
| 1.2 | 实现 ToolRegistry 单例 (register/dispatch/list_for_llm) | `tools/__init__.py`, `tools/schema.py` | Mock dispatch 测试 |
| 1.3 | 实现 LLM 抽象层 (LiteLLMProvider + MockLLMProvider) | `llm.py` | test_harness_mechanisms.py |
| 1.4 | 实现 Agent 主循环 (_call_llm_with_tools → dispatch → 回灌 → 停机) | `agent.py` | test_harness_integration.py |

## Phase 2: 工具系统

| # | Task | 文件 | 验证 |
|---|------|------|------|
| 2.1 | 实现文件系统工具 (file_read/write/edit/glob/grep + shell_exec + check_tasks) | `tools/builtin/filesystem.py` | test_filesystem_tools.py (19 tests) |
| 2.2 | 实现研究工具 (retrieve/search_papers/read_paper/update_notes/delete_paper) | `tools/builtin/retrieve.py` | 集成测试 |
| 2.3 | 实现子代理工具 (spawn_subagent: ThreadPoolExecutor, 6 workers) | `tools/subagent.py` | test_subagent.py |
| 2.4 | 实现 MCP 外部工具加载 (JSON-RPC stdio) | `tools/mcp_loader.py` | 集成测试 |
| 2.5 | 实现参数校验 (validate_tool_params) | `tools/validate_params.py` | 单元测试 |

## Phase 3: 治理与反馈 ← 主要贡献

| # | Task | 文件 | 验证 |
|---|------|------|------|
| 3.1 | 实现 guardrail(): 12 种危险命令正则阻断 | `guardrail.py` | test_hitl.py (4 tests) |
| 3.2 | 接入 guardrail 到 agent 循环 (shell_exec 前调用) | `agent.py` | 集成测试 |
| 3.3 | 实现 HITL 审批: confirm_required SSE + threading.Event(60s) + /api/confirm | `agent.py`, `server.py`, `App.tsx` | test_hitl.py |
| 3.4 | 实现 _auto_validate(): py_compile/pytest/javac 文件后自动检查 | `agent.py` | test_feedback_loop.py (3 tests) |
| 3.5 | 实现 _safe_path() 路径沙箱 | `tools/builtin/filesystem.py` | test_filesystem_tools.py |
| 3.6 | Mock-LLM 全链路集成测试 (护栏+反馈+分发) | `test_harness_integration.py` | 6 tests |
| 3.7 | 机制演示脚本 (零 LLM 确定性验证) | `demo_mechanisms.py` | CI demo job |

## Phase 4: 上下文与记忆

| # | Task | 文件 | 验证 |
|---|------|------|------|
| 4.1 | 实现 project_manager (workspace hash → JSON 存储) | `project_manager.py` | 功能测试 |
| 4.2 | 实现 build_context (system prompt + progress + history + skills) | `context.py` | test_context.py |
| 4.3 | 实现 memory 适配层 (store_turn/get_recent_turns/count/compress) | `memory.py` | test_memory.py |
| 4.4 | 实现对话压缩 (10轮未压缩 → LLM 摘要) | `agent.py:_maybe_compress` | 集成测试 |
| 4.5 | 实现 progress.md 自动维护 | `agent.py` + `project_manager.py` | 功能测试 |

## Phase 5: 前端

| # | Task | 文件 | 验证 |
|---|------|------|------|
| 5.1 | 定义 MessageSection 类型 + SSE 解析器 | `types.ts`, `App.tsx` | TSC |
| 5.2 | 实现 ThinkingBlock (可折叠思考) | `ThinkingBlock.tsx` | TSC |
| 5.3 | 实现 ToolCallBlock (可折叠工具+diff) | `ToolCallBlock.tsx` | TSC |
| 5.4 | 实现 CitationCard (论文引用卡片) | `CitationCard.tsx` | TSC |
| 5.5 | 重构为 chat-centric (对话为基本单位) | `App.tsx`, `ProjectSidebar.tsx`, `TopBar.tsx` | TSC |
| 5.6 | 实现 HITL 确认弹窗 (confirm_required → window.confirm) | `App.tsx` | 集成测试 |

## Phase 6: 分发与文档

| # | Task | 文件 | 验证 |
|---|------|------|------|
| 6.1 | Docker 镜像构建 + CI 推送 GHCR | `Dockerfile.*`, `ci.yml` | CI pass |
| 6.2 | Render 部署配置 | `render.yaml` | 线上可访问 |
| 6.3 | 编写 SPEC.md (12节含 §A.3 §A.4) | `specs/2026-08-09-harness-spec.md` | 文档审查 |
| 6.4 | 编写 README.md (分发+安全+架构) | `README.md` | 文档审查 |
| 6.5 | 更新 SPEC_PROCESS.md + AGENT_LOG.md | 对应文件 | 文档审查 |
---

## 依赖关系

```
Phase 1 ──→ Phase 2 ──→ Phase 3
                │
                └──→ Phase 4 (并行)
Phase 1 ──→ Phase 5 (可并行)
Phase 2+3+4+5 ──→ Phase 6
```

## 任务完成状态

| Phase | 状态 | 关键 commit |
|-------|------|-------------|
| Phase 1 | ✅ done | `0639297` |
| Phase 2 | ✅ done | `0639297` |
| Phase 3 | ✅ done | `3a356e1`, `495d1c3` |
| Phase 4 | ✅ done | `0639297`, `10021c0` |
| Phase 5 | ✅ done | `10021c0` |
| Phase 6 | ✅ done | `4bd7921` |
