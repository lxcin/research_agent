# 工具与 State 契约收敛

## 工具

2026-10-16：新增可选 context_call / context_result / history_result 投影端口，完整调用和结果独立存储，已完成历史只保留必要摘要。文件工具声明实现前读取当前实现的 usage；编辑回执区分文件修改完成和测试通过，带变更行范围与内容摘要。见[上下文与回执契约](TOOL_CONTEXT_AND_EDIT_RECEIPTS.md)。

参数事实源为 ToolSchema.parameters。注册时按 Draft 2020-12 检查定义，拒绝远程 $ref/$dynamicRef；模型调用与直接 registry.dispatch 共用 schema 校验，必填、类型、嵌套数组、枚举、范围及 additionalProperties 均按声明执行。不再维护工具名到必填参数的静态表。format 未启用额外检查器，不应当作网络地址安全策略；路径/URL 权限仍由工具和治理处理。

空字符串、空数组等限制由具体工具声明，不从 required 自动推导；文件路径、shell 命令和子代理任务数量已增加声明。未知额外参数只有在 additionalProperties=false 时拒绝；不存在隐式全局限制。

ToolSchema.usage 属于能力层，随工具定义/能力描述注入。基础提示词已移除具体工具规则。ToolResult 校验类型，handler 必须返回 ToolResult。ToolSchema.version 与参数 schema 的哈希进入执行日志签名；恢复时契约变化拒绝复用。handler 语义改变时维护者必须提升 version，哈希不分析 Python 函数实现。

2026-10-16：工具确认的网络失败使用 ToolResult.network_error，包含 network_interrupted / outcome=failed / retryable=true，并作为失败观察回灌循环。只读工具的传输异常可由注册表转换；有副作用的工具须自行确认失败，或显式抛 ToolNetworkError(..., confirmed_failed=True)。未知副作用不能冒充确定失败，仍由 RecoveryRequired 阻断。完成日志 completed 表示“有确定结果”，可包含失败的 ToolResult；复用不会自动重发同一调用。模型请求断网的挂起机制与工具失败分开，见[网络恢复](NETWORK_RECOVERY.md)。

## State

StateComponent 声明 owner、lifetime、value_type、validate、version 和成对的 encode/decode。初始化、set、恢复和保存均检查值；快照保存 owner/lifetime，恢复不允许悄悄转移归属。旧快照缺 metadata 继续按当前声明兼容读取，不将此称为可验证的历史归属。

插件注入自动绑定 owner，声明其他插件 owner 会被拒绝。默认兼容字段归为 core，并补类型与请求/会话生命周期元数据。宿主在任务入口 reset 插件 request/task 字段，保留 session/application；核心输入和附件由宿主单独组织，不在此重置。lifetime 不隐式创建全局单例或任务调度器。

推荐通过 state.components.view(plugin_id) 读取和更新：get 返回副本，set 校验归属与类型，update 在同一容器锁下复制—变换—校验—替换。变换/校验失败不修改原值。插件可声明 copy_on_read=True，使直接 get 同样返回副本。StateView.update 返回值也为副本，避免绕过后续校验。

核心管理接口保留直接 get/set/remove；这是可信插件的工程所有权约束，不是 Python 插件安全沙箱。默认数据列表使用副本读写。caps 仅保留旧调用方和旧执行日志兼容，审批等待表是宿主持有的运行时资源，继续使用引用；它们不代表任意原地写入都具有并发保护。

## 业务迁移与恢复（2026-10-15）

内置业务不再向 caps 写计划或沙箱会话，声明随插件启停注入/移除：

| 字段 | 归属 / 生命周期 | 值与恢复策略 |
| --- | --- | --- |
| plan.current | plan / task | Plan 或 None，副本读取，经 owner view 更新；codec 保存数据，执行检查点和已完成工具日志均恢复该字段 |
| shell.session | shell / request | SandboxSession 或 None，只有内存引用，无 codec，不进入检查点；子代理 fork 返回 None，按需新建 |

