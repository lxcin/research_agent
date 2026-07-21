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

**概述**：从"检索段落"升级到"理解论证结构"。知识图谱分为两层：全局图谱和单篇论文图谱。

#### G1. 全局知识图谱（自动维护）

**触发**：每篇论文摄入时自动抽取 Claim 并加入全局图。无需用户干预。

**结构**：

```
┌─────────────────────────────────────────────────────────┐
│                    全局知识图谱                           │
│                                                         │
│   Paper A: Attention Is All You Need                     │
│   ├── Claim: "Scaled dot-product attention is faster"   │
│   │   ├── Evidence: 实验对比 RNN/CNN                      │
│   │   └── supports → Paper B: BERT 也用了                │
│   ├── Claim: "Multi-head attention improves diversity"  │
│   │   └── contradicts → Paper C: 单头更高效 (特定场景)      │
│                                                         │
│   Paper B: BERT: Pre-training of Deep Bidirectional      │
│   ├── Claim: "MLM enables bidirectional context"        │
│   │   └── extends → Paper A: 单向→双向 attention          │
│   └── Claim: "NSP improves sentence-level tasks"        │
│                                                         │
│   Paper C: Efficient Attention: Is One Head Enough?      │
│   └── Claim: "Single head sufficient for short text"    │
│       └── contradicts → Paper A: multi-head claim        │
└─────────────────────────────────────────────────────────┘
```

**数据模型**：

```python
@dataclass
class Claim:
    id: str
    paper_id: str          # 所属论文
    text: str              # 主张内容
    type: str              # claim / evidence / method / result
    confidence: float      # LLM 抽取置信度

@dataclass
class Relation:
    source_claim_id: str
    target_claim_id: str
    relation_type: str     # supports / contradicts / extends / cites
    paper_id_a: str        # 跨论文关联时用
    paper_id_b: str
```

**自动构建流程**（论文摄入时触发）：

```
论文摄入
  │
  ├── 1. 文本分块 → Chroma（已有）
  │
  ├── 2. LLM 抽取 Claim（新增）
  │     "从这篇论文中提取关键主张，每条包含：主张内容、类型、支持证据"
  │     → [{text: "...", type: "claim", evidence: "..."}, ...]
  │
  ├── 3. 确定性校验（新增）
  │     - Claim 是否在原文中可定位？（正则回溯）
  │     - Claim 是否与已有 Claim 重复？（向量相似度 > 0.9 → 合并）
  │
  ├── 4. 加入全局图（新增）
  │     - 新 Claim → 新节点
  │     - 与已有 Claim 的关系 → LLM 判断 + 确定性校验
  │
  └── 5. 嵌入 Claim 节点（新增）
        - 每个 Claim 存 Chroma，供后续图检索
```

#### G2. 单篇论文知识图谱（用户显式触发）

**触发条件**（满足任一）：
- 用户说 "分析这篇论文的论证结构" / "梳理一下这篇文章的论点"
- 用户讨论完一篇论文后说 "总结一下它的论证逻辑"

**输出格式**：

```
Attention Is All You Need — 论证结构

核心论点:
  Transformer 模型完全基于 attention 机制，无需 RNN/CNN

├── 子论点 1: Scaled dot-product attention 计算效率高
│   ├── 证据: 与 RNN 对比，训练时间从 3.5 天降到 12 小时
│   └── 前提: 序列长度 < 模型维度时，attention 比 RNN 快

├── 子论点 2: Multi-head attention 捕捉多角度信息
│   ├── 证据: 消融实验，8 头 > 1 头，BLEU 提升 0.5
│   └── 前提: 不同 head 学习不同的 attention pattern

├── 子论点 3: 位置编码替代序列结构
│   ├── 证据: 对比 learned vs sinusoidal，效果相当
│   └── 前提: 位置信息对序列建模是必要的

└── 实验验证: WMT 2014 EN-DE 28.4 BLEU，SOTA
    ├── 对比: 之前的 SOTA 26.0 BLEU
    └── 局限: 长序列（>512 token）退化为 O(n²)
```

**与 G1 的关系**：G2 是 G1 的子集放大。G1 自动维护 Claim 节点，G2 在用户要求时把一篇论文的 Claim 组织成树状论证结构。

#### G3. 图增强检索

**检索流程**：

