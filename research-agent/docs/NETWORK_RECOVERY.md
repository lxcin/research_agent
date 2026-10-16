# 网络中断与执行恢复

更新：2026-10-15。默认 FunctionCallingKernel 的步骤恢复已接入宿主、CLI 和 API。

2026-10-16 补齐：执行段快照交接可崩溃恢复；取消状态持久化；工具网络失败作为观察结果交给 Agent，而不是把会话误标为网络挂起。

## 工具失败与模型断网

工具能确认失败时，返回 success=false，以及 error_code=network_interrupted、outcome=failed、retryable=true；错误文本明确写出“网络中断”。该失败结果也进入调用日志的 completed 记录——表示本次调用已得到确定结果，不表示工具成功。默认循环将它加入工具上下文，Agent 可以换工具、调整方案或用新调用标识重试，不直接终止整个任务。同一调用标识的恢复只复用该失败结果，不偷偷重新发请求。

只读工具声明 side_effect=False，传输异常可直接转换为上述失败；有副作用的插件只有确认操作已失败、没有不确定结果时，才使用 ToolNetworkError(..., confirmed_failed=True) 或返回明确的失败结果。仅收到超时/连接断开不能证明远端未执行；此时调用仍保持 started，需核对，不把未知结果伪装成可盲重试的失败。插件负责真实声明副作用和失败证据。

内置 web_fetch / web_search 保留已有后端回退，全部失败后返回错误观察；传输故障会带上述分类，鉴权/策略/内容错误不冒充网络中断。模型自身无法连接时，没有模型可以消费错误观察，仍使用下述 waiting_network 和恢复机制。

## 从哪里继续

文件快照 S/E 用于撤销；executions.db 中的执行检查点用于续跑，二者不是同一个恢复动作。网络中断不自动回退文件，也不重新运行整轮。

| 中断位置 | 继续方式 |
| --- | --- |
| 模型选动作，尚未拿到完整响应 | 用保存的上下文重发这次模型请求；不重复增加该轮 round，不执行半截参数 |
| 一组工具执行期间 | 保留 pending 意图；completed 工具复用结果及状态，不重新执行/重复审阅；started 结果未知仍需核对 |
| 工具结果已入库，循环游标尚未更新就崩溃 | 利用工具完成时固定的工作区摘要核对现场，复用结果，再推进游标 |
| 生成最终答复时流中断 | 只重发答复阶段；不重新选工具。已有部分文本保留，重新生成前发送 reply_reset，让客户端替换旧前缀 |
| 最终答复已持久化 | 复用最终结果；原有收尾幂等键继续防止重复写入 |

例如：读取文件 → 修改文件 → 下一次模型请求断网。恢复从这次模型请求继续，先前读写结果已在上下文中，修改不再执行。若生成答复时断网，则从答复阶段继续，不回到修改前。

阶段保存为 next / select / tools / answer；messages、pending、rounds、failures、重复调用保护、原始轮次上限、可持久化 state/caps 和宿主上下文预算信息一并保存。恢复不能通过换一个 max_rounds 重置原上限。字段缺失、插件迁移不兼容、请求内容/工作区/聊天/kernel 身份不同均保持拒绝。

## 重试、挂起和入口

模型宿主先使用已有有限退避重试。408 / 429 / 部分 5xx 和连接/超时错误可重试；401 等永久错误直接报告 model_error。退避支持取消，网络故障不累计为内核思考失败并转入兜底作答。

重试耗尽或已发布的答复流断开时，保存 waiting_network；无额外审阅，也不把半截答复当成最终完成。API 与交互 CLI 使用 run_agent_resilient，在原 execution_id 下额外等待 2 秒、5 秒并最多续跑两次。每次模型调用仍受原有限传输重试约束；持续离线后返回保存状态，不在后台无限重试。应用重启后不会自行唤醒所有旧任务。

```text
research-agent execution inspect <execution_id>
research-agent execution resume <execution_id>
```

API：GET /api/executions/{execution_id} 查询 status / phase / rounds；再次 POST /api/chat，携带相同 execution_id、message、workspace_dir、chat_id 即可恢复。凭证仍由当次配置提供，不持久化 API Key。