旧 caps.plan 和 caps.sandbox 在使用时一次性迁移并删除旧槽位，不双写。新任务重置插件 request/task 字段；同一执行恢复则在重置后加载执行检查点。会话组件文件只更新/恢复 session/application 字段，任务计划不会串入下一任务。已卸载字段的历史磁盘记录保留，不等于运行中实例仍存在。

ComponentStore.snapshot/restore_snapshot 复用版本、归属和类型校验；只导出有 codec 的字段，恢复先全部校验再提交。save/restore 支持 lifetime 过滤，文件保存保留原有冲突检测与原子替换。这里不承诺多个字段的资源清理具备事务回滚。

会话文件允许忽略未加载插件字段并保留其磁盘数据；执行恢复调用 strict=True，快照字段缺失声明或 codec 时拒绝恢复。字段版本、归属、生命周期、解码或类型不兼容，统一提升为 RecoveryRequired，终止循环；不作为普通 ToolResult.fail 返回模型。兼容旧日志没有字段清单时仍只能恢复其中实际保存的数据，不能验证历史上未记录的依赖。

AgentState 复制时隔离数据；资源可用 StateComponent.fork 声明子代理策略。审批等待表在子代理中为空；不能复制且未声明 fork 的资源会阻止派生，不退回共用父状态。子代理在 finally 中清理其临时沙箱。临时沙箱由请求结束/字段卸载清理；持久沙箱仍由宿主的工作区资源表持有，跨请求复用，在 shell 停用排空后或进程结束时关闭。

## 并发与默认工作区修正

ToolRegistry 用控制锁串行安装/启用/停用/卸载，用短时表锁协调 State 绑定、字段同步、工具表和能力快照。需要同时获取时顺序为控制锁→表锁→组件锁；业务 handler 在表锁外运行。等待插件排空时不持有表锁，活动调用仍可绑定子代理 State。调用入口在表锁内取得工具和插件租约，租约覆盖 handler、完成日志写入和结果恢复，防止 handler 返回后字段被提前卸载。不要在插件 handler 内同步停用自身或修改生命周期；控制操作应由宿主发起。

默认工作区在 run_agent 获取请求锁之前确定；请求锁、State.workspace_dir、组件加载、任务上下文与保存使用同一工作区值，避免空工作区和默认目录形成两个快照路径。

## 验证

2026-10-15 最终全量：472 passed / 1 skipped，8 条第三方依赖弃用警告；线程未处理异常按错误执行。回归覆盖嵌套 schema、不合法输入不执行 handler、禁止远程引用、owner 越权拒绝、读取副本、100 次并发原子更新以及归属不匹配恢复失败。迁移专项另覆盖旧计划消费、任务/会话隔离、工具结果与检查点恢复、父子状态与审批资源隔离、插件停用清理和子代理异常清理。边界修复专项覆盖恢复不兼容中止、缺失执行字段拒绝、Docker 删除失败/超时保留并重试、并发绑定/启停和默认工作区一致性。沙箱验证使用本地后端及模拟 Docker 返回值，不能替代真实 Docker daemon 的环境验收。


## 文件版本与上下文契约 v3（2026-10-16）

filesystem 注入 session 字段 filesystem.views，持久化会话/工作区/路径/版本下的已读覆盖。file_read 用行范围与 Unicode 字符列续读；file_edit/file_write 必须携带 expected_version，新建为 absent，已有文件修改须验证版本及实际已读范围。工具提供 context_probe、context_delivery 与 defer_context 声明，harness 负责调用、过期通知与水位批量折叠。Kernel 无具体文件策略。此节替代旧版“每次编辑前重读”和立即收缩历史读取的约定，详细见 [文件上下文契约](TOOL_CONTEXT_AND_EDIT_RECEIPTS.md)。
