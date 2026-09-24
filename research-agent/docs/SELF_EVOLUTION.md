# 自进化：项目经验沉淀 → 用户技能（v1）

> 目标：让 Agent 在项目开发中**沉淀可复用经验**，并在用户同意下晋升为**用户技能（skill）**。
> 代码：`src/research_agent/report.py`、`src/research_agent/evolve.py`、`src/research_agent/tools/builtin/evolve.py`
> 测试：`tests/test_evolve.py`（16 例）

## 0. 设计立场：先"买/组合"，最后才"造"

能力获取阶梯（`evolve.classify` 按此给建议）：

```
需要能力 →
  能用现有工具组合？        → reuse（直接用，不产生产物）
  有现成 MCP/服务？         → install_mcp（注册，不写代码）
  可复用流程/SOP？          → skill（本 v1 实现）
  确无现成 且 需确定性代码？  → plugin（本 v1 不做，见 §6）
  否则                     → keep（留在报告）
```

因此 v1 **只做 skill 沉淀**：便宜、安全、不执行代码。自写插件被有意推迟。

## 1. 两个能力（用户可启动）

### a) 项目经验报告（项目级）
- 载体：`{workspace}/.research-agent/report.json` + Markdown 渲染；
- 分区：`progress / decision / pitfall / procedure / tool_candidate`；
- 每条带 `id / source / status(open|promoted|dropped) / promoted_to`；
- Agent 工具：`record_experience`、`list_experience`。

### b) 用户技能沉淀（跨项目）
- 载体：`data_dir/skills/*.md`（YAML 头 + Markdown），由 `skill_loader` 与仓库 `skills/` **一起加载**，trigger 命中注入；
- **可开关**：每个技能 YAML 头带 `enabled: true/false`；禁用后 `skill_loader.matched_skills` 不再命中、不注入。切换：`research-agent evolve enable/disable <name>`（`evolve.set_skill_enabled`）。
- Agent 工具：`propose_skill`（**需要人工审批**）、`classify_experience`、`list_skills`（返回 enabled/version/used 状态）。

## 2. 全链路（每阶段可审计）

```
record_experience ──► report.json（分区条目）
      │  用户/Agent 选出可复用的一条
      ▼
classify_experience ──► {target, reason}   （build/buy 阶梯建议，用户可改）
      ▼
propose_skill
  ├─ build   生成 SKILL.md（name/description/triggers/body）
  ├─ review  静态校验（见 §4）
  ├─ approve 宿主 HITL：requires_approval → confirm_required（60s 超时默认拒绝）
  ├─ write   写入 data_dir/skills/<slug>.md；用 skill_loader 回解析验证，失败回滚
  └─ record  追加 provenance 到 data_dir/evolution/records.jsonl
      ▼
下次会话 build_context 时按 trigger 注入技能
```

## 3. 全链路追踪 / 治理

- **provenance 记录**（`data_dir/evolution/records.jsonl`）：`id / entry_id / target / status(applied|rejected) / artifact / review / reason / capabilities / approval / ts`；
- **人工闸门**：`propose_skill` 的 schema `requires_approval=True`，宿主 `_tool_approval_hook` 对**任意**该字段的工具统一走 HITL（未确认即拒绝，代码级拦截）；
- **反向定位**：产物文件 → `records.jsonl` → `entry_id` → `report.json` 条目。

## 4. 生成质量校验（本 v1 已有：静态 + 人工）

| 层 | 校验 |
|---|---|
| 静态（`evolve.review_skill`） | 体积上限（8000 字符）、注入标记（ignore previous / 忽略以上 / system prompt…）、YAML 头存在、**重名拒绝**、与现有技能**近似去重**（SequenceMatcher > 0.85） |
| 回环 | 写盘后用 `skill_loader.parse_skill_file` 真解析，失败即删除回滚 |
| 人工 | HITL 审批 + 默认不覆盖 |
| 记录 | 通过/拒绝都留 record，拒绝原因可查 |