等待自动恢复期间取消，会在 executions.db 事务中把状态写为 cancelled，保留步骤及上下文；CLI/API 查询与本次返回一致，不再残留 waiting_network。取消不删除检查点，用户日后仍可显式恢复；已完成记录不会被取消操作覆盖。

SSE 增加 execution_suspended、network_wait、execution_resumed、reply_reset 事件。waiting_network 是非错误的保存状态；客户端若消费 reply 片段，必须在 reply_reset 时替换已有部分文本，不能继续拼接。答复重发可能产生额外模型费用，不承诺 token 级续传或供应商请求恰好执行一次。

## 工作区和资源

挂起只释放本次前台资源，不回滚文件。快照 outbox 标记 suspended，不封存临时 E；未变化的恢复继续使用原 S，任务真正完成时再记录完整 E。因此原轮在断网前后的修改仍可统一撤销。后台工具继续遵守其独立凭证、排空及隔离规则。

恢复时先验证原 S 仍为当前轮。如果人工在等待期间改变文件、HEAD 或暂存区，先保存当前工作区作为新执行段基线，并把变化通知交给模型重新读取、规划；历史工具结果保留为历史证据，不使用旧快照覆盖当前文件。此时最近一轮撤销只涵盖新执行段。读取/写入之间的外部编辑仍受既有乐观并发限制，本批不增加文件事务。

如果变化时仍有未完成工具意图，不直接执行旧写入计划；未知结果需核对。若已开始别的新任务并退休旧 S，旧任务恢复在模型调用前拒绝，不自动复活旧快照或覆盖新任务。applying / partial 撤销、后台残留写入和工作区隔离仍然阻断恢复。

执行段切换使用小型持久化交接记录 snapshot-transition：先固定新 S 和旧/新归属意图，再提交执行日志的新快照指针，随后发布 active-turn、退休旧引用及消息，最后清除交接记录。接入时持有原工作区凭证核对日志：指针未提交则保留旧 S；已提交则幂等完成新归属和清理。Git / reviews.db / executions.db 仍不是一个事务，但每个交接边界均有重放依据，不再先删除原 S 再更新执行位置。

## 责任和剩余范围

- kernel 只描述循环阶段和可恢复的数据，不包含网络 SDK、文件快照或重试策略。
- runtime / model_retry 负责模型重试、流完成边界、取消和答复替换通知。
- network_recovery 负责宿主接入、挂起记录、工作区核对和执行段基线。
- execution / 工具层负责 completed 结果复用、契约匹配与未知副作用拒绝。
- checkpoint / outbox 负责文件撤销证据及资源边界，不充当任务调度器。

本批没有新增任意 Python 栈恢复、无限后台联网调度、通用只读/幂等工具结果探测器、未知远程副作用自动补偿。替代 kernel 需要自行提供可持久化步骤检查点；没有检查点时报告不支持续跑，不从头静默重做。

tests/test_network_recovery.py 覆盖阶段恢复、一次写入、流式替换、人工修改、旧任务失效、预算和约束保持、取消、真实进程崩溃及本机 HTTP 429/503/断开连接。测试仅使用临时仓库、本机模拟服务和测试凭证，不调用外部模型。

2026-10-15 最终验收：全量 **689 passed / 8 skipped / 9 个第三方警告，469.28 秒**，线程未处理异常按错误处理；最终网络专项 **18 passed**；另行启用真实 Docker **7 passed**。证据分别为 artifacts/network-recovery-final.xml、artifacts/network-recovery-focused-final.xml、artifacts/network-recovery-docker.xml。

2026-10-16 补齐验收：全量 **700 passed / 8 skipped / 9 个第三方警告，460.08 秒**，专项（网络恢复与内置网络工具）**101 passed**，真实 Docker **7 passed**。另补交接恢复不得清除部分撤销证据的测试，并与取消/副作用边界组成 **4 passed** 保护专项；该新增一例未计入前述 700 项。报告为 artifacts/recovery-handoff-acceptance.xml、artifacts/recovery-handoff-focused.xml、artifacts/recovery-handoff-docker.xml、artifacts/recovery-handoff-guards.xml。真实进程分别在新指针提交前、提交后、归属发布后及旧引用退休后退出，均可恢复且保留人工内容；还覆盖指针写入失败保留旧快照、等待时取消落盘、工具失败回灌与未知副作用拒绝。
