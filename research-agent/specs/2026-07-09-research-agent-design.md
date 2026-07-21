# 科研伙伴 Agent —— 设计规约

> 状态: Draft | 日期: 2026-07-09 | 版本: V1

---

## 目录

1. [问题陈述](#1-问题陈述)
2. [用户故事](#2-用户故事)
3. [功能规约](#3-功能规约)
4. [非功能性需求](#4-非功能性需求)
5. [系统架构](#5-系统架构)
6. [数据模型](#6-数据模型)
7. [凭据与分发设计](#7-凭据与分发设计)
8. [技术选型与理由](#8-技术选型与理由)
9. [验收标准](#9-验收标准)
10. [风险与未决问题](#10-风险与未决问题)

---

## 1. 问题陈述

### 1.1 目标用户

研究生（硕博）到青年教师，覆盖科研全阶段。低年级用户侧重引导和入门，高年级用户侧重效率和跨领域创新。

### 1.2 核心痛点

1. **知识不持续** — 现有 LLM 工具每次对话都是孤岛。Agent 不记得你之前读过哪些论文、你的研究背景、上次讨论到哪一步。研究者无法积累"共同的工作记忆"。
2. **推理深度不足** — 现有工具能回答事实问题，但无法参与真正的科研推理：从文献中发现 gap、对比不同方法的优劣、基于用户已有知识设计实验。深层推理需要知识广度+深度的支撑，这依赖于持续积累。
3. **工具链割裂** — 文献管理、笔记、讨论、代码实验分散在不同工具中，没有一个统一的 Agent 把流程串联起来。
4. **实验数据断层** — 所有现有 AI 工具都能读论文，没有工具能读你的实验数据。文献中说的"应该有效"和你实验跑出来的"没效果"之间没有 AI 桥接。

### 1.3 价值主张

一个**随用户一起成长的科研伙伴 Agent**。它通过持续记忆用户研究脉络和实验数据，参与从文献到实验到报告的完整科研闭环。它记得你的研究方向、读到过什么、还在等什么实验结果——像一个真正了解你的长期合作者。

### 1.4 为什么值得做

市场调研显示，现有 AI 科研工具**全部聚焦在文献搜索和合成**（红海），而**实验数据整合 + 长程记忆 + 异步协作编排 + 主动建议**这四样能力目前没有任何产品覆盖（空白市场）。我们的差异化不是"更准的搜索"，而是"不会遗忘的研究伙伴"。

---

## 2. 用户故事

### 核心科研工作流

**US-1：新领域快速入门**

> 我是研究生，当告诉 Agent "我想了解强化学习在药物设计中的应用"，Agent 给我一个结构化的领域概览（关键论文、主流方法派系、核心开放问题），并标注每个结论的置信度和来源。主动指出哪些是"公认结论"、哪些是"仍有争议"。

**US-2：可信问答与溯源**

> 作为研究者，当我针对某篇论文或某个方法提问时，Agent 必须引用原文段落作为证据。当证据不足以支撑确定回答时，Agent 明确说"不确定"而非猜测，并给出"如果要确认这点可以查什么"的建议。

**US-3：文献综述协作撰写**

> 作为博士生，我希望与 Agent 协作写一篇综述。Agent 根据积累的知识体系自动生成综述大纲；对每一节，调取相关论文并生成初稿段落（带引用）；我能逐段审阅、修改。Agent 不捏造不存在的引用。

**US-4：研究方向研判**

> 讨论了几个 research idea 后，Agent 根据文献知识帮我分析每个方向的：新颖性、可行性、与现有工作的差异、潜在实验成本。标注每个判断的推理链。

**US-5：实验设计与自主执行**

> 当确定研究方向后，Agent 帮我设计完整实验方案（对照设置、评估指标、baseline 选择），并通过 MCP 连接计算资源实际执行实验。在实验过程中若结果异常，Agent 自动检测、分析原因、尝试修正并重跑，形成 agentic self-correction loop。所有操作和修正记录可追溯。

**US-6：跨会话知识持续积累**

> 作为长期使用用户，昨天和 Agent 讨论过"图神经网络在蛋白质结构预测"，今天聊"分子动力学模拟"时，Agent 主动关联两个话题。所有知识在 Agent 的记忆档案中持久化存储。

**US-7：通过 Skill 扩展能力**

> 当我需要特定工具或工作流（如 Web of Science API 检索、RDKit 分子计算），通过安装或编写 Skill 扩展 Agent 能力。Skill 执行结果纳入知识上下文。不需要修改 Agent 核心代码。

**US-8：Agentic Loop 自我纠错**

> 在任何多步骤任务中（文献检索、综述生成、实验执行），Agent 检测到中间结果的错误或不一致时，自动回溯纠正。纠错过程对用户可见，用户可随时介入。

### 项目管理与多项目编排

**US-PM1：多项目自动路由**

> 同时推进两个课题，当我说"上次那个 HPLC 结果拿到了"，Agent 自动识别指的是哪个项目，恢复该项目上下文——上次讨论到哪、等待什么结果、下一步做什么。无需手动切换。

**US-PM2：异步等待与断点续研**

> 当研究步骤需要我做人工实验时，Agent 将当前项目状态标记为"等待中"，记录等什么数据、预期时间、下一步。几天后我带着数据回来，Agent 先复述断点然后推进分析。

**US-PM3：研究计划动态编排**

> Agent 生成分步研究计划，每步标注责任人（Agent/用户/MCP工具）和预期耗时。某一步结果推翻前置假设时，Agent 自动调整后续步骤。

**US-PM4：跨项目知识联想**

> Agent 发现自己参与的两个项目之间有可迁移的发现时，主动关联并注明来源（哪个项目、哪篇论文）。

**US-PM5：项目全景仪表**

> 问"我手上有什么在进行"时，Agent 展示它参与的各项目当前阶段、等待事项、最近进展摘要。每个条目可交互追问详情。

**US-PM6：Agent 能力自述**

> Agent 能说清楚："我目前在协作的项目有 [项目列表]。我的知识覆盖：[领域]。我的能力边界：[化学合成方法学]，生信方面的事建议找你的另一个 Agent。"

---

## 3. 功能规约

### 3.1 模块总览

| 模块  | 名称                     | V1 优先级 |
| --- | ---------------------- | ------ |
| A   | 文档摄入与 RAG 检索（含入库去重+矛盾检测） | P0     |
| B   | 项目记忆与经验沉淀（LangGraph checkpoint 驱动） | P0     |
| C   | 会话上下文与自动路由         | P0     |
| D   | 研究方向研判                 | P1     |
| E   | Skill/MCP 插件系统         | P0     |
| F   | Agentic Loop 自我纠错      | P0     |
| G   | 知识图谱（V2 预留）            | V2     |

---

### 模块 A：可溯源多源 RAG（Traceable Multi-Source RAG）

**概述**：Agent 的知识底座。所有论文摄入后经清洗、分块、嵌入向量库。新论文入库时自动检测与已有知识的 agree/conflict 关系，同意则汇聚多源溯源链（verified_sources），矛盾则标记冲突。召回时每段文本可追溯到原始论文的唯一位置，丢失 PDF 也不丢失原文。

**命名**：可溯源多源 RAG —— Provenance-Aware Multi-Source RAG。三个核心能力：每段原文可回溯到来源论文（可溯源）、每个观点支持多篇论文交叉验证（多源）、从检索命中可恢复完整论文原文（闭环）。

---

#### A1. 文本清洗管道

| 问题 | 处理 |
|------|------|
| PDF 页眉页脚 | 正则匹配去除每页以短数字/短文本结尾的页眉行 |
| 参考文献部分 | 正则 `#+ References|Bibliography|参考文献` 截断，之后不参与分块（保留在完整文本中） |
| 多栏 PDF | pymupdf 按阅读顺序提取，不需手动手动处理 |
| 多余空格/空行 | 压缩连续空白但保留段落边界（双换行 = 段落边界） |
| 数学符号 | Unicode 原样保留，分词时不拆分 |
| 图片标题 | 保留 `Figure N:` `Fig. N:` 文本，标记 `content_type: caption` |
| 保留引用编号 | [1], [2] 标记保留，RAG 需要溯源 |

---

#### A2. 分块策略

**核心理念**：每个 chunk 必须是完整的语义单元，可独立被理解。设计命名：**章节为墙、段落为砖、句子为胶**。

**策略表**：

| 策略 | 做法 | 为什么 |
|------|------|-------|
| 章节感知 | `^#{1,4}` 正则拆分为大段，任何规则不可跨章节 | 章节是绝对的墙 |
| 段落级 | 双换行 = 段落边界，以此为自然切分点 | 保持语义完整 |
| 最小长度 | chunk 合并短段落至 ≥ 200 token | 太短无意义 |
| 最大长度 | chunk > 800 token → 句子边界 `. ` 处切分（最接近 800 的位置） | 太长降低检索精度 |
| 重叠 | 相邻 chunk 重叠前一 chunk 的尾 3 句 | 避免论点被边界截断 |
| 特殊内容隔离 | 表格（含 `\t` 或 `|` 模式）、代码块、数学公式独立成 chunk | 不让非正文稀释语义 |
| 尾段兜底 | 不达标的残余合并到最后一个有效 chunk | 不丢弃任何内容 |

**冲突仲裁规则**（按优先级）：

```
1. 章节感知 —— 最先执行，最高层级分割。后续规则永远在章节内部运作
2. 特殊内容隔离 —— 检测到表格/代码/公式 → 独立 chunk，标记 content_type
3. 段落聚合（最小长度）—— 连续短段落合并至 ≥ 200 token
4. 超长切割（最大长度）—— 单段落 > 800 token → 句子边界切分
5. 重叠注入 —— 相邻 chunk 重叠尾 3 句（章节边界处跳过）
6. 尾段兜底 —— 残余 < 200 token → 合并到上一个 chunk
```

**重叠跨章节**：不重叠。`^#{1,4}` 正则处强制断重叠。

---

#### A3. 内容质量过滤与准入

**source_score 打分规则**（基础分 3，然后累积）：

| 条件 | 分数变动 |
|------|---------|
| 顶会/顶刊 (Nature, Science, Cell, NeurIPS, ICML, ACL, CVPR, etc.) | +4 |
| 有 DOI 且引用 > 100 | +3 |
| 有 DOI 且引用 > 10 | +2 |
| arXiv 预印本有 DOI 或引用 > 5 | +1 |
| 会议论文（非顶会但有同行评审） | +1 |
| 年份 > 15 年前且引用 < 5 | -1 |
| 无 DOI 无 arXiv ID 无引用 | score = 1（直接拒绝） |
| 来源域名: zhihu.com, medium.com, blogspot, 微信公众号 | score = 1（直接拒绝） |
| 掠夺性期刊/Beall's list 命中 | score = 2（隔离，用户手动通过） |

**准入决策**：

```
新论文摄入
    ↓
[1] 去重（DOI / 标题完全一致 → 拒绝；编辑距离 < 3 → 合并版本）
    ↓
[2] 元数据获取 + 评分
    ↓
[3] 准入判断:
    ├─ source_score ≥ 4 → ✅ 入库
    ├─ source_score == 3 → ⚠️ 入库但标记"未经同行评审"（仅 arXiv）
    ├─ source_score == 2 → ⚠️ 隔离区，用户手动通过
    └─ source_score == 1 → ❌ 拒绝，提示用户具体原因
```

**入库报告内容**：新增 N 篇、拒绝 M 篇（附原因）、隔离 L 篇（需手动处理）。

---

#### A4. 多源溯源（Multi-Source Provenance）

**概述**：新论文入库时检测与已有知识的 agreement/conflict。同意 → 汇聚多源验证链。矛盾 → 标记冲突。

**流程**：

```
新论文 chunk_7: "柱温每升10°C保留时间前移0.3min"
    ↓ 向量检索 Top-3 相似已有 chunk
命中 Paper A chunk_3（相似度 > 0.85）
    ↓ LLM 判断
relation: "agree"
    ↓ 双向追加 verified_sources:
  Paper A chunk_3 追加: {paper_id: "paper_b", chunk_index: 7}
  Paper B chunk_7 追加: {paper_id: "paper_a", chunk_index: 3}
    ↓ 召回时
chunk_3 返回: verified_sources: [Paper A ch3, Paper B ch7, Paper C ch1]
Agent: "该结论被3篇独立论文验证"
```

**relation 判断规则**：
- `agree` → 双向追加 `verified_sources`
- `contradict` → 写入 `chunk_conflicts` 表，双方标记冲突链表
- `unrelated` → 不处理

**数据落点**：`verified_sources` 存储在 Chroma 每个 chunk 的 metadata 中，格式为 `[{paper_id, chunk_index}]` 数组。

---

#### A5. 检索流程（Agentic RAG）

| 步骤 | 行为 |
|------|------|
| **1. 问题分析** | Agent 判断查询意图（语义型/关系型/混合型） |
| **2. 混合检索** | 向量检索 + BM25 + RRF 融合。content_type 过滤（默认只返回 paragraph，明确需要数据时包含 table） |
| **3. 多源信息组装** | 返回的 chunk 自带 verified_sources → Agent 在上下文中可看到"此结论被 N 篇论文验证" |
| **4. 溯源降级** | 检索不足 → fallback 到无来源评分的纯向量搜索 → 标记"未经图谱验证" |
| **5. 自纠正** | 不足 → 反思 → 修改查询 → 回到步骤 2（最多 3 轮） |
| **6. 闭环溯源** | 用户点"看完整论文" → `coll.get(where={"paper_id": id})` → 按 chunk_index 排序拼接 → 还原全文 |

---

#### A6. Chunk Metadata Schema

每个 chunk 在 Chroma 中存储以下 metadata：

```json
{
  "paper_id": "uuid-xxx",
  "chunk_index": 3,
  "section": "2. Methods",
  "content_type": "paragraph",
  "verified_sources": [
    {"paper_id": "paper_a", "chunk_index": 3},
    {"paper_id": "paper_c", "chunk_index": 5}
  ]
}
```

**字段说明**：

| 字段 | 用途 |
|------|------|
| paper_id | 与 SQLite papers 表的一一对应外键 |
| chunk_index | 按此排序可拼接还原全文 |
| section | 检索时可过滤特定章节（如只看 Methods） |
| content_type | paragraph / table / caption / formula / code；默认检索只返回 paragraph |
| verified_sources | 同一观点的其他论文溯源列表。为 `[]` 表示本论文独立提出 |

**全文还原**：
```python
coll = get_collection()
results = coll.get(where={"paper_id": paper_id})
sorted_chunks = sorted(results["metadatas"], key=lambda m: m["chunk_index"])
full_text = "\n".join(results["documents"][i] for i in sorted_chunks)
```
——无需额外存储完整文本文件。

---

#### A7. 存储与性能

- 全量 chunk 在同一 Chroma collection + 一个 BM25 索引
- 存储估算：千篇论文 ≈ 100-200 MB（仅文本 + embedding）
- Chroma 内置按 paper_id 的 get/delete 操作，O(1) 全文恢复
- 磁盘上限 500MB，超出提醒用户清理

---

### 模块 B：项目记忆与经验沉淀

**概述**：每个项目 = 一个 LangGraph checkpoint（`thread_id` = project_id）。Agent 启动时加载所有项目索引，切换项目时自动读取对应 checkpoint 的完整上下文。LangGraph 负责状态的序列化/反序列化。

#### B1. 项目模型（升级版）

| 字段                 | 说明                                                     |
| ------------------ | ------------------------------------------------------ |
| project_id         | 唯一标识 = LangGraph `thread_id`                           |
| topic              | 项目主题                                                   |
| status             | 进行中 / 等待用户 / 暂停 / 完成                                   |
| history_summary    | 自然语言摘要（"我们在做什么、目前到哪里了"）                                |
| accumulated_wisdom | **经验沉淀**，结构化知识（见下）                                      |
| plan               | 研究计划分步列表，每步 {step, owner, status, depends_on}          |
| pending_task       | 当前等待用户完成的事项（含预期时间、数据格式要求）                              |
| intro_summary      | 本项目的 Agent 视角摘要（Agent 参与这个项目时知道什么、擅长什么），供跨项目查找或多 Agent 互发现用 |

#### B2. 经验沉淀（accumulated_wisdom）

> 每次压缩或项目阶段结束时，Agent 反思本轮讨论，**从对话中提取可复用的结构化知识**，写入 `accumulated_wisdom`：

```json
{
  "sops": [
    "HPLC纯度分析标准流程: C18柱，甲醇:水=70:30，1mL/min，254nm"
  ],
  "pitfalls": [
    {
      "现象": "纯度从85%波动到92%——重复性差",
      "根因": "柱温未稳定就进样",
      "解决": "每次调温后平衡15min再进样",
      "改进": "升温步长从10°C降到5°C"
    }
  ],
  "frameworks": [
    "遇到重复性问题→排查优先级：温度→流速→溶剂批次",
    "同类化合物可复用此梯度和波长条件作为起点"
  ],
  "agent_improvements": [
    "下次设计HPLC实验时主动提醒柱温平衡时间",
    "用户提到'重复性差'时优先建议排查温度"
  ]
}
```

**后续使用**：
- 用户新开同类实验 → Agent 说"上次你优化过 HPLC 条件，SOP 是...要不要沿用？"
- 用户说"重复性又不好了" → Agent 查 pitfalls："你上次柱温没平衡导致重复性差，这次先确认平衡时间？"
- 用户说"帮我设计实验" → Agent 从 frameworks 出发给排查思路
- 跨项目遇到类似问题 → Agent 说"你在 HPLC 项目里遇到过类似的，当时的方案是..."

这就是 Agent "随用户一起成长"的机制——不是靠更聪明的模型，而是靠积累的经验。

#### B3. 记忆生命周期

```
Agent 启动
    ↓
加载所有项目索引（topic + intro_summary + 状态）
    ↓
用户发言 → Router 识别项目 → LangGraph 自动恢复该 thread_id 的 checkpoint
    ↓
对话进行（每轮通过 LangGraph checkpoint 自动持久化状态）
    ↓
触发压缩（任一条件满足）:
    ├─ 对话超过 40 轮
    ├─ 用户说"总结一下进展"
    ├─ 项目阶段结束（状态变更为暂停/完成）
    └─ Agent 检测到 token 预算接近上限
    ↓
反思压缩（1 次 LLM 调用）:
    ├─ 更新 history_summary
    ├─ 提取/更新 accumulated_wisdom（sops, pitfalls, frameworks, agent_improvements）
    ├─ 更新 intro_summary（Agent 视角）
    └─ 缩短 messages 列表为最近 10 轮
```

---

### 模块 C：会话上下文与自动路由

**概述**：没有"窗口"概念。一个 CLI 入口，根据用户发言自动路由到项目。每轮传给 LLM 的上下文固定天花板。

#### C1. 每轮上下文组装

```
传给 LLM 的上下文:
  ┌─ 系统指令（固定 ~500 token）
  ├─ 当前项目的 history_summary + intro_summary（~400 token）
  ├─ RAG 检索结果（~2000 token）
  ├─ 当前项目的最近 20 轮 messages（LangGraph 框架自动管理）
  └─ 当前用户消息（~50 token）
  ≈ 总计 3000-5000 token —— 远低于任何模型上下文窗口上限
```

**关键设计**：
- **不传 User Profile** → 你的研究方向自然隐含在项目对话中
- **不传 Agent 自述** → 内含在项目 intro_summary 里
- **不传历史对话的全文** → 只有最近 20 轮；早期的已被压缩进 history_summary 和 accumulated_wisdom
- **切换项目 = 切换 thread_id** → 不同项目上下文完全隔离，从不污染

#### C2. 自动路由

```
用户发言 → Router 分析意图:
    ├─ 关键词匹配已有项目 → 切换到该项目的 checkpoint
    ├─ 用户说"上次项目里提到的那个方法" → Router 读所有项目的 intro_summary → 找到匹配 → 切换
    ├─ 可能涉及多项目交叉 → 加载所有相关项目的 intro_summary
    └─ 新主题 → 创建新项目（新的 thread_id）
```

#### C3. 防幻觉 / 防偷懒（不变）

- 每轮回答末尾附：引用的来源清单 + 自信度标记（确定 / 推测 / 不确定）
- 不确定时明确说"不确定"并建议用户如何验证

---

### 模块 D：研究方向研判

| 维度      | 规约                                                                                                                                                                                |
| ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **输入**  | 用户提出的 N 个 research idea，或 Agent 从文献积累中自动提议                                                                                                                                        |
| **行为**  | 1. 对每个 idea，Agent 检索相关文献：是否已有类似工作？关系是支持还是矛盾？<br>2. 输出研判矩阵：新颖性（相关文献少=高新颖）、可行性（相关方法实验数据的可靠度）、与现有的差异、估算实验成本<br>3. 每个评分附带推理链（"判断新颖性 8/10 的依据：仅 2 篇论文涉及 X 和 Y 的交叉"）<br>4. 用户可追问任何判断的依据 |
| **防幻觉** | 不允许"可能"、"应该"等模糊词；每个陈述追溯到文献或标注"无数据支撑，仅推测"                                                                                                                                          |

---

### 模块 E：Skill + MCP 插件系统

**概述**：Agent 能力扩展的双通道——Skill（工作流编排）和 MCP（外部工具集成）。Skill 定义"做什么、怎么做"，MCP 提供"能调什么工具"。Agent 启动时自动连接已配置的 MCP server，发现可用工具；Skill 可在工作流中调用 MCP 工具。

#### E1. MCP 集成（模型上下文协议）

**MCP 协议遵循**：agent 通过标准 MCP 协议（JSON-RPC over stdio/HTTP）连接 MCP server，自动发现 tools/resources/prompts 列表。

**配置文件**：`~/research-agent-data/mcp_servers.yml`

```yaml
servers:
  - name: "semantic-scholar"
    command: "npx"
    args: ["-y", "@anthropic/mcp-server-semantic-scholar"]
  - name: "python-executor"
    command: "python"
    args: ["-m", "mcp_server_python"]
  - name: "filesystem"
    command: "npx"
    args: ["-y", "@anthropic/mcp-server-filesystem"]
```

**生命周期**：

```
Agent 启动
    ↓
读取 mcp_servers.yml → 逐个启动 MCP server 子进程
    ↓
每个 server 发送 initialize → tools/list → 获取可用工具列表
    ↓
工具注册到 Agent 的工具目录（tools registry）
    ↓
Agent 运行时：当 LLM 决定调用工具时 → tools/call → 返回结果
    ↓
Agent 退出 → 关闭所有 MCP server 子进程
```

**安全约束**：

| 规则 | 说明 |
|------|------|
| MCP server 仅允许本地 stdio 连接（不暴露网络端口） |
| 首次连接新 server → 用户确认授权 |
| 工具调用涉及文件写入/网络请求 → 预览确认 |
| 工具调用结果超 10000 token → 截断并标记 |
| 禁止执行系统命令（shell、rm -rf 等） |
| MCP server 超时 30s → 强制终止并报告 |

#### E2. Skill 系统（工作流编排层）

**Skill 定义**：一个 Skill = 一段工作流指令 + 可选的 MCP 工具声明 + 触发条件

| 字段 | 说明 |
|------|------|
| name | 唯一标识 |
| description | 给 Agent 的语义描述，用于自动判断是否触发 |
| trigger | 关键词列表 / 手动命令 / Agent 自动判断 |
| system_prompt | 该 Skill 专用的系统指令追加 |
| mcp_tools | 该 Skill 需要的 MCP 工具列表（从 MCP registry 中按名称匹配） |
| workflow | 步骤定义（Agent 参考而非硬编码执行） |

**V1 内置 Skill**：

| Skill | 核心能力 | 依赖的 MCP 工具 |
|-------|---------|---------------|
| **paper-search** | 检索新论文 + 摄入知识库 | 需 MCP server: semantic-scholar |
| **literature-review** | 生成综述大纲 → 逐节初稿（带引用）→ 自检引用 | 无（纯 LLM + 本地 RAG） |
| **experiment-runner** | 生成实验方案 → 通过 MCP 连接计算资源执行 → 异常检测 | 需 MCP server: python-executor |
| **write-report** | 整合项目数据 + 实验结果 → 生成正式报告 | 无（纯 LLM + 本地数据） |

**Skill 执行流程**：

```
用户触发 / Agent 判断需要某 Skill
    ↓
加载 Skill 的 system_prompt
    ↓
从 MCP registry 匹配 Skill 声明的 mcp_tools
    ↓
Agent 按 workflow 步骤执行（可灵活调整路线）
    ├─ 需要工具 → 调用 MCP tool → 结果纳入上下文
    └─ 纯 LLM 步骤 → 生成
    ↓
每步结果纳入上下文 + 可选持久化到项目存档
    ↓
Skill 完成 → 恢复默认 system_prompt
```

#### E3. 工具发现与注册

Agent 启动时从 MCP server 收集的工具列表注册到统一 registry：

```python
# 工具注册表（Agent 运行时可用）
tools_registry = {
    "search_papers": {
        "mcp_server": "semantic-scholar",
        "description": "Search for academic papers on Semantic Scholar",
        "parameters": {"query": "string", "limit": "int"},
        "require_confirm": False,
    },
    "execute_python": {
        "mcp_server": "python-executor",
        "description": "Execute Python code and return output",
        "parameters": {"code": "string"},
        "require_confirm": True,  # 代码执行需用户确认
    },
    "read_file": {
        "mcp_server": "filesystem",
        "description": "Read a file from the local filesystem",
        "parameters": {"path": "string"},
        "require_confirm": False,
    },
    "write_file": {
        "mcp_server": "filesystem",
        "description": "Write content to a file",
        "parameters": {"path": "string", "content": "string"},
        "require_confirm": True,  # 写文件需用户确认
    },
}
```

**Agent 调用 MCP 工具的方式**（LangGraph 中作为 tool 节点）：

```python
# Agent 在 router_node 中判断用户意图
# 如果意图需要工具 → 展示工具选项给用户确认
# 如果是纯对话 → 走正常 reasoner→retriever→generator 流程

# MCP 工具调用格式（LangGraph tool node）
def mcp_tool_node(state):
    tool_name = state.tool_call_name
    tool_args = state.tool_call_args
    server = mcp_registry.get_server(tool_name)
    result = server.call_tool(tool_name, tool_args)
    state.tool_result = result
    return state
```

#### E4. 安全（补充）

- Skill 文件不可含硬编码 API key，key 走凭据模块管理
- MCP server 仅允许本地 stdio 连接（不开放网络端口）
- 首次连接新 MCP server → 用户授权
- 涉及文件写入/代码执行/网络请求的工具调用 → 用户预览确认
- Skill 不可修改其他 Skill 或 Agent 核心代码
- MCP server 超时 30s → 强制终止

---

### 模块 F：Agentic Loop 自我纠错

#### F1. 适用场景

文献检索不足、综述引用不匹配、实验数值异常、逻辑矛盾

#### F2. 行为

| 步骤        | 行为                                  |
| --------- | ----------------------------------- |
| **自检**    | 每步结果后 Agent 自检：结果与预期一致？引用可验证？逻辑无矛盾？ |
| **检测到错误** | 标记错误类型 → 分析根因 → 生成修正动作              |
| **重试**    | 执行修正 → 重新检测 → 最多 3 轮                |
| **兜底**    | 3 轮未解决 → 返回未解决问题 + 附分析路径给用户         |
| **日志**    | 所有纠错过程记录为日志，用户可随时回溯                 |

#### F3. 收敛保障

- 每轮必须有明确改变（换检索词 / 换推理路径 / 调整实验参数），禁止原地循环
- 2 轮后无明显改善 → 强制中止，返回最佳现有结果 + 失败分析

#### F4. 边界感知

Agent 系统指令的增量规则：

> 每一步先判断自己是否具备执行能力：
> 
> - 知识库有相关信息 → 可以做
> - 需要外部数据/API → 调用 MCP，失败则如实告知
> - 需要人工实验/操作 → "这步需要你做，完成后的数据请按 [我指定的格式，如 CSV/截图/文本描述] 给我。期待的数据字段：[...]"
> - 无法确定 → "我不确定，建议你向 [方向/领域] 寻求帮助"

---

### 模块 G：知识图谱（V2 预留）

**V2 引入，V1 不做。**

规划方向：

- 单篇论文 → 论证结构图（Mind Map：主张、证据、前提、对比）
- 论文间 → 引用关系图 + 学派/争议可视化
- 构建框架：LangGraph + NetworkX/Neo4j
- Claim 去重：Leiden 社区检测 + LLM 批量判断
- 引入时先做效果评估：图是否提升了检索召回和推理深度

---

## 4. 非功能性需求

| 维度       | 规约                                                                     |
| -------- | ---------------------------------------------------------------------- |
| **性能**   | 首次检索 < 2s；Agent 反思+重检索 loop 单轮 < 5s；论文摄入 1 篇 < 30s                     |
| **可用性**  | CLI 优先（类似 opencode）；每轮回答附引用来源和自信度标记                                    |
| **安全**   | API key 通过环境变量或本地加密配置注入；MCP 连接仅 localhost；工具调用需用户确认（可配置白名单）；不执行系统破坏性命令 |
| **可观测性** | 每轮检索路径、Agent 反思日志、token 消耗量可记录；用户可回溯任何回答的来源链                           |
| **可靠性**  | 幻觉率目标 < 10%；Agent 偷懒率（不应拒绝但拒绝的频率）持续监控和优化                               |

---

## 5. 系统架构

### 5.1 组件图

```
┌───────────────────────────────────────────────────────┐
│                   CLI 界面                              │
└───────────────────────────┬───────────────────────────┘
                            │
┌───────────────────────────▼───────────────────────────┐
│                Agent Core (LangGraph)                   │
│                                                        │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐               │
│  │ Router   │ │ Reasoner │ │ Loop     │               │
│  │ 多项目   │ │ 检索策略 │ │ 自纠错   │               │
│  │ 自动路由 │ │ 知识沉淀 │ │ Controller│               │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘               │
│       │       └────┬─────┘      │                      │
│  ┌────▼─────────────▼────────────▼─────┐               │
│  │          Reasoner / Planner          │               │
│  │  "该搜什么？怎么搜？够了没？"         │               │
│  └────┬────────────────────────────────┘               │
└───────┼────────────────────────────────────────────────┘
        │
┌───────▼────────────────────────────────────────────────┐
│                   Data Layer                            │
│  ┌────────────┐ ┌──────────┐ ┌────────────────────┐   │
│  │ 向量数据库  │ │ BM25索引  │ │ LangGraph        │   │
│  │ (Chroma/   │ │(rank_bm25)│ │ checkpoint(SQLite)│   │
│  │  Qdrant)   │ └──────────┘ │ + 项目索引表       │   │
│  └────────────┘              └────────────────────┘   │
│  └────────────┘                                        │
└────────────────────────────────────────────────────────┘
        │
┌───────▼────────────────────────────────────────────────┐
│              External Dependencies                      │
│                                                        │
│  LLM (via LiteLLM)          MCP Server                 │
│  Claude / GPT / 本地模型    工具连接（计算集群、API等）  │
│                                                        │
│  Search API                     PDF Parser             │
│  Semantic Scholar / ArXiv       pymupdf / Unstructured  │
└────────────────────────────────────────────────────────┘
```

### 5.2 数据流

```
用户输入 → Router（判断属于哪个项目）
              ↓
       Reasoner（决定检索策略）
              ↓
       混合检索（向量 + BM25 + RRF）→ 也查引用关系（Semantic Scholar API）
              ↓
       Reasoner 评估充分性
         ├─ 足够 → 组装上下文 → LLM 生成回答 → 自检 → 输出
         └─ 不足 → 反思 → 修改查询 → 回到检索（max 3 轮）
              ↓
        每轮结束 → LangGraph checkpoint 自动持久化
               ↓
        触发压缩时 → 反思 → 更新 history_summary + accumulated_wisdom
```

### 5.3 外部依赖

| 依赖                     | 用途                    | 备选                |
| ---------------------- | --------------------- | ----------------- |
| LiteLLM                | LLM 统一接口              | 直接 API 调用         |
| Chroma/Qdrant          | 向量存储                  | FAISS（更轻量）        |
| rank_bm25              | BM25 索引               | Elasticsearch（过重） |
| pymupdf + Unstructured | PDF 解析                | pdfplumber        |
| Semantic Scholar API   | 论文搜索 + 元数据            | Crossref API      |
| LangGraph              | Agent 框架 + checkpoint | 自建                |

---

## 6. 数据模型

### 6.1 核心实体

| 实体                 | 核心字段                                                                                                            | 存储           |
| ------------------ | --------------------------------------------------------------------------------------------------------------- | ------------ |
| **Chunk**          | id, paper_id, text, embedding, chunk_index, section, content_type, verified_sources[{paper_id, chunk_index}] | Chroma（向量 + metadata） |
| **Paper**          | id, title, doi, year, source_score (1-10), citation_count, authors[], abstract, file_path                    | SQLite       |
| **ChunkConflict**  | id, paper_id_a, chunk_index_a, paper_id_b, description                                                       | SQLite       |
| **Project**        | id, topic, status, history_summary, accumulated_wisdom{}, intro_summary, plan[], pending_task{}, created_at, updated_at | LangGraph checkpoint (SqliteSaver) + SQLite 索引 |
| **AccumulatedWisdom** | sops[], pitfalls[{现象,根因,解决,改进}], frameworks[], agent_improvements[]                                        | 嵌入 Project checkpoint 的 state 中 |

### 6.2 已移除的实体（V1 简化）

| 原实体 | 去向 |
|------|------|
| UserProfile | 研究方向自然蕴含在各项目对话中，不单独维护 |
| AgentSelfIntro | 降级为每个 Project 的 `intro_summary` 字段 |
| Session | LangGraph checkpoint 自动管理，无需单独实体 |

### 6.2 V2 预留（不实现）

| 实体                       | 说明                     |
| ------------------------ | ---------------------- |
| Claim                    | 论文中的主张/结论节点            |
| Relation                 | Claim 之间的关系边（支持/矛盾/扩展） |
| Domain, Method, Dataset  | 领域/方法/数据集节点            |
| Paper-to-Paper citations | 引用图边                   |
| CodeRepo                 | 代码仓库链接                 |

---

## 7. 凭据与分发设计

### 7.1 API Key 管理

| Key 类型               | 管理方式                                                                  |
| -------------------- | --------------------------------------------------------------------- |
| LLM API Key          | 环境变量 `RESEARCH_AGENT_LLM_KEY` 或 `~/.config/research-agent/config.yml` |
| Semantic Scholar API | 免费，无需 key                                                             |
| 其他 API Key           | 同上配置文件，按需添加                                                           |

- 不硬编码任何 key 到代码或 Skill 文件
- 可选本地加密存储（用户自行决定）

### 7.2 分发

- **形态**：Python CLI 工具
- **安装**：`pip install research-agent` 或本地 `git clone`
- **平台**：Windows / macOS / Linux
- **数据存储**：`~/research-agent-data/`（用户可配置路径）
- **运行模式**：纯本地，无需云服务（LLM 调用除外）

### 7.3 用户数据

- 所有用户数据（论文文本、embedding、项目存档、记忆档案）存储在本地
- 不上传至任何第三方服务器
- 用户可随时备份/迁移数据目录

---

## 8. 技术选型与理由

| 层面           | 选型                                           | 理由                                                            |
| ------------ | -------------------------------------------- | ------------------------------------------------------------- |
| **语言**       | Python                                       | LLM/RAG/NLP 生态最丰富；目标用户群体熟悉                                    |
| **Agent 框架** | LangGraph                                    | 管理 agentic loop + checkpoint 持久化 + V2 直接复用建图；开源；Python native |
| **LLM 接入**   | LiteLLM                                      | 统一 Claude/GPT/本地模型接口，免费切换                                     |
| **嵌入模型**     | 默认 BGE-M3（本地），可选 text-embedding-3-small（API） | 本地优先（与是否用本地 LLM 绑定）                                           |
| **向量库**      | Chroma（V1 轻量）/ Qdrant（后期生产）                  | 本地运行，零配置                                                      |
| **BM25**     | rank_bm25                                    | 纯 Python，轻量无依赖                                                |
| **PDF 解析**   | pymupdf + Unstructured                       | 覆盖 PDF + 扫描件                                                  |
| **关系数据库**    | SQLite                                       | 零配置，适合本地单用户                                                   |
| **V2 图数据库**  | NetworkX 或 Neo4j                             | NetworkX 够用千级节点                                               |
| **分发**       | PyPI + CLI                                   | 简单，目标用户熟悉 `pip`                                               |

---

## 9. 验收标准

| 用户故事              | 验收标准                                                                                 |
| ----------------- | ------------------------------------------------------------------------------------ |
| **US-1 领域概览**     | 用户说"了解 X 领域" → Agent 30s 内返回 ≥ 5 篇关键论文 + ≥ 3 个开放问题，每项带来源 |
| **US-2 可信问答**     | 任何回答标引用；不确定时明确说"不确定"；抽查 20 个回答，幻觉率（引用与内容不匹配）< 10%                                    |
| **US-3 综述撰写**     | 触发 skill → 生成大纲 → 逐节初稿 → 输出带引用 Markdown；抽查 10 个引用，内容一致性 ≥ 90%                        |
| **US-4 方向研判**     | 3 个 idea 输入 → 输出每个方向的新颖性/可行性评分 + ≥ 2 条推理依据                                           |
| **US-5 实验执行**     | 用户同意方案 → Agent 通过 MCP 提交计算任务 → 返回结果；异常自动检测并提议修正                                      |
| **US-6 跨会话记忆**    | 第 1 天讨论 "GNN+蛋白质" → 第 2 天聊"分子动力学"时 Agent 主动关联                                        |
| **US-7 Skill 扩展** | 放入新 Skill 文件 → 重启 → Agent 可用该 Skill                                                  |
| **US-8 自主纠错**     | 检索不足时 Agent 自动修改查询重试 ≥ 1 次；错误修正记录可查看                                                 |
| **PM-1 项目路由**     | 多项目并存时，说"HPLC结果拿到了"→ Agent 自动识别并加载正确项目                                               |
| **PM-2 异步等待**     | 标记等待事项 → 关闭 → 几天后重启 → Agent 先复述断点                                                    |
| **PM-3 计划编排**     | 生成分步计划 → 某步结果推翻假设 → Agent 自动调整后续步骤                                                   |
| **PM-4 跨项目联想**    | Agent 发现两项目有可迁移知识 → 主动关联并说明来源                                                        |
| **PM-5 项目仪表**     | 问"我手上有什么"→ 展示各项目阶段、待办、进展摘要                                                           |
| **PM-6 能力自述**     | Agent 能说清自己参与的项目和知识覆盖范围                                                              |

---

## 10. 风险与未决问题

### 10.1 风险矩阵

| 风险                       | 等级   | 应对                                     |
| ------------------------ | ---- | -------------------------------------- |
| **幻觉不可控**                | 🔴 高 | 强制引用溯源 + 自信度标记；不确定时宁可拒绝；V1 设幻觉率基线持续监控  |
| **Agent 偷懒**             | 🟡 中 | 监控简短拒绝频率；retry 设为强制性行为（模块 F：loop 收敛保障） |
| **检索召回不足**               | 🟡 中 | 混合检索（向量+BM25）缓解；agentic loop 多轮重试兜底    |
| **Loop 发散 / token 消耗失控** | 🟡 中 | 最多 3 轮 + 必须每轮有明确变化 + 2 轮无改善强制中止        |
| **LLM 成本**               | 🟢 低 | 用户自行提供 API key + 本地模型可选                |
| **MCP 安全**               | 🔴 高 | 本地 only + 工具调用需用户确认 + 不执行系统破坏性命令       |
| **项目管理复杂度超出收益**          | 🟡 中 | V1 只做基础项目存档 + 路由，不追求甘特图级别              |
| **V2 图对 V1 的兼容**         | 🟡 低 | V1 数据层预留图扩展接口；Paper 表 + Chunk 表保持稳定    |
| **长尾论文质量污染检索**           | 🟡 中 | 来源评分 + 检索排序；不丢弃低分论文但不优先展露              |

### 10.2 未决问题

1. **嵌入模型**：BGE-M3（本地）vs text-embedding-3-small（API）→ 跟随 LLM 选择：本地 LLM → 本地嵌入；API LLM → API 嵌入
2. **UI 形态**：V1 CLI；是否考虑轻量 TUI（如 Textual）→ 待定
3. **论文来源**：用户手动指定 watch 目录 vs 每次导入 → V1 支持两者
4. **LangGraph checkpoint 与项目存档的职责划分**：checkpoint 管会话级状态，项目存档管长期研究进度。边界需在实现阶段明确
5. **多 Agent 场景（V2+）**：Agent 之间发现和通信机制的过早设计不在 V1 范围

---

## 附录 A：V1 明确的非目标（Out of Scope）

以下**不**在 V1 范围内：

- 多 Agent 群聊和跨领域 Agent 交互
- 知识图谱可视化（V2）
- Claim 级精细图谱构建（V2）
- 多用户系统
- Web UI
- 云端部署
- 跨 Agent 项目协作
- 移动端
---

## V2 迭代 (2026-07-10 ~ 2026-07-21)

### V2 变更概述

V2 在 V1 harness 基础上做了以下主要变更：

**架构变更**
- LangGraph → litellm native function calling (tool_choice=""auto"")
- JSON 解析 action → OpenAI function call schema
- Skill/MCP 作为独立模块注册 → context injection 模式

**工具系统重构**
- 4 个硬编码 action → 11 个 ToolRegistry 工具
- 新增: shell_exec (foreground/background), file_read/write/edit/glob/grep, check_tasks
- my_tools/ 目录自动导入用户自定义工具

**上下文引擎重构**
- 固定 4000 token 截断 → 模型自适应 (DeepSeek 64K, GPT-4o 128K)  
- 两个 build_context/build_chat_context → 统一 build_context + messages 累积
- 新增 context injection (SURVEY_WORKFLOW) 替代 Python handler skill

**前端重写**
- 纯文本聊天 → React + TypeScript + react-markdown + Mermaid
- 新增: 侧边栏项目管理、多对话 ChatTabs、工作区可拖拽侧边栏、论文库 CRUD
- 桌面应用: pywebview + tkinter 原生文件夹选择器

**检索优化**
- Semantic Scholar → arXiv API
- 固定分块 → TF-IDF 语义切块
- RRF k=10 → k=60

### V2 关键决策

| 决策 | V1 | V2 | 理由 |
|------|------|------|------|
| Agent 循环 | LangGraph | litellm function calling | 调试透明，工具稳定性高 |
| Skill 实现 | Python handler | context injection | LLM 保留流程控制权 |
| 上下文 | 硬编码 4000 tokens | 模型自适应 | 现代 LLM 支持 64K+ |
| 前端 | 无 | React + pywebview | 用户体验需求 |
| 论文搜索 | Semantic Scholar | arXiv API | S2 搜索质量差 |

### V2 验收

- [x] function calling 替代 JSON 解析
- [x] 11 工具注册表可插拔
- [x] context injection SURVEY_WORKFLOW
- [x] 后台任务 + check_tasks
- [x] 多项目工作区隔离
- [x] 桌面应用打包
- [x] 综述生成: 17 read, 17 cited, 16K chars
