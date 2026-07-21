# SPEC_PROCESS.md —— 科研助手 Agent 设计过程记录

> 记录与 Superpowers 协作从零到完整 SPEC + PLAN 的全过程，含冷启动验证。

---

## 一、brainstorming 关键节点

### 节点 1：知识图谱之辩（最大的设计震荡）

**AI 初始提案**：RAG + Light Graph（轻量图），认为全量图对实体关系抽取准确率要求太高，V1 负担过重。

**用户推翻**：

> "局部图+全量图吧。我不太确定这个可行性，目录可能有点类似全量图，大的知识点之间可能会存在关联？"

> "很难搞得一个问题是怎么样算全量的，多局部算局部的"

**处理决策**：AI 重新定义——不是按规模划界，而是按目的。全量图 = 导航（"去哪找"），局部图 = 推理（"这些结论怎么关联"）。用户接受了这个定义，但随后追问了论文质量、冗余、存储等问题。

**转折点**——用户后来质疑了整个图方案的必要性：

> "像claude一样做全量检索不行吗"

**最终决策**：V1 不做图，纯 RAG + 混合检索。图推迟到 V2，引入时用 LangGraph，先做效果评估再集成。这是整个设计过程中最大的一次方向修正——从"以图为核心"翻转为"先跑通基础，图是加分项"。

---

### 节点 2：记忆架构之争（"一个对话窗口就够了"）

**AI 初始提案**：双层记忆——User Profile（长程）+ 对话记忆（短程），每轮结束后 Agent 反思更新。

**用户推翻**：

> "有意义吗？比如那个化学任务我不是可以直接在同一个对话用"

**处理决策**：AI 承认用户说得对——如果用户不关窗口，双层记忆的额外 LLM 调用成本是浪费。转向更简单的 LangGraph checkpoint 按项目隔离，每个项目 = 一个 thread_id，状态自动持久化。

**用户进一步修正**：

> "agent记忆参考随时做压缩和总结，防止爆炸"

**最终决策**：压缩改为定期触发（≥40 轮/用户手动/项目阶段结束），不被每轮触发。压缩时不仅总结，还要提取结构化知识（SOP、pitfalls、frameworks、agent_improvements）——这是 Agent "随用户一起成长"的核心机制。

---

### 节点 3：RAG 摄入设计（从"检索时处理"到"入库时处理"）

**用户关键需求**：

> "chunk存入的时候就带着溯源，这段话这个实验来自哪一篇论文"

> "搜索时带回来的论文要有评分和取舍，太低分的论文就不入库"

**处理决策**：这推翻了 AI 之前"入库时不做质量裁决，在检索时做"的设计。改为：
- 入库时即做去重、矛盾检测、质量过滤、多源溯源
- source_score < 4 的论文直接拒绝
- 知乎/微信公众号/无水刊直接拒绝
- 同一观点被多篇论文验证 → 双向追加 verified_sources

**命名**：可溯源多源 RAG（Traceable Multi-Source RAG）

---

### 节点 4：分块策略的冲突仲裁

**用户追问**：

> "会不会出现矛盾，怎么处理先后问题"

**处理决策**：AI 将 6 条分块策略（章节感知、段落级、最小/最大长度、重叠、特殊内容隔离）之间的冲突列出了明确的仲裁规则：
1. 章节是绝对的墙——任何规则不可跨章节
2. 特殊内容先隔离
3. 短段落聚合
4. 超长段落句子边界切分
5. 重叠注入
6. 尾段兜底

命名：**章节为墙、段落为砖、句子为胶**。

---

## 二、AI 建议被采纳/推翻的对照

| 建议 | AI 提出 | 用户决策 | 原因 |
|------|---------|---------|------|
| RAG + Light Graph | V1 | 推翻了，改成纯 RAG | 用户认为图的投入产出比在 V1 不划算 |
| 全量图 + 局部图 | 中期 | 推翻了，V2 再议 | 同上，用户要的是"先跑通" |
| 双层记忆（UserProfile + 对话） | V1 | 推翻了，改成 checkpoint 按项目 | 用户认为"一个窗口就够" |
| 每轮结束后压缩 | V1 | 推翻了，改为定期触发 | 用户担心 LLM 调用成本和缓存命中率 |
| 入库时不做质量裁决 | V1 | 推翻了，改为入库时过滤 | 用户担心"垃圾进垃圾出" |
| 章节感知分块 + 冲突仲裁 | 中期 | 采纳 | 用户要求规范冲突处理 |
| verified_sources 多源溯源 | 中期 | 采纳 | 用户要求 chunk 带溯源 |
| source_score 门槛过滤 | 中期 | 采纳 | 用户要求拒绝低质来源 |
| LangGraph 做基础框架 | 中期 | 采纳 | 用户认可 checkpoint 机制 |
| accumulated_wisdom（SOPs/pitfalls/frameworks） | 中期 | 采纳 | 用户认为这是"Agent 成长"的核心 |
| 冷启动验证（第二个 agent） | 由用户提出 | 执行 | 验证 SPEC+PLAN 的独立性 |

