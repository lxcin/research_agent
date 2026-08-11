# REFLECTION —— PaperPilot 项目反思

> 中文反思，批判性审视 Superpowers 方法论、AI 协作模式与整个开发过程。

---

## 一、哪些 Superpowers 技能最有用？哪些是"形式大于实质"？

`brainstorming` 是最有价值的技能。它追问边界条件的节奏——"你确定需要知识图谱吗？""这个场景的退化路径是什么？"——迫使在设计阶段想清楚模糊地带。V1 从"以图为核心"翻转到纯 RAG，V3 从 SQLite 翻转到文件系统，每次关键 pivot 都诞生于 brainstorming 对话。

`subagent-driven-development` 是执行加速器。15 个 task 并行推进，这是单线程人类开发无法实现的。但它的失效模式很清晰：subagent 标注"完成"不等于真正完成。T7 消融评估被标注 done，实际用的是空向量库——因为 SPEC 没说"必须先摄入论文再跑评估"。

**形式大于实质的**：`using-git-worktrees` 在 40+ commit 的尺度下，管理开销（filter-branch 跨分支、独立 git 状态）超过了隔离收益。一个 cleanup 事故耗了 2 小时。`test-driven-development` 的前提是 SPEC 精确——当 spec 写错（schema 缺列），TDD 只是让 subagent 更自信地写错代码。

---

## 二、TDD 在 AI 协作下是阻碍还是放大器？

**取决于 SPEC 质量。**

SPEC 精确时，TDD 给 subagent 明确的通过标准，减少瞎编代码。T2（存储层）是正面案例。

SPEC 模糊或写错时，TDD 放大了错误。T7 消融评估测试全部通过，但结果无意义。冷启动验证的 7 个缺陷中 6 个是 spec 错误——这意味着 TDD 的测试本身就有 bug。Subagent 写代码让测试通过，但从不质疑测试的正确性。这是 subagent-driven development 的根本局限：subagent 不会挑战 SPEC。

改进方向是"反向验证"——第二个 agent 只拿 SPEC 不从对话历史取上下文，从用户故事出发写端到端场景。冷启动验证正是这种模式。

---

## 三、Subagent 驱动的工作流能自主运行多久不脱轨？

**本项目的答案是 1-2 个 task。**

T1（脚手架）和 T2（存储层）在清洁 agent 下自主通过。T7 在无人工验证下"通过"了——但结果是错的。脱轨模式是"形式正确，实质错误"：代码能跑、测试通过、commit 规范，但没达到用户真正期望。

根本原因不是 subagent 不够聪明，而是没有"意图理解"——SPEC 说"实现消融评估框架"，subagent 实现了框架，但没理解需要真实数据、需要对比不同策略的召回率。这种 from text to intent 的鸿沟只能通过人工验证填补。

此外 subagent 的默认行为是"完成任务"而非"发现问题"。当 SPEC 写错，subagent 默默让错误通过，从不报告"这里矛盾了"。

---

## 四、什么粒度的 Task 是最优的？

约 80-150 行新增代码，含 1-2 个文件 + 3-7 个测试。

T2（存储层，150 行 + 7 测试）在这个粒度上自主通过率最高。太细（T5 RRF 融合，60 行）不需要人类判断，用 subagent 是浪费。太粗（T10 LangGraph Agent，300 行，4 个节点）产出语法正确但设计脆弱——因为方向判断 subagent 完全无法做。

最优粒度是"一个完整但局部的功能切片"：不是"实现检索系统"（太粗），也不是"实现 RRF 融合"（太细），而是"实现混合检索管线，含 5 个测试验证融合效果"。

---

## 五、SPEC/PLAN 质量如何影响实现质量？举一个具体案例。

**T9 项目路由——模糊 spec 导致 subagent 偏离。**

SPEC 对 T9 的描述是："关键词重叠匹配"。Subagent 实现了空格分词的 TF-IDF 匹配，单元测试用英文全绿。但集成测试发现用户输入"上次那个 Transformer 的 attention 机制分析结果怎么样了"，所有中文 TF-IDF score 都是 0，路由永远失败。

根因是"关键词重叠匹配"这六个字太模糊——subagent 理解为英文空格分词。如果 SPEC 写"支持中文分词（jieba），中英混合输入正确路由"，subagent 会直接引入 jieba。

这揭示了一个模式：**SPEC 中的每个模糊词，在 subagent 实现时都会被放大为设计缺陷**。但也带来一个悖论——如果 SPEC 需要精确到代码级，那编写成本接近直接写代码。妥协方案：关键路径（数据模型、路由逻辑、安全规则）精确到代码级；非关键路径（日志格式、错误消息）保持模糊。

---

