# Research Agent V2 — 迭代计划

> 日期: 2026-07-11 | 基于 V1 交付物 + 原始 spec 的 V2 预留 + 开发中发现的缺口

---

## 一、V1 回顾：已有什么

| 模块 | 状态 | 说明 |
|------|------|------|
| 可溯源多源 RAG | ✓ | 向量+BM25+RRF 混合检索，Chroma 存储 |
| 项目记忆 | ✓ | SQLite 存储项目/论文，对话 SQLite+Chroma 双层 |
| 上下文管理 | ✓ | Token 感知滑动窗口，优先级压缩 |
| Agent 主循环 | ✓ | 自实现 while 循环，LLM 决策→分发→验证 |
| Agentic Loop | ✓ | 自纠错，最多 3 轮重试，MAX_ROUNDS 硬截断 |
| Skill 系统 | ✓ | 3 个内置 skill（论文搜索/文献综述/写报告），触发词匹配 |
| MCP 客户端 | ✓ | JSON-RPC over stdio，工具注册/发现/调用 |
| 护栏 | ✓ | 代码级 guardrail，危险动作拦截 |
| 幻觉检测 | ✓ | validate.py，引用交叉验证，置信度标记 |
| CLI | ✓ | chat / status / history / review 命令 |
| 评估 | ✓ | 消融框架，R@5/P@5/MRR 指标 |

---

## 二、V2 目标

从"能检索的回答助手"升级为"理解论证结构的研究伙伴"。

**核心差异化**：知识图谱驱动的论证推理 + 断点续研 + 中文原生支持。

---

## 三、功能模块

### 模块 G：知识图谱（P0，V2 核心）

**概述**：从"检索段落"升级到"理解论证结构"。

**子模块**：

| 子模块 | 内容 | 技术 |
|--------|------|------|
| G1. 论文内论证图 | Claim → Evidence → Premise，LLM 抽取 + 确定性校验 | `networkx` |
| G2. 论文间引用图 | 引用关系 + 学派聚类 + 争议检测 | `networkx` + Leiden |
| G3. 图增强检索 | 检索命中 chunk → 导航到关联 Claim → 发现矛盾/支持证据 | 图游走 + RAG |
| G4. 图可视化 | `research-agent graph --paper "Attention Is All You Need"` 输出论证结构 | `matplotlib` 或终端 ASCII |

**设计原则**：
- 图构建是离线的（论文摄入时），检索时只读图，不加重检索延迟
- 图节点用 Chroma 嵌入，图边用 NetworkX 存储
- 图检索和文本检索混合：先搜文本 chunk → 通过 chunk 的 paper_id 查图 → 获取关联节点

**V1 数据兼容**：Paper 表 + Chunk 表不变，新增 Claim 表 + Relation 表。

### 模块 H：中文原生支持（P0）

**V1 问题**：路由用空格分词（中文无效），嵌入模型用 ONNX MiniLM（英文）。

| 子模块 | 内容 |
|--------|------|
| H1. 中文分词 | `jieba` 替换空格分词，路由和 BM25 都受益 |
| H2. 中文嵌入 | `bge-small-zh` 或 `text2vec-base-chinese` 替换 `onnx-mini-lm` |

### 模块 I：断点续研（P0）

**概述**：Agent 在需要人工操作时自动标记 WAITING 状态，用户回来后自动恢复上下文。

**子模块**：

| 子模块 | 内容 |
|--------|------|
| I1. 自动标记 | Agent 检测到需要人工实验 → 自动设置 `status=WAITING` + 填充 `PendingTask` |
| I2. 上下文恢复 | 检测 `status=WAITING` → 输出断点摘要 → 恢复 `status=ACTIVE` |
| I3. CLI 命令 | `research-agent resume` 列出所有等待项目，`--project` 恢复指定项目 |

### 模块 J：跨项目知识联想（P1）

**概述**：Agent 发现两个项目之间的可迁移发现时主动关联。

```
用户: 蛋白质结构预测用了什么方法？
Agent: 你的 Transformer NLP 项目中讨论的 multi-head attention 机制，
      在 AlphaFold 的蛋白质结构预测中也被使用了（异构注意力）。
      这是跨项目可迁移的知识点。
      [来源: paper:xxx, 项目: Transformer NLP]
```