```
用户: "attention 机制和 RNN 哪个更快？"
  │
  ├── 1. 文本检索（已有）→ chunk: "attention is faster than RNN"
  │
  ├── 2. 图检索（新增）
  │     通过 chunk 的 paper_id → 查全局图 → 获取关联 Claim
  │     → Attention Is All You Need 的 Claim: "训练时间从 3.5 天降到 12 小时"
  │     → BERT 的 Claim: "双向 attention 比单向慢 2x 但效果好"
  │
  ├── 3. 矛盾检测（新增）
  │     → Efficient Attention 的 Claim: "单头在短文本上更快"
  │     → 标记为 "存在争议"
  │
  └── 4. 生成回答
       "Attention 比 RNN 快（训练 12h vs 3.5 天），但存在争议：
        有论文认为单头 attention 在短文本上更快..."
```

#### G4. 可视化（P2，后续前端 UI）

**短期（终端）**：

```bash
# 全局图谱概览
$ research-agent graph
  ┌─ Attention Is All You Need (2017) ─── extends ─── BERT (2019)
  │       │                                              │
  │       ├── supports ── Efficient Attention (2020) ──┘
  │       │
  │       └── contradicts ── Sparse Transformer (2019)

# 单篇论文论证结构
$ research-agent graph --paper "Attention Is All You Need"
  (输出 ASCII 树状图)

# 跨论文关系
$ research-agent graph --relation "Attention Is All You Need" "BERT"
  extends: BERT 的 MLM 将单向 attention 扩展为双向
```

**长期（前端 UI）**：简单 Web 界面（FastAPI + 前端），思维导图风格可视化。

#### 技术选型

| 组件 | 技术 |
|------|------|
| 图存储 | `networkx` + SQLite（节点和边存关系表） |
| Claim 嵌入 | Chroma（复用现有向量库） |
| LLM 抽取 | `LiteLLMProvider`（复用现有抽象） |
| 矛盾检测 | 向量相似度 + LLM 判断 + 确定性校验 |
| 可视化 | 终端 ASCII → 后续 FastAPI + D3.js |

#### V1 数据兼容

`Paper` 表 + `Chunk` 表不变，新增：

```sql
CREATE TABLE claims (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL,
    text TEXT NOT NULL,
    type TEXT NOT NULL,       -- claim / evidence / method / result
    confidence REAL DEFAULT 0.0,
    embedding_id TEXT         -- Chroma 中的嵌入 ID
);

CREATE TABLE relations (
    id TEXT PRIMARY KEY,
    source_claim_id TEXT NOT NULL,
    target_claim_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,  -- supports / contradicts / extends / cites
    source_paper_id TEXT,
    target_paper_id TEXT
);
```

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

### 模块 O：前端 UI 可视化（P2）

**概述**：为知识图谱和项目计划提供简单 Web 界面。

**技术**：FastAPI + 简单前端（D3.js 或 Cytoscape.js）。

**页面**：

| 页面 | 内容 |
|------|------|
| 全局图谱 | 思维导图风格，展示所有论文的 Claim 节点和关系边 |
| 单篇论文 | 点击论文 → 展开论证树（Claim → Evidence → Premise） |
| 项目仪表盘 | 所有项目状态、等待事项、进度时间线 |
| 研究计划 | 步骤列表 + 依赖关系图 + 责任人标记 |

**第一版（终端）**：`research-agent graph` 输出 ASCII 图。
**第二版（Web）**：`research-agent serve` 启动本地 Web 服务。

---

## 四、优先级总结

| 优先级 | 模块 | 预计工作量 | 价值 |
|--------|------|-----------|------|
| **P0** | G. 知识图谱 | 大（3-4 天） | 核心差异化，全局自动 + 单篇按需 |
| **P0** | H. 中文支持 | 小（半天） | 目标用户刚需 |
| **P0** | I. 断点续研 | 小（半天） | 已有基础设施，串起来即可 |
| **P1** | J. 跨项目联想 | 中（1 天） | 知识图谱的自然延伸 |
| **P1** | K. 研究方向研判 | 中（1 天） | 未实现的 US-4 |
| **P1** | L. 可配置模型 | 小（半天） | 开发和部署都受益 |
| **P2** | M. 评估增强 | 中（1 天） | 图评估需要 G 先完成 |
| **P2** | N. Skill 插件 | 中（1 天） | 生态扩展 |
| **P2** | O. 前端 UI 可视化 | 大（2-3 天） | 知识图谱 + 项目仪表盘 |

---

## 五、V2 架构变化

```
V1:  用户 → CLI → Agent Loop → RAG → 回答
                     ↓
                  护栏 + 幻觉检测

V2:  用户 → CLI / Web UI → Agent Loop → RAG + 知识图谱 → 回答
                            ↓              ↓
                         护栏 + 幻觉检测   论证推理
                            ↓              ↓
                    断点续研 + 跨项目联想   全局图谱（自动）
                            ↓              ↓
                    研究方向研判         单篇图谱（按需）
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