---

## 三、3 轮关键迭代对话节选

### 第 1 轮：从图到 RAG 的翻转

```
AI: 我的建议是 V1 采用 RAG + Light Graph（轻量图）
用户: 局部图+全量图吧。我不确定这个可行性...
AI: 全量图作目录 + 局部图作推理链 + chunk 作素材
用户: 那会不会让ai的缓存很混乱，堆积了太多垃圾
AI: 知识图谱 vs 原始文献库，两层分离
用户: 像claude一样做全量检索不行吗
AI: [诚实评估] 对大多数查询来说，是的，直接 RAG 效果接近。
    图解决的不是"搜得准"，而是 gap analysis、跨域关联、多跳推理。
    务实建议：V1 不建 Claim 图，用 Claude 级全量检索 + 轻量元数据图。
用户: 那这个的设计和通用agent+skill有什么区别
AI: [重新定位] 差异化 = 长期记忆 + 可解释性 + 多跳推理
```

### 第 2 轮：从双层记忆到 checkpoint 的简化

```
AI: 双层记忆——User Profile + 对话记忆
用户: 有意义吗？化学任务我不是可以直接在同一个对话用
AI: [承认] 你说得对。如果用户不关窗口，确实不需要。
    简化：每个项目 = 一份独立对话，不压缩，不管记忆。
用户: 要不我们维护项目状态和对话，保障项目的跨对话进行，
      就像claude的/memory一样，摘要和压缩进项目状态大概一定时间段触发
AI: LangGraph checkpoint 按 thread_id=project_id
    定期压缩 → accumulated_wisdom
用户: 直接按langgraph的框架+claude的长程记忆
AI: 最终：LangGraph 管会话，accumulated_wisdom 管结构化知识
```

### 第 3 轮：从"检索时处理"到"入库时处理"

```
用户: chunk存入的时候就带着溯源，这段话这个实验来自哪一篇论文，
      如果后面不同的论文也有这个观点就在去重那一步加上
AI: 可溯源多源 RAG：agree → 双向追加 verified_sources
用户: 搜索时带回来的论文要有评分和取舍，太低分的论文就不入库
AI: 入库时 source_score 门控：
      ≥4 入库，3 标记预印本，2 隔离，1 拒绝
      知乎/微信公众号/无水刊直接拒绝
用户: 那我们这个设计叫什么
AI: 可溯源多源 RAG（Traceable Multi-Source RAG）
```

---

## 四、冷启动验证：第二个 agent 的发现

### 验证方式

用一个**不与主 agent 共享对话历史**的清洁 agent，只给 `SPEC.md` + `PLAN.md`，尝试实现 Task 1（脚手架）+ Task 2（SQLite 存储层）。

### 结果

- Task 1: ✅ 通过，commit `54208ba`
- Task 2: ✅ 通过，commit `2420c7b`，7 个测试全绿
- **结论：SPEC + PLAN 可以独立指导实现，不需要对话历史上下文**

### 发现的 7 个缺陷及修复