### 模块 K：研究方向研判（P1）

**概述**：US-4。Agent 对提出的 research idea 做结构化分析。

**输出格式**：
```
Idea: 用强化学习优化 HPLC 流动相比例

新颖性: ★★★☆☆ — 文献中有类似工作，但你的方法组合是新的
可行性: ★★★★☆ — 实验条件你已有，RL 框架成熟
差异点: 现有工作用网格搜索，你用 RL 自适应
实验成本: 低 — 每次运行 ~30min，预计 50 次收敛
风险: 局部最优问题，可能需要多种探索策略

[推理链: 基于 3 篇相关论文 + 你的 HPLC 项目历史]
```

### 模块 L：可配置模型（P1）

**V1 问题**：模型硬编码 `claude-3-haiku-20240307`，集成测试用 monkey-patch 映射。

```yaml
# config.yml
model:
  provider: deepseek
  name: deepseek-chat
  api_key_env: DEEPSEEK_API_KEY

# 或
model:
  provider: anthropic
  name: claude-3-haiku-20240307
  api_key_env: ANTHROPIC_API_KEY
```

### 模块 M：评估增强（P2）

**V1 问题**：只有 1 个领域 3 篇论文，27 条查询。

| 子模块 | 内容 |
|--------|------|
| M1. 多领域扩展 | 5 个领域 × 5 篇论文 = 25 篇，含化学/生物/材料 |
| M2. 图评估 | G1-G3 完成后，对比 E4(agentic) vs E5(graph) vs E6(graph+agentic) |
| M3. 中文评估 | 中文查询 + 中文论文，评估 R@5/P@5 在中文场景下的表现 |

### 模块 N：Skill 插件市场（P2）

**概述**：从 3 个内置 Skill 扩展为可安装第三方 Skill。

```bash
# 安装第三方 Skill
$ research-agent skill install https://github.com/xxx/rdkit-skill

# 列出已安装
$ research-agent skill list
  paper-search      内置    搜索论文
  literature-review 内置    写综述
  write-report      内置    写报告
  rdkit-calc        第三方  分子计算
```

---

## 四、优先级总结

| 优先级 | 模块 | 预计工作量 | 价值 |
|--------|------|-----------|------|
| **P0** | G. 知识图谱 | 大（3-4 天） | 核心差异化 |
| **P0** | H. 中文支持 | 小（半天） | 目标用户刚需 |
| **P0** | I. 断点续研 | 小（半天） | 已有基础设施，串起来即可 |
| **P1** | J. 跨项目联想 | 中（1 天） | 知识图谱的自然延伸 |
| **P1** | K. 研究方向研判 | 中（1 天） | 未实现的 US-4 |
| **P1** | L. 可配置模型 | 小（半天） | 开发和部署都受益 |
| **P2** | M. 评估增强 | 中（1 天） | 图评估需要 G 先完成 |
| **P2** | N. Skill 插件 | 中（1 天） | 生态扩展 |

---

## 五、V2 架构变化

```
V1:  用户 → CLI → Agent Loop → RAG → 回答
                     ↓
                  护栏 + 幻觉检测

V2:  用户 → CLI → Agent Loop → RAG + 知识图谱 → 回答
                     ↓              ↓
                  护栏 + 幻觉检测   论证推理
                     ↓
         断点续研 + 跨项目联想 + 研究方向研判
                     ↓
                可配置模型 + 中文支持
```

---

## 六、V2 验收标准

1. `research-agent graph --paper "Attention Is All You Need"` 输出论证结构图
2. 中文查询"attention机制是什么" → 正确检索中文论文 → 引用溯源
3. Agent 检测到需要人工实验 → 自动标记 WAITING → 用户回来 → 恢复上下文
4. 两个项目之间有可迁移发现 → Agent 主动关联
5. 消融评估：E5(graph) > E4(agentic) 在 Recall 和 MRR 上
6. 所有新增模块通过 mock-LLM 确定性测试