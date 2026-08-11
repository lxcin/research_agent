# SPEC_PROCESS.md —— PaperPilot 设计过程记录

> 记录从 V1 到 V3 的全部 brainstorming、spec 生成与架构决策过程。

---

## 一、V1：从零到 SPEC（2026-07-09 ~ 2026-07-11）

### 1.1 启动与 brainstorming

使用 Superpowers 的 brainstorming 技能，从零设计科研助手 Agent。

**关键设计震荡**：

**节点 1：知识图谱之辩**
- AI 初始提案：RAG + Light Graph
- 用户推翻："像 claude 一样做全量检索不行吗"
- 最终决策：V1 不做图，纯 RAG + 混合检索。图推迟到 V2

**节点 2：记忆架构之争**
- AI 初始提案：双层记忆（UserProfile + 对话记忆），每轮反思
- 用户推翻："一个对话窗口就够了"
- 最终决策：LangGraph checkpoint 按项目隔离，定期压缩触发

**节点 3：RAG 摄入设计**
- 用户要求 chunk 存入时就带溯源、低分论文不入库
- 最终决策：入库时即做去重+矛盾检测+质量过滤+多源溯源
- 命名：可溯源多源 RAG（Traceable Multi-Source RAG）

**节点 4：分块策略冲突仲裁**
- 6 条分块策略之间的优先级：章节为墙、段落为砖、句子为胶

### 1.2 冷启动验证

用不与主 agent 共享对话历史的清洁 agent，只给 SPEC.md + PLAN.md，实现 Task 1（脚手架）+ Task 2（存储层）。

**发现的 7 个缺陷**：

| # | 缺陷 | 原因 |
|---|------|------|
| 1 | `setuptools.backends._legacy:_Backend` 拼写错误 | spec 写错 |
| 2 | `--help` 断言在 Task 1 过早验证 | spec 写错，cli.py 在 Task 12 才创建 |
| 3 | `accumulated_wisdom` 列在 schema 中缺失 | spec 写错，schema 和代码不一致 |
| 4 | `init_conflict_table` 被引用但未定义 | spec 遗漏 |
| 5 | `AccumulatedWisdom` 未导入 | spec 遗漏 |
| 6 | `row.get()` 在 sqlite3.Row 上不可用 | Python 运行时行为 |
| 7 | `ProjectStatus` 枚举往返未处理类型转换 | spec 写错 |

**结论**：7 个缺陷中 6 个是 spec/plan 写错或遗漏。清洁 agent 自行修复了全部缺陷——错误是局部的、可自行推断的。SPEC + PLAN 可以独立指导实现。

---

## 二、V2：增量迭代（2026-07-10 ~ 2026-07-21）

### 2.1 主要变更

V2 保留 V1 核心架构（自实现 agent loop、SQLite+ChromaDB 存储、混合检索），重点增强用户体验和工具能力。

| 维度 | V1 | V2 |
|------|----|----|
| Agent 循环 | LangGraph StateGraph | litellm native function calling |
| 动作解析 | JSON 解析 `{"action": "retrieve"}` | OpenAI function call schema |
| Skill 实现 | Python handler 注册工具 | context injection（SURVEY_WORKFLOW 注入 prompt） |
| 上下文 | 4000 token 硬编码截断 | 模型自适应上限（DeepSeek 64K, GPT-4o 128K） |
| 前端 | 无 | React + TypeScript + pywebview |
| 论文搜索 | Semantic Scholar API | arXiv API |
| 分块 | 固定 token 切割 | TF-IDF 语义切块 |

### 2.2 关键设计讨论

**Function Calling vs JSON 解析**
V1 让 LLM 输出 `{"action": "retrieve"}` JSON，但 DeepSeek 有时输出 markdown fence 包裹的 JSON。切换到 litellm native function calling 后稳定性大幅提升，且 LLM 可以同时调用多个工具。

**Skill 的正确形态**
最初把文献综述写成 Python handler 注册为 tool，LLM 不愿调用且失去流程控制。改为 context injection——注入 SURVEY_WORKFLOW 到 system prompt，LLM 用自己的工具按流程执行。这是 V2 最重要的设计决策之一。

**上下文为什么截断论文全文**
发现 `build_chat_context` 不包含 tool results，造成"读了 13 篇只引用 4 篇"。根因是 `trim_messages` 截断到 4000 tokens。修复：按模型自适应上限，generate 阶段直接使用 messages（包含所有工具结果）。

