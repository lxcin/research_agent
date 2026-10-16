# 任务正确性与循环策略评测

更新：2026-10-15。

## 评测对象

三个维度分别报告，不混成一个未经校准的总分：

| 维度 | 检查内容 | 入口 |
| --- | --- | --- |
| harness | 工具/资源生命周期、排空、构建与证据健康 | ToolRegistry.evaluate / EvaluationRegistry.run |
| task | 文件与结构化数据是否正确、保护内容是否保留、回答是否有来源证据 | TaskCase 的 task 检查器 |
| strategy | 同一任务集上不同循环的通过率、耗时、调用量及费用差值；也支持声明策略检查器 | run_benchmark |

正常退出、返回“已完成”、工具成功率高，都不直接判定任务正确。TaskCase 至少有一个 task 检查器；任一检查 fail 或非 completed 终止判为 fail，检查器异常判为 error，证据不足判为 skip。skip/error 均不算通过，通过率分母包含全部试验。

## 插件声明与生命周期

ToolPlugin.evaluation_cases 和 ToolSchema.evaluation_cases 接收 TaskCase 声明，随插件启用注册、停用撤销。插件级 owner 是 plugin_id，工具级为 plugin_id/tool_name；同一 owner 内用例 id 唯一。检查器无需伪装成 Agent 工具，标准答案和评测提示不注入模型工具描述。

EvaluationRegistry.cases() 返回声明快照。执行每个用例时获取所属插件的资源租约，持续到策略执行和检查器完成；停用等待已运行的试验，已撤销的旧快照不能重新调用其检查器。普通 EvaluationSpec 检查同样持有所属插件租约。注册表内部锁只保护登记/快照，不覆盖耗时检查；不要在评测回调里同步卸载自身。

TaskCase 包含 prompt、初始 files、reference、constraints、max_rounds、checks 和 version。策略仅接收 TaskRequest 与临时工作区，不接收 reference 或检查器。每次运行前复制声明，每个检查器得到独立证据副本，防止某个检查修改后续检查的输入。插件和检查器是可信代码，这不是对恶意 Python 插件的隔离。

## 内置任务集

当前 filesystem 插件声明四个可执行工作区任务：

| 用例 | 验收依据 |
| --- | --- |
| aggregate_csv | 按地区汇总 CSV，检查 totals.json 的实际键和值，并保护原 CSV |
| minimal_edit | 只修改 TIMEOUT，逐字节检查目标文件及人工笔记；样本使用宿主原生换行 |
| missing_source | 必须读取指定缺失文件并取得失败事件，只回答 MISSING，不能创建假文件 |
| follow_dependencies | 先读 manifest 指定的数据源，再写精确 JSON，原始文件保持不变 |

JSON 比较允许相等的整数/浮点数，拒绝把布尔值当数字，拒绝额外字段。文件检查限定在当前试验目录，限制读取大小并拒绝越界/符号链接逃逸。模型只声称完成而不产生正确结果时，四个用例都不会通过。

这是小型工作区验收集，不是通用智能基准，也不覆盖所有换行、二进制、长上下文或业务领域。增加领域任务应由能力插件声明自己的 fixture 和 oracle；检查器语义改变要提升 EvaluationSpec.version，用例语义改变要提升 TaskCase.version。

## 执行与对照

run_benchmark 为每个策略、任务和重复试验创建独立临时目录，写入相同初始文件；参考答案只在检查阶段使用。每隔一次重复反转策略执行顺序，减轻固定先后顺序影响；没有宣称随机化或统计显著性。配对按 owner/case/trial 进行，同一对输入一致。

runtime_strategy 复用生产 RuntimeHost、FunctionCallingRuntime、工具分发、约束与上下文预算，注入 AgentKernel。默认策略为 FunctionCallingKernel，另提供明确不调用工具的 DirectAnswerKernel 基线；两者使用同一配置模型和 filesystem 工具环境。默认不会运行 shell，不提供交互审批；filesystem 路径另有试验目录限制。它覆盖运行时执行层，未调用完整 run_agent 的项目装配和回合后提炼，不能据此声称整个应用端到端验收。

自定义 StrategySpec 可注入其他循环；需要领域工具时用 runtime_strategy 的 registry_factory 提供该能力的独立注册表。自定义工厂应保持模型、工具、预算和权限一致，或在 metadata 明确记录差异。评测目录是数据隔离，不是操作系统沙箱；自定义 Python 回调仍是可信代码。

```python
from research_agent.task_evaluation import run_benchmark, write_benchmark_report
from research_agent.evaluation_runtime import runtime_strategy

# registry 是已装配、持有领域用例的注册表。
strategy = runtime_strategy(my_kernel, provider_factory,
                            registry_factory=my_registry_factory,
                            metadata={"model": model_name})
report = run_benchmark(registry.evaluations.cases(), [strategy], repeats=3)
write_benchmark_report(report, "reports/tasks")
```

## 命令行与报告

```text
research-agent evaluation list
research-agent evaluation run --case filesystem/aggregate_csv --repeats 3 --output reports/comparison
research-agent evaluation run --strategy function_calling --output reports/acceptance
research-agent evaluation run --strategy-factory my_eval:create_strategy --output reports/custom
```

list 只列出启用插件/工具的用例，不调用模型。run 显式调用配置模型，可用 --model 覆盖；没有指定策略时比较 function_calling 和 direct_answer。默认策略只提供 filesystem 环境；领域用例需通过 --strategy-factory 注入自己的工具环境。扩展工厂协议为 create_strategy(model=...) -> StrategySpec，仅显式指定时导入模块，不扫描执行任意目录。

输出 JSON 和 Markdown，分别以临时文件原子替换；两份报告不构成跨文件事务。默认 --gate primary 要求首个策略全部通过，对照基线失败不会误触发门禁；--gate all 检查所有策略，--gate report-only 只生成报告。失败、跳过或错误均使被门禁检查的策略返回非零退出码；报告仍保留。

JSON 包含每个试验的用例版本/内容指纹、策略版本/metadata、环境、回答、终止原因、逐项检查结果及指标；默认 runtime_strategy 另记录模型调用设置、上下文配置和价格版本，不自动记录凭证。报告含任务输出，按用户数据管理。

汇总分别给出通过/失败/跳过/错误数量、正常退出数量、通过率，以及耗时 P50/P95、平均调用量、token 和费用的观测覆盖率。对照报告给出配对胜负、通过率差值和已知指标的候选减基线差值；缺失数据不补零、不参与对应指标配对。成本低不代表更正确，不自动选出“最佳内核”。

评测在 adapter 接缝观察 select 返回 usage 与 answer 用量事件；任一观察调用缺少用量，该项运行总量保留未知。model_calls 是适配器调用次数，不保证包括适配器内部的传输回退。费用可来自显式上报或本地 telemetry 价格估算，未知模型保留未知，不能视作供应商账单。调用失败且没有用量回执时无法准确计算已产生费用。

## 验证范围

2026-10-15 全量验收：495 passed / 1 skipped，8 条第三方依赖弃用警告；线程未处理异常按错误处理。评测专项 25 项通过（含原有注册表用例）。

回归通过脚本模型驱动真实 RuntimeHost 和文件工具，验证正确/错误产物、重复试验目录隔离、标准答案不传入策略、假完成拒绝、终止与正确性分离、缺失证据、检查异常、未知用量、插件排空与自定义 CLI 工厂。脚本模型只是传输测试替身；真实供应商运行、真实模型策略收益和长期统计结论需要单独执行上述命令取得报告，不由测试结果推断。