| # | 缺陷 | 是 spec 写错还是 agent 读错 | 修复前的 diff | 修复 | 修复后的 diff |
|---|------|---------------------------|-------------|------|-------------|
| 1 | `setuptools.backends._legacy:_Backend` 不存在 | **spec 写错**——拼写错误 | `build-backend = "setuptools.backends._legacy:_Backend"` | → `setuptools.build_meta` | `build-backend = "setuptools.build_meta"` |
| 2 | `research-agent --help` 在 Task 1 过早验证 | **spec 写错**——cli.py 在 Task 12 才创建 | `Expected: Installs successfully with no errors. research-agent --help works.` | → 移除 `--help` 断言 | `Expected: Installs successfully with no errors.` |
| 3 | `insert_project` 引用 `accumulated_wisdom` 列，但 `init_db` 的 CREATE TABLE 没有该列 | **spec 写错**——schema 和代码不一致 | `CREATE TABLE projects (id, topic, status, pending_task, history_summary, plan, ...)` | → 补充 `accumulated_wisdom` 和 `intro_summary` 列 | `CREATE TABLE projects (..., intro_summary, accumulated_wisdom, plan, ...)` |
| 4 | `init_conflict_table` 函数被 ingestion.py 引用但 store.py 中未定义 | **spec 遗漏**——函数存在但未写进 plan | N/A（完全是新增） | → 补充 `def init_conflict_table()` | 新增约 10 行函数 |
| 5 | `AccumulatedWisdom` 在 `_project_from_row` 中使用但未导入 | **spec 遗漏** | `from research_agent.models import Paper, Project, PendingTask, PlanStep` | → 补充 `AccumulatedWisdom, ProjectStatus` | `from ... import ..., AccumulatedWisdom, ProjectStatus` |
| 6 | `row.get("accumulated_wisdom")` 在 sqlite3.Row 上不可用 | **spec 写错**——sqlite3.Row 不支持 `.get()` | `row.get("accumulated_wisdom")` | → `row["accumulated_wisdom"]` | `row["accumulated_wisdom"]` |
| 7 | `ProjectStatus` 枚举往返（存为字符串，读回字符串，但 `status.value` 期望枚举） | **spec 写错**——未处理 SQLite 存取的类型转换 | `status=row["status"]` | → `status=ProjectStatus(row["status"])` | `status=ProjectStatus(row["status"])` |

### 解读

- **7 个缺陷中 6 个是 spec/plan 写错或遗漏**，1 个是 Python 运行时行为（sqlite3.Row 的 API）。
- 最严重的缺陷是 #3（schema 缺失列）——如果没有被修复，后续所有 project 操作都会失败。
- 这些缺陷的共同特征是"设计文档和代码实现之间的缝隙"——plan 中写了代码片段，但表格 schema 和函数签名之间没有足够的交叉验证。
- 好消息：清洁 agent 在发现这些问题后**自行修复了**，没有卡住，说明错误是局部的、可自行推断的。

---

## 五、反思：brainstorming 技能的评估

### 做得好的地方

1. **"一个窗口就够了"的追问推动了大简化**：AI 提出了双层记忆，用户挑战了必要性，最终设计从复杂的 UserProfile+AgentSelfIntro+每轮反思 简化为 LangGraph checkpoint 按项目 + 定期压缩。这个简化节省了约 30% 的 LLM 调用成本。

2. **"像 Claude 一样做全量检索不行吗"戳破了过度设计**：AI 花了大量精力设计知识图谱，但用户的诚实问题让团队意识到——V1 先做简单 RAG 跑通，图是 V2 的护城河。这个决策避免了 V1 的 scope creep。

3. **"冷启动验证"暴露了文档缺陷**：第二个 agent 发现的 7 个问题如果不经验证，会在正式实现时造成大量返工。

### 让你不满的地方

1. **初始方向偏了太远**：AI 一开始就跳进了知识图谱的复杂设计，花了很多轮讨论实体关系、Leiden 算法、Claim 节点——这些东西在 V1 全部被砍。如果一开始就问"为什么不能直接用 RAG"，可以节省 30% 的讨论时间。

2. **过早的技术细节**：在用户还没确认"差异化是什么"的时候，AI 就开始讨论 RRF 融合、BM25 权重、chunk 大小。这些细节在最终设计中被证明是正确的，但讨论顺序应该先"为什么做"再"怎么做"。

3. **追问密度不够均匀**：AI 在知识图谱部分问了大量细节问题，但在记忆系统、项目管理部分的追问明显更少。这导致记忆系统经历了 3 轮大幅修正，而图谱部分即使最终被砍，也投入了过多时间。

---

## 六、最终 SPEC 与 PLAN 状态

| 文档 | 路径 | 任务数 | 状态 |
|------|------|--------|------|
| SPEC | `docs/superpowers/specs/2026-07-09-research-agent-design.md` | — | 通过冷启动验证 |
| PLAN | `docs/superpowers/plans/2026-07-09-research-agent-implementation.md` | 15 | 7 个缺陷已修复，通过冷启动验证 |
| 本文档 | `docs/superpowers/SPEC_PROCESS.md` | — | 完成 |