**Prompt 分段的重要性**
使用 `=== 以下是工具调用结果 ===` 分隔线后，DeepSeek 不再输出 `<tool_calls>` XML。分隔线让 LLM 明确区分"数据"和"任务"。

### 2.3 人工干预

- 修复 `<tool_calls>` XML 输出：用 `_generate_msgs` 过滤 assistant tool_call 消息
- 修复论文引用率低：bump context limit 4000→64000 + messages 传递工具结果
- 修复综述永远不写：DeepSeek function calling 循环中不愿停止调工具，加强 SURVEY_WORKFLOW 指引

---

## 三、V3：架构大 pivot（2026-08-01 ~ 2026-08-10）

### 3.1 为什么 pivot

V2 虽然功能可用，但存在三个深层问题：

1. **SQLite 项目管理笨重**：每个项目需要 init_db() 创建表结构，跨机器迁移困难，与 workspace 文件系统语义脱节
2. **治理层不够纵深**：guardrail 只做输入拦截，没有 HITL 审批状态机，没有 auto_validate 反馈闭环
3. **前端耦合后台概念**：前端以项目为中心，但用户交互以"聊天"为单元，项目是容器而非交互主体

### 3.2 三大架构变更

**变更 1：SQLite → 文件式 workspace 模型**

移除 SQLite 项目管理层（`store.py` 中 project 相关逻辑），改为文件系统标记式：
```
workspace/
  .research-agent/
    project.json          # 项目元数据
    conversations/         # 对话历史 JSON 文件
    progress.md            # 研究进度
  papers/                  # 论文 Markdown 副本
  code/                    # 代码工作区
```

核心理念：项目 = 文件系统目录。无需数据库。迁移 = 复制目录。备份 = tar。

**变更 2：治理纵深——三层反馈闭环**

| 层 | 文件 | 机制 | 确定性 |
|----|------|------|--------|
| 输入护栏 | `guardrail.py` | 12 个危险模式正则 + 路径穿越拦截 | 纯代码，mock 可测 |
| 执行反馈 | `agent.py` _auto_validate | 文件写入后自动语法检查/test 运行，结果回灌到 messages | 纯代码 |
| HITL 审批 | `agent.py` + `server.py` | threading.Event 挂起，前端弹确认框，用户同意/拒绝后恢复 | 状态机 |

12 个 guardrail 模式：`rm -rf /`、`sudo`、`mkfs`、`dd if=`、`>/dev/sd`、`chmod 777 /`、fork bomb、`curl|bash`、`wget|sh`、`eval`、路径穿越 + 特殊设备文件。

HITL 流程：guardrail 拦截 → emit `confirm_required` {id, action, reason} → 前端弹框 → 用户决策 → `POST /api/confirm` → 继续/取消。

auto_validate：`file_write`/`file_edit` 后自动 `py_compile` 语法检查，`.py` 文件写入后自动 `pytest` 测试运行，结果以 `[自动验证]` system 消息注入 context。

**变更 3：前端——项目中心 → 聊天中心**

- 左侧 ProjectSidebar：项目 CRUD，工作区路径设置
- 中间 ChatArea：多 tab 对话，可切换、新建、关闭
- 右侧 WorkspaceSidebar：可拖拽宽度（180-600px），文件列表 + 预览
- 底部 PlanBar：执行计划进度条，自动打勾

### 3.3 SSE 协议重新设计

```
事件类型:
  thinking       — LLM 思考过程 token 流
  tool_start     — 工具开始执行
  tool_end       — 工具执行完成，含结果摘要
  reply          — 最终回复 token 流
  file_change    — 工作区文件变更通知（前端刷新文件树）
  confirm_required — HITL 审批请求
  done           — 对话结束
  error          — 错误信息
```

### 3.4 死代码清理

- 移除 `skills/` 目录中的 Python handler 文件（context injection 替代）
- 移除 `memory.py` 中死掉的对话 embedding 代码
- 移除 `build_context` 中残留的 `retrieved_context` 注入
- 清理 V1 过期的测试文件（移除 `tests/` 中 stale test）

### 3.5 工具系统细化

