# 运行时评测（telemetry 插件）

> 目标：在**真实运行**过程中采集评测数据（token/费用/时延/工具路径/任务规划与完成的成本），
> 做评分与问题分析。**评测不进入 agent 循环**——它是运行时观察层。
> 代码：`research_agent/telemetry.py`、`tools/builtin/telemetry.py` · 测试：`tests/test_telemetry.py`

## 1. 设计原则：评测 = AgentRuntime 观察层，不动循环

评测被实现为一个 **AgentRuntime 装饰器** `MeteredRuntime`，包住基础内核
(`FunctionCallingRuntime`)，宿主在 telemetry 插件开启时选用它：

```
run_agent
  └─ runtime = MeteredRuntime(FunctionCallingRuntime())   # telemetry 开
       ├─ 包装 ctx.emit          → 观察 tool_start/end、fault、llm_usage（旁路）
       ├─ 包装 ctx.call_llm_with_tools → 读附加的 usage 字段（不改循环）
       └─ base.run(ctx)          → 执行原内核（零改动）
```

- **内核循环完全不变**：不 import telemetry、不做 `is_plugin_enabled` 判断；
- **采集靠两处既有接缝**：`emit(type,data)` 事件通道 + 注入的 LLM 原语；
- LLM 原语只**附加**返回 `usage/model` 字段（循环忽略）；流式答复经 `emit("llm_usage", …)` 旁路出来。

## 2. 采集什么

| 维度 | 字段 | 来源 |
|---|---|---|
| LLM 用量 | model、prompt/completion tokens、latency_ms、cost_usd、purpose | litellm `resp.usage` |
| 工具 | name、duration_ms、status | `tool_start`/`tool_end`（按 id 关联） |
| 回合 | rounds（= tool_select 次数） | LLM 调用 purpose 统计 |
| 阶段 | planning（选工具）/ execution（跑工具）/ synthesis（最终答复）| 按 purpose 归并耗时 |
| 运行总计 | tokens、cost_usd、wall_ms、path、faults、completed | 运行末汇总 |
| 关联 | trace、workspace、chat | 宿主 |

每次运行落一条 `data_dir/usage/runs.jsonl`。

## 3. 指标意义

| 指标 | 意义 | 方向 |
|---|---|---|
| token/次、token/任务 | 上下文与输出的规模 | ↓（同等完成度） |
| cost_usd/次、按阶段费用 | **钱花在哪**（规划 vs 答复 vs 蒸馏） | ↓ |
| wall_ms、llm/tool latency | **时间花在哪**、瓶颈 | ↓ |
| rounds、path | 工具调度路径与规划效率 | ↓ |
| p95 | 尾部异常（个别超贵/超慢的会话语义） | ↓ |
| completed | 是否产出可用答复 | ↑ |

> 费用用**本地价格表**估算（`telemetry.prices` 可覆盖）；未知模型记 0 且 `cost_known=false`，报告会标注"费用偏低"。

## 4. 问题分析（可扩展）
- token/cost 异常值（>P95）→ 上下文膨胀/循环/重试；
- 阶段失衡（planning 占比过高）→ 规划低效；
- 慢工具/慢调用 Top → 工具或模型瓶颈；
- 路径重复/失败重试 → 与 `diagnostics/monitor` 的 fault 对齐。

## 5. 用法

```bash
research-agent plugin list            # telemetry 默认启用
# 运行时自动采集；Agent 可用工具自查：
#   usage_report(limit)     → 聚合 token/费用/时延/工具/路径
#   usage_query(trace_id)   → 单次运行明细
```
报告：`data_dir/usage/report-{ts}.md/.json`（`telemetry.write_report()`）。

## 6. 与 quality / audit / diagnose 的关系

| | telemetry | audit | quality | diagnose |
|---|---|---|---|---|
| 对象 | **运行成本/时延/路径** | 框架有效性评分 | 构建期质量 | 运行故障 |
| 数据 | runs.jsonl（用量） | logs/*.jsonl（事件） | pytest+coverage | logs/*.jsonl |
| 输出 | 费用/时延/路径报告 | 维度分+追踪链 | 门禁 PASS/FAIL | 故障清单 |

## 7. 开关、价格留痕、评分与看板

- **记忆蒸馏可关**：`memory.distill: false` 关闭回合后蒸馏（省 LLM 花费），读取路径不受影响；配置见 `config.get_memory_config()`。
- **缓存留痕 + 折扣计价**：记录 `cached_tokens` 与 `cache_hit_rate`（`prompt_tokens_details.cached_tokens` / `prompt_cache_hit_tokens`）；**命中 token 按缓存价计费**（`prices` 第三项，缺省等于 input），provider 未上报时为 `None`、显示 `n/a（未上报）`。
- **上下文增长指标**：`max_prompt_tokens`（单轮最大 prompt）与 `tokens_per_round`，用于诊断"轮次↑→累计 prompt 超线性↑→成本↑"。
- **工具成功按结果判定**：`tool_end` 的 `output.success=false` 或非零 `returncode`（如 shell 命令失败）计为**失败**，而不只是"未抛异常"。
- **回合后归因（post 桶）**：`LiteLLMProvider.complete` 带 `purpose`（`extract/judge/compress`）；run 结束后的调用（记忆蒸馏/上下文压缩，蒸馏为异步）写入 `usage/post.jsonl`，`report()` 计入**总花费**并按 purpose 归因。
- **rounds 语义**：`rounds` = **模型轮次**（以 tool-select 的 LLM 调用数近似），文档与报告统一表述。
- **价格表留痕**：`data_dir/usage/prices.json` 保存生效价格表 + 版本哈希；每条运行记录含 `prices_version`，费用可审计、可比对。
- **接入评分**：`audit` 新增**经济性**维度（由 telemetry 的 avg 成本 / P95 时延 / avg token 计算，无数据时跳过），并以 15% 权重并入框架总分。
- **接入看板**：`quality` HTML 看板新增 KPI「累计费用」、卡片「运行时评测（成本/时延）」、趋势「累计费用」、历史表「费用」列。

## 8. 边界与后续
- 价格表为快照，`telemetry.prices` 覆盖；
- 后续：同任务基线 P50/P95 对比与阈值告警；把经济性拆成独立门禁（只告警）。