## 六、最有效的 Prompt/Context 策略是什么？

**三条核心策略**：

1. **把任务要求放在 context 最后**——LLM 对最后的 system message 关注度最高。V2 综述生成先注入工作流再提问题导致步骤被跳；改为先问题后框架后遵循率显著提升。

2. **`===` 分隔线标记 context 段落**——`=== 以下是工具调用结果 ===` 让 LLM 区分"数据"和"任务"。不加时 DeepSeek 把检索结果当成新工具调用指令输出 `<tool_calls>`；分段后此现象消失。

3. **Context injection 优于 Python handler**——V2 把文献综述从 Python handler 改为 SURVEY_WORKFLOW 注入 system prompt，LLM 用自己的工具执行。这让 LLM 保留流程控制权——它可以判断"这步够了，直接写下一节"。Python handler 剥夺了这种自主权。

---

## 七、凭据与分发需求迫使你思考了什么？

**API key 的流经路径**：浏览器 localStorage → 前端 ConfigPanel → POST body → 后端 FastAPI → LiteLLMProvider。五个节点都可能泄露。最终设计：前端不持久化 key，后端内存传递不写日志，litellm 以参数传递而非环境变量。

**分发模式的差异**：Desktop（pywebview）信任本地文件和 env，Web（浏览器）必须考虑 XSS。导致两个 config path：Desktop 直接读 config.yml，Web 通过 POST body 传递后后端合并 fallback。

**CI 中的安全检查**：Docker build 不调用 LLM，但需 pre-commit hook 检查硬编码凭据——`rg "(sk-[a-zA-Z0-9]{20,}|AIza[0-9A-Za-z_-]{35})" src/`。

---

## 八、如果重来，会改变什么？

1. **先选锚点再设计功能。** 最核心的治理（guardrail + HITL + feedback）反而是最后才做深的。正确顺序：先定义 harness 边界，再装功能。

2. **第一个 subagent task 就加入端到端演示。** V1 等到 T14 才做集成测试，T7/T9/T11 的 bug 在后期才发现。T0 就应有黄金路径回归测试。

3. **不做 LangGraph。** V1.0 LangGraph→V1.5 自实现→V2 function calling，agent 核心循环改了三次。从"30 行 while + litellm"开始可省掉一个版本的 agent.py 重写。

4. **文件系统优先于数据库。** 项目数据天然适合文件系统——人类可读、git 追踪、tar 迁移。SQLite 的事务和索引在 PaperPilot 的项目管理中价值很弱。

---

## 九、对 Superpowers 方法论的批判

Superpowers 的核心是"用 SPEC 驱动 subagent 并行实现"。三个关键假设及其在本项目中的检验：

**假设 1：SPEC 本身正确。** 冷启动验证证明错误率很高（7/15 tasks 有缺陷）。Superpowers 缺乏内置的 SPEC 验证机制。

**假设 2：Subagent 忠实实现 SPEC。** Subagent 忠实实现了模糊的 SPEC——"关键词匹配"→空格分词。它不会说"中文场景空格不够"。这模型在英语编程社区有效（社区共识充填空白），但在中文场景、科研工具体领域，SPEC 的空白无法被充填。

**假设 3：Task 独立。** 15 task PLAN 假设每个 task 可并行。但 T4+T5（摄入+检索）、T10+T11（agent+loop）存在强耦合。改进：不强求全量并行，分波次——第一波独立模块，第二波耦合模块，第三波集成。每波之间有冻结边界。

---

## 核心洞察

这个项目最重要的贡献不是检索算法或前端 UI——是 **Governance**。

V3 的三层纵深——guardrail（12 个危险模式正则）+ HITL（threading.Event 审批状态机）+ auto_validate（文件写入后 py_compile/pytest 回灌）——全部是确定性代码。没有一行依赖 LLM 的 prompt。

这揭示了一个根本洞见：**当 LLM 负责思考，工程价值在 harness。** LLM 会越来越聪明，但聪明和可靠是两回事。Harness 的职责不是让 Agent 更聪明，而是确保聪明的 Agent 不会做蠢事——guardrail 设"绝不能"的硬边界，HITL 设"你可以做但要我确认"的审批带，auto_validate 设"你做完了我帮你检查"的反馈闭环。

"使 Agent 工作"（性能）和"使其安全"（治理）之间的张力用架构设计化解：ToolRouter 按意图限缩工具暴露面，governance 层对暴露面做纵深拦截。Agent 在受限空间内自由行动——不是要么完全自由要么完全受限的二元选择。Harness 是马鞍和缰绳，LLM 是马达。马鞍越紧、马达越安全；缰绳越短、方向越可控。但只有缰绳没有马达，也到不了目的地。