- ToolRouter：意图驱动的工具子集过滤（search 意图只暴露 search_papers+read_paper，chat 意图只暴露 3 个核心工具）
- 工具职责明确：retrieve（查已有库）vs search_papers（外部搜索）vs read_paper（摄入全文）
- 硬限制：MAX_SEARCH_CALLS=10，防止无限搜索
- 搜索后自动移除 retrieve 工具，强制走 search→read_paper 工作流

---

## 四、AI 建议被采纳/推翻的对照

| 建议 | AI 提出 | 用户决策 | 原因 |
|------|---------|---------|------|
| RAG + Light Graph 知识图谱 | V1 | 推翻了 | V1 负担过重，先跑通基础 |
| 双层记忆（UserProfile + 对话） | V1 | 推翻了 | "一个窗口就够了" |
| 每轮结束后压缩 | V1 | 推翻了 | LLM 调用成本过高 |
| 入库时不做质量裁决 | V1 | 推翻了 | "垃圾进垃圾出" |
| 章节感知分块 + 冲突仲裁 | V1 | 采纳 | 用户要求规范冲突处理 |
| verified_sources 多源溯源 | V1 | 采纳 | 用户要求 chunk 带溯源 |
| LangGraph 做基础框架 | V1 | 采纳（后在 V1.5 移除） | checkpoint 机制有价值 |
| Function Calling 替代 JSON | V2 | 采纳 | DeepSeek JSON 不稳定 |
| Skill = Context Injection | V2 | 采纳 | LLM 保留流程控制权 |
| 关键词路由 | V1 | 推翻了 | 中文分词不友好 |
| 意图路由（ToolRouter） | V3 | 采纳 | 减少 LLM 工具选择混淆 |
| 文件式 workspace 模型 | V3 | 采纳 | 零数据库依赖，可迁移 |
| Guardrail 12 模式 + 路径穿越 | V3 | 采纳 | 确定性代码，mock 可测 |
| HITL 审批状态机 | V3 | 采纳 | 危险操作不直接阻断，给用户裁决权 |
| auto_validate 反馈回灌 | V3 | 采纳 | 文件写入后自动验证 + 自我修正 |

---

## 五、反思：过程评估

### 做得好的

1. **冷启动验证暴露了文档缺陷**：第二个 agent 发现的 7 个问题如果不经验证，会在正式实现时造成大量返工
2. **"一个窗口就够了"的追问推动了大简化**：减少约 30% 的 LLM 调用成本
3. **V3 的 anchor pivot 决策果断**：从 SQLite→文件系统、"项目中心→聊天中心"，每次 pivot 都基于实际使用反馈
4. **治理优先于功能**：V3 最大的贡献不是新功能而是治理纵深——guardrail + HITL + auto_validate 全部是确定性代码

### 不足的

1. **初始方向偏了太远**：AI 一开始就跳进了知识图谱的复杂设计，花了很多轮讨论实体关系、Leiden 算法。如果一开始就问"为什么不能直接用 RAG"，可以节省 30% 的讨论时间
2. **过早的技术细节**：在用户还没确认"差异化是什么"的时候，就开始讨论 RRF 融合、BM25 权重、chunk 大小
3. **V1→V2→V3 的架构 churn**：LangGraph→自实现→Function Calling，三次迭代每次改动 agent 核心循环，暴露出初期架构选择不够深思熟虑

### 最重要的教训

**"当 LLM 负责思考，工程价值在 harness"**。V1 花大量精力设计 Claim 节点、论证树、知识图谱，最终全部推翻了。但 guardrail、HITL、auto_validate——这些确定性代码——从 V2 到 V3 持续加强。不是 LLM 更聪明了，是 harness 更强了。

---

## 六、最终 SPEC 与 PLAN 状态

| 版本 | 时间 | SPEC | PLAN | 任务数 | 状态 |
|------|------|------|------|--------|------|
| V1 | 2026-07-09 | `specs/2026-07-09-research-agent-design.md` | `plans/2026-07-09-research-agent-implementation.md` | 15 | 冷启动验证通过，7 个缺陷已修复 |
| V2 | 2026-07-21 | SPEC 附录 V2 章节 | `specs/2026-07-11-research-agent-v2-plan.md` | 8 模块（G-O） | 验收通过 |
| V3 | 2026-08-08 | ARCHITECTURE.md（10 个设计决策） | AI4SE_GAP.md（5 步执行计划） | 5 步 | 完成 |
