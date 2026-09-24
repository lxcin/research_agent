# 内部质量门禁与看板（Quality Gate & Dashboard）

> 目标：把"项目内部质量"变成**一条命令、可判定、可视化、可进 CI** 的东西。
> 代码：`src/research_agent/quality/` · 测试：`tests/test_quality_gate.py`

## 1. 一条命令

```bash
# 评估并生成看板（默认写到 data_dir/quality/）
PYTHONPATH=src python -m research_agent.quality.gate --workdir . --tests tests/

# 或走 CLI
research-agent quality              # 生成看板
research-agent quality --open       # 生成后浏览器打开
research-agent quality --no-report  # 只判定，不写产物
```

退出码：门禁通过 `0`，失败 `1`（可直接作为 CI 的 gate）。

## 2. 采集（`collect.py`）
一次 `pytest` 子进程同时产出：
- **JUnit XML**（`--junitxml`，pytest 内置）→ 每个用例的 pass/fail/skip，按文件聚合；
- **coverage JSON**（`pytest-cov`，可选）→ 每个模块覆盖率。

无网络、无 API key；`pytest-cov` 缺失时覆盖率检查标记为"未采集（跳过）"，不影响其它门禁。

## 3. 门禁判据（`gate.py`，均为**下限**而非炫耀指标）

| 检查 | 类别 | 阈值 |
|------|------|------|
| 测试失败数 | correctness | `= 0` |
| 测试通过数 | correctness | `≥ 250` |
| 覆盖率 `tools/builtin/network.py` | coverage | `≥ 90%` |
| 覆盖率 `guardrail.py` | coverage | `≥ 50%` |
| 覆盖率 `tools/__init__.py` | coverage | `≥ 60%` |
| 安全用例通过率 | security | `= 100%` 且用例数 `≥ 8` |

安全子集 = `tests/test_network_tools.py` 中名称含
`ssrf / block / validate / scheme / redirect / private / loopback / metadata / mapped / 6to4 / nat64` 的用例——
即 **SSRF / URL 策略 / 重定向** 的确定性回归，必须全绿。

> 阈值刻意设在当前水位之下（当前 network 99.6% / guardrail 54.8% / tools 64.9%），作为"不许退化"的底线；要收紧就改 `DEFAULT_THRESHOLDS`。

## 4. 产物（`report.py`）
写到 `data_dir/quality/`：

| 文件 | 用途 |
|------|------|
| `quality-{ts}.json` | 机器可读，供 API / 趋势 / 归档 |
| `quality-latest.html` | **自包含看板**（内联 CSS+SVG，零依赖、离线可开） |
| `quality-{ts}.html` | 当次快照 |

看板内容：整体 PASS/FAIL、KPI（门禁/测试/失败/跳过/总覆盖率）、逐项门禁表、关键模块覆盖率条、**通过数与覆盖率的趋势 sparkline**、历史运行表。

## 5. CI 集成（`.github/workflows/ci.yml`）
`backend` job 在跑完测试后执行 `research-agent.quality.gate`：
- 门禁失败 → 步骤非零退出 → **CI 红**；
- 看板 HTML 通过 `actions/upload-artifact` 上传为 `quality-dashboard`，可直接下载查看。

## 6. 为什么是"内部质量"
- **确定性、离线**：全部基于本仓库测试与覆盖率，不依赖外部服务、可复现；
- **防退化**：把关键模块覆盖率与安全回归设成硬阈值，改动导致退化会立刻被 gate 拦下；
- **可观测**：JSON 存档 + HTML 看板 + 趋势，让"质量"从感觉变成曲线；
- **可扩展**：`collect.py` 解析器与 `gate.py` 的 `evaluate(metrics)` 解耦，新增检查只需加一条 `_check`（如记忆 R@5、e2e 任务成功率）。

## 7. 与 `diagnose` 的区别
| | `research-agent quality` | `research-agent diagnose` |
|---|---|---|
| 对象 | **构建/测试/覆盖/安全**（开发期质量） | **运行时会话故障**（事件流/监控） |
| 数据 | pytest + coverage | `data_dir/logs/*.jsonl` 事件流 |
| 判定 | 阈值门禁（退出码） | 报告聚合（人工审阅） |
| 用途 | CI 卡口、防退化 | 线上/使用中排障 |

## 8. 运行时全链路审计（`research-agent audit`）

`quality` 评的是**构建期**质量；`audit` 评的是**运行时框架有效性**（不是模型质量）。

- 数据源：`data_dir/logs/*.jsonl` 事件流（`EventRecorder` 产出）；
- 全链路：**追踪**（重建会话执行链）→ **审查**（标记反模式）→ **评分**（维度分）→ **报告**（md/json）；
- 维度（0–100，加权总分）：工具健康 0.30 / 收敛循环 0.25 / 任务完成 0.25 / 效率 0.10 / 稳定性 0.10；等级 A≥90 ≥B75 ≥C60 否则 D；
- 审查的反模式：`tool_loop`（同名同参重复）、`error_streak`、`empty_streak`、`search_exhausted`、`llm_unstable`、`no_response`、冗余调用、无最终回复、调用过多；
- 纯代码、离线、可确定性单测（喂合成事件即可）。

```bash
research-agent audit --report        # 生成 data_dir/audit/audit-{ts}.md/.json
```

| | `quality` | `audit` | `diagnose` |
|---|---|---|---|
| 对象 | 构建/测试/覆盖/安全 | **运行时框架有效性** | 运行时会话故障 |
| 数据 | pytest+coverage | 事件流（评分卡） | 事件流（故障聚合） |
| 输出 | 门禁 PASS/FAIL + 看板 | **总分+维度分+追踪链+建议** | 故障清单 |
| 用途 | CI 卡口 | 框架是否聪明/收敛/完成 | 排障 |