> 语义层（LLM 打分）与动态层（执行）在 v1 不涉及（skill 是文本，无执行）；`plugin` 晋升时再加（见 §6）。

## 5. 查询与报告（"全链路检测评估报告质量查询"）

```bash
research-agent evolve list [--target skill] [--status applied] [--text ...]   # 记录查询
research-agent evolve report [--out DIR]                                      # 生成自进化报告(md)
research-agent evolve skills                                                  # 列出用户技能
research-agent evolve experience [--section procedure] [--workspace WS]       # 打印项目经验报告
research-agent evolve usage                                                  # 使用次数 + 效用 A/B + 未使用技能
research-agent evolve enable|disable <name>                                   # 开关某个用户技能
```
- `records.jsonl` = 全链路审计源；`evolve report` 汇总记录/状态/目标分布 + 用户技能清单。

## 6. MCP 接入（"买"路径，已具备）

外部能力优先走 MCP，而不是自研插件：
- 配置 `skills/mcp.yml` 的 `servers:`，`run_agent` 启动时由 `mcp_loader` 连接、注册为 `mcp_*` 工具（`plugin_id=mcp`）；
- 可用 `RESEARCH_AGENT_MCP=0` 关闭；`mcp` 插件可在 `plugin disable mcp` 停用；
- 例（免 key 的 Exa 语义搜索 MCP，需本机能访问对应服务）：
  ```yaml
  servers:
    - command: ["npx", "-y", "mcporter", "run", "exa"]
  ```
- 检索/抓取已由 `web` 插件覆盖（Exa/Tavily/Serper/DDG + direct/Jina）；**能买到的（搜索、读网页、平台数据）不要自研**。

## 7. 安全边界与已知限制

- skill 只注入上下文，**不执行代码**；写文件限定 `data_dir/skills`，名字经 `_slug` 清洗；
- 未审批不写入；默认不覆盖；注入标记会被静态拦下；
- 来源应为项目对话蒸馏（避免把网页原文直接升成技能）；
- **v1 限制**：不做 plugin 自写、不做语义打分、不做 canary/使用率统计——这些在后续阶段（能力声明 + MCP 子进程 + 四层校验）落地。

## 8. 版本强化 + 评估闭环

**版本强化**（`evolve.write_skill`）：
- 同名 skill = **更新**（不再拒绝）：`version` 递增（写入 YAML 头），records 记 `parent` 指向上一 applied 记录，形成版本链；
- 不同名但正文近似（SequenceMatcher > 0.85）仍拒绝（防重复）；
- 更新时 `review_skill(allow_existing=True)` 跳过"已存在"，仍校验注入/体积/近似。

**评估闭环**：
- **使用追踪**：`context.build_context` 注入技能时，对命中的每个 skill 调 `evolve.note_skill_used(name, trace)`（`skill_loader.matched_skills` 提供命中项）；落 `data_dir/evolution/usage.jsonl`；
- **效用 A/B**：`evolve.utility_report()` 按 trace 连接 `telemetry.runs`，比较"用过该 skill 的运行" vs "其余运行"的 token/费用/时延/完成率；
- **淘汰**：`evolve.prune()` 列出零/低使用技能；`evolve report` 的用户技能段显示 `used N`。
- CLI：`research-agent evolve usage` 一览使用次数 + A/B + 待淘汰项。

> 设计意图：skill/plugin 的价值最终由"是否让框架更有效"（失败率↓、轮数↓、完成率↑、成本↓）来判定，
> 而不是"看着像不像"——数据来自 `telemetry` 与 `audit`。

## 9. 后续阶段（未做）

- **P1** 语义/动态校验、相似合并、数量上限；
- **P2** 能力声明（capabilities）+ 最小权限授予；
- **P3** 自写插件（默认关闭）：生成物 = **MCP server 子进程**，宿主用 `ToolPlugin` 包装，`mcp_loader` 注册；
- **P4** 使用率/故障观测、canary、自动回滚，并把"产物合法率/无越权审批"接进 `research-agent quality` 阻断门禁